# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Web Pod 会话历史采集与查询（release 旧架构版：http.server + websockets）。

职责：
- ``ChatHistoryStore``：aiosqlite + WAL，落 sessions / messages 两表，幂等去重（由 ws loop 写）。
- ``make_history_callback(store)``：产出 ``EnterpriseWebWsServer.on_frame`` 回调——白名单过滤 +
  pending（首条请求无 session_id 时暂存、final 回填）+ 调 store 落盘。
- ``list_sessions_sync`` / ``get_session_detail_sync``：标准库 sqlite3 同步只读，
  供 http.server 的 ``_SpaStaticHandler``（同步线程）调用；与 ws loop 的 aiosqlite 写共享同一 WAL db。

设计文档：``docs/zh/WebPodHistoryStorage.md``。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

import aiosqlite

logger = logging.getLogger("jiuwenclaw.history")

# 用户对话请求白名单（其余 method 不采集）。
_REQUEST_METHODS = frozenset({"chat.send", "chat.resume", "chat.user_answer"})
# 终态回复事件（流式增量 / 工具调用等中间事件不采集）。
_FINAL_EVENTS = frozenset({"chat.final", "chat.error"})

_TITLE_LEN = 30
_PREVIEW_LEN = 100
_MAX_LIST_LIMIT = 100

# on_frame 回调签名：(direction, raw, conn_id)
FrameCallback = Callable[[str, str, "str | None"], Awaitable[None]]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id     TEXT PRIMARY KEY,
    user           TEXT,
    title          TEXT,
    message_count  INTEGER DEFAULT 0,
    last_preview   TEXT,
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT NOT NULL,
    request_id    TEXT NOT NULL,
    role          TEXT NOT NULL,
    content       TEXT NOT NULL,
    event_type    TEXT,
    timestamp     REAL NOT NULL,
    UNIQUE(session_id, request_id, role)
);
CREATE INDEX IF NOT EXISTS idx_msg_session_ts ON messages(session_id, timestamp);
"""

_UPSERT_SESSION = """
INSERT INTO sessions (session_id, user, title, message_count, last_preview, created_at, updated_at)
VALUES (?, ?, ?, 1, ?, ?, ?)
ON CONFLICT(session_id) DO UPDATE SET
    message_count = message_count + 1,
    last_preview  = excluded.last_preview,
    updated_at    = excluded.updated_at
"""


class ChatHistoryStore:
    """会话历史 SQLite 存储（aiosqlite + WAL，懒初始化，幂等）。"""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None
        self._init_lock: Any = None  # 懒建：首次用时创建（避免 import 时无 event loop）

    async def _ensure(self) -> aiosqlite.Connection:
        if self._db is not None:
            return self._db
        import asyncio
        if self._init_lock is None:
            self._init_lock = asyncio.Lock()
        async with self._init_lock:
            if self._db is not None:
                return self._db
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = await aiosqlite.connect(str(self._db_path))
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA foreign_keys=ON")
            await conn.executescript(_SCHEMA)
            # 兼容旧库：sessions 无 user 列时补上（已有则忽略）
            try:
                await conn.execute("ALTER TABLE sessions ADD COLUMN user TEXT")
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise
                logger.debug("[history] sessions.user 列已存在，跳过 ALTER")
            # 迁移：多租户前的旧会话（user 为 NULL）归默认 guest
            await conn.execute("UPDATE sessions SET user = 'guest' WHERE user IS NULL")
            await conn.commit()
            self._db = conn
            logger.info("[history] store 初始化完成: db=%s", self._db_path)
        return self._db

    async def record_user(self, *, request_id: str, session_id: str, query: str, ts: float,
                          user: str | None = None) -> bool:
        """落盘一条 user 消息。重发幂等（UNIQUE 命中则不增计数）。user 写 sessions.user（首条定）。"""
        conn = await self._ensure()
        cur = await conn.execute(
            "INSERT OR IGNORE INTO messages (session_id, request_id, role, content, event_type, timestamp) "
            "VALUES (?, ?, 'user', ?, NULL, ?)",
            (session_id, request_id, query, ts),
        )
        inserted = cur.rowcount > 0
        if inserted:
            await conn.execute(
                _UPSERT_SESSION,
                (session_id, user, query[:_TITLE_LEN], query[:_PREVIEW_LEN], ts, ts),
            )
        await conn.commit()
        if inserted:
            logger.info("[history] 落盘 user: rid=%s sid=%s user=%s len=%d",
                        request_id, session_id, user, len(query))
        return inserted

    async def record_assistant(self, *, request_id: str, session_id: str, content: str,
                               event_type: str, ts: float) -> bool:
        """落盘一条 assistant 终态消息（chat.final / chat.error）。重发幂等。"""
        conn = await self._ensure()
        cur = await conn.execute(
            "INSERT OR IGNORE INTO messages (session_id, request_id, role, content, event_type, timestamp) "
            "VALUES (?, ?, 'assistant', ?, ?, ?)",
            (session_id, request_id, content, event_type, ts),
        )
        inserted = cur.rowcount > 0
        if inserted:
            await conn.execute(
                _UPSERT_SESSION,
                (session_id, None, None, content[:_PREVIEW_LEN], ts, ts),
            )
        await conn.commit()
        if inserted:
            logger.info("[history] 落盘 assistant: rid=%s sid=%s event=%s len=%d",
                        request_id, session_id, event_type, len(content))
        return inserted

    async def list_sessions(self, *, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        conn = await self._ensure()
        limit = max(1, min(limit, _MAX_LIST_LIMIT))
        offset = max(0, offset)
        cur = await conn.execute(
            "SELECT session_id, title, message_count, last_preview, created_at, updated_at "
            "FROM sessions ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        rows = await cur.fetchall()
        return [{"session_id": r[0], "title": r[1], "message_count": r[2],
                 "last_preview": r[3], "created_at": r[4], "updated_at": r[5]} for r in rows]

    async def get_session_detail(self, session_id: str) -> dict[str, Any] | None:
        conn = await self._ensure()
        cur = await conn.execute(
            "SELECT session_id, title, message_count, last_preview, created_at, updated_at "
            "FROM sessions WHERE session_id = ?", (session_id,),
        )
        s = await cur.fetchone()
        if s is None:
            return None
        cur = await conn.execute(
            "SELECT role, content, event_type, timestamp, request_id "
            "FROM messages WHERE session_id = ? ORDER BY timestamp ASC", (session_id,),
        )
        msgs = await cur.fetchall()
        return {
            "session_id": s[0], "title": s[1], "message_count": s[2],
            "last_preview": s[3], "created_at": s[4], "updated_at": s[5],
            "messages": [{"role": m[0], "content": m[1], "event_type": m[2],
                          "timestamp": m[3], "request_id": m[4]} for m in msgs],
        }

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None
            logger.info("[history] store 已关闭: db=%s", self._db_path)


def make_history_callback(store: ChatHistoryStore) -> FrameCallback:
    """产出 on_frame 回调：白名单 → pending 回填 → store.record_*。

    闭包内持有 ``pending: {request_id → {query, ts, method}}``，跨帧保持。
    回调内部 catch 所有异常，绝不冒泡到中继（broker 侧另有一层兜底）。
    """
    pending: dict[str, dict[str, Any]] = {}
    # 流式回复累积：request_id -> 拼接后的 assistant 文本（chat.delta 逐帧累积，chat.final 落盘）
    assistant_buf: dict[str, str] = {}

    async def _handle_browser(data: dict[str, Any]) -> None:
        if data.get("type") != "req":
            return
        method = data.get("method")
        if method not in _REQUEST_METHODS:
            return
        params = data.get("params") if isinstance(data.get("params"), dict) else {}
        query = params.get("query") or params.get("content")
        if not isinstance(query, str) or not query:
            return
        request_id = data.get("id")
        if not isinstance(request_id, str):
            return
        session_id = params.get("session_id")
        user = params.get("user")
        if not isinstance(user, str):
            user = None
        ts = time.time()
        if isinstance(session_id, str) and session_id:
            await store.record_user(request_id=request_id, session_id=session_id, query=query, ts=ts, user=user)
        else:
            pending[request_id] = {"query": query, "ts": ts, "method": method, "user": user}
            logger.debug("[history] 暂存 pending user(无 sid): rid=%s method=%s pending=%d",
                         request_id, method, len(pending))

    async def _handle_uplink(data: dict[str, Any]) -> None:
        if data.get("type") != "event":
            return
        event = data.get("event")
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        request_id = data.get("request_id")

        # 流式增量：累积 chat.delta（最终回复文本在 delta 里逐帧推送；chat.final 只是终止信号、content 为空）
        if event == "chat.delta" and isinstance(request_id, str):
            delta = payload.get("content")
            if isinstance(delta, str) and delta:
                assistant_buf[request_id] = assistant_buf.get(request_id, "") + delta
            return

        if event not in _FINAL_EVENTS:
            return
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            logger.warning("[history] 终态帧缺 session_id，丢弃: event=%s rid=%s",
                           event, request_id if isinstance(request_id, str) else "")
            return
        if event == "chat.final":
            # 流式：取累积的 delta；非流式兜底：取 payload.content
            content = assistant_buf.pop(request_id, "") or payload.get("content") or ""
        else:  # chat.error
            content = payload.get("error") or payload.get("content") or assistant_buf.pop(request_id, "")
        if not isinstance(content, str) or not content:
            logger.debug("[history] 终态帧无内容，丢弃: event=%s rid=%s", event, request_id)
            return
        ts = time.time()
        if isinstance(request_id, str) and request_id in pending:
            p = pending.pop(request_id)
            await store.record_user(
                request_id=request_id, session_id=session_id,
                query=p["query"], ts=p["ts"], user=p.get("user"),
            )
            logger.info("[history] pending 回填 user: rid=%s sid=%s", request_id, session_id)
        await store.record_assistant(
            request_id=request_id if isinstance(request_id, str) else "",
            session_id=session_id, content=content, event_type=event, ts=ts,
        )

    async def cb(direction: str, raw: str, conn_id: str | None = None) -> None:  # noqa: ARG001
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("[history] 非 JSON 帧，忽略: dir=%s head=%r", direction, (raw or "")[:120])
            return
        if not isinstance(data, dict):
            return
        try:
            if direction == "browser":
                await _handle_browser(data)
            elif direction == "uplink":
                await _handle_uplink(data)
        except Exception:
            logger.exception("[history] on_frame 处理失败: dir=%s", direction)

    return cb


# ---- 同步只读（供 http.server 的 _SpaStaticHandler 调用）----

def _open_readonly(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def list_sessions_sync(db_path: str | Path, *, limit: int = 20, offset: int = 0,
                       user: str | None = None) -> list[dict[str, Any]]:
    """同步读会话列表（http.server 线程用）。db 不存在或无表返回空。user 非空时按 user 过滤。"""
    if not Path(db_path).exists():
        return []
    limit = max(1, min(limit, _MAX_LIST_LIMIT))
    offset = max(0, offset)
    conn = _open_readonly(db_path)
    try:
        if user:
            rows = conn.execute(
                "SELECT session_id, user, title, message_count, last_preview, created_at, updated_at "
                "FROM sessions WHERE user = ? ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (user, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT session_id, user, title, message_count, last_preview, created_at, updated_at "
                "FROM sessions ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def get_session_detail_sync(db_path: str | Path, session_id: str, *,
                            user: str | None = None) -> dict[str, Any] | None:
    """同步读会话详情。db 不存在 / 无表 / 会话不存在均返回 None。user 非空时校验归属。"""
    if not Path(db_path).exists():
        return None
    conn = _open_readonly(db_path)
    try:
        where = "WHERE session_id = ?" + (" AND user = ?" if user else "")
        params: tuple = (session_id, user) if user else (session_id,)
        s = conn.execute(
            "SELECT session_id, user, title, message_count, last_preview, created_at, updated_at "
            f"FROM sessions {where}",
            params,
        ).fetchone()
        if s is None:
            return None
        msgs = conn.execute(
            "SELECT role, content, event_type, timestamp, request_id "
            "FROM messages WHERE session_id = ? ORDER BY timestamp ASC", (session_id,),
        ).fetchall()
        return {
            "session_id": s["session_id"], "user": s["user"], "title": s["title"],
            "message_count": s["message_count"], "last_preview": s["last_preview"],
            "created_at": s["created_at"], "updated_at": s["updated_at"],
            "messages": [dict(m) for m in msgs],
        }
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
