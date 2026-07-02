# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Web Pod 会话历史采集与查询（FastAPI 版）。

职责：
- ``ChatHistoryStore``：aiosqlite + WAL，落 sessions / messages 两表，幂等去重。
- ``make_history_callback(store)``：产出 ``broker.on_frame`` 回调——白名单过滤 +
  pending（首条请求无 session_id 时暂存、final 回填）+ 调 store 落盘。
- ``build_history_router(rt)``：``GET /api/sessions`` 列表、``GET /api/sessions/{id}`` 详情。

设计文档：``docs/zh/WebPodHistoryStorage.md``。
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

import aiosqlite
from fastapi import APIRouter
from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    # 仅类型注解用；运行时不 import，避免拉起 webserver.common 的重依赖链（便于单测独立运行）。
    from jiuwenclaw.webserver.common import WebRuntime

logger = logging.getLogger("jiuwenclaw.webserver.history")

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
INSERT INTO sessions (session_id, title, message_count, last_preview, created_at, updated_at)
VALUES (?, ?, 1, ?, ?, ?)
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
            await conn.commit()
            self._db = conn
            logger.info("[history] store 初始化完成: db=%s", self._db_path)
        return self._db

    async def record_user(
        self, *, request_id: str, session_id: str, query: str, ts: float
    ) -> bool:
        """落盘一条 user 消息。重发幂等（UNIQUE 命中则不增计数）。返回是否真正写入。"""
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
                (session_id, query[:_TITLE_LEN], query[:_PREVIEW_LEN], ts, ts),
            )
        await conn.commit()
        if inserted:
            logger.info(
                "[history] 落盘 user: rid=%s sid=%s len=%d",
                request_id, session_id, len(query),
            )
        return inserted

    async def record_assistant(
        self, *, request_id: str, session_id: str, content: str,
        event_type: str, ts: float
    ) -> bool:
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
                (session_id, None, content[:_PREVIEW_LEN], ts, ts),
            )
        await conn.commit()
        if inserted:
            logger.info(
                "[history] 落盘 assistant: rid=%s sid=%s event=%s len=%d",
                request_id, session_id, event_type, len(content),
            )
        return inserted

    async def list_sessions(self, *, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        """按 updated_at 倒序返回会话列表。"""
        conn = await self._ensure()
        limit = max(1, min(limit, _MAX_LIST_LIMIT))
        offset = max(0, offset)
        cur = await conn.execute(
            "SELECT session_id, title, message_count, last_preview, created_at, updated_at "
            "FROM sessions ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        rows = await cur.fetchall()
        logger.debug("[history] list_sessions: limit=%d offset=%d -> %d 条", limit, offset, len(rows))
        return [
            {
                "session_id": r[0], "title": r[1], "message_count": r[2],
                "last_preview": r[3], "created_at": r[4], "updated_at": r[5],
            }
            for r in rows
        ]

    async def get_session_detail(self, session_id: str) -> dict[str, Any] | None:
        """返回单个会话元数据 + messages（timestamp 升序）；不存在返回 None。"""
        conn = await self._ensure()
        cur = await conn.execute(
            "SELECT session_id, title, message_count, last_preview, created_at, updated_at "
            "FROM sessions WHERE session_id = ?",
            (session_id,),
        )
        s = await cur.fetchone()
        if s is None:
            return None
        cur = await conn.execute(
            "SELECT role, content, event_type, timestamp, request_id "
            "FROM messages WHERE session_id = ? ORDER BY timestamp ASC",
            (session_id,),
        )
        msgs = await cur.fetchall()
        return {
            "session_id": s[0], "title": s[1], "message_count": s[2],
            "last_preview": s[3], "created_at": s[4], "updated_at": s[5],
            "messages": [
                {
                    "role": m[0], "content": m[1], "event_type": m[2],
                    "timestamp": m[3], "request_id": m[4],
                }
                for m in msgs
            ],
        }

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None
            logger.info("[history] store 已关闭: db=%s", self._db_path)


def make_history_callback(store: ChatHistoryStore) -> FrameCallback:
    """产出 broker.on_frame 回调：白名单 → pending 回填 → store.record_*。

    闭包内持有 ``pending: {request_id → {query, ts, method}}``，跨帧保持。
    回调内部 catch 所有异常，绝不冒泡到中继（broker 侧另有一层兜底）。
    """
    pending: dict[str, dict[str, Any]] = {}

    async def _handle_browser(data: dict[str, Any]) -> None:
        if data.get("type") != "req":
            return
        method = data.get("method")
        if method not in _REQUEST_METHODS:
            return  # 白名单外（chat.interrupt / skilldev.* / 其他）—— 不采集
        params = data.get("params") if isinstance(data.get("params"), dict) else {}
        query = params.get("query") or params.get("content")
        if not isinstance(query, str) or not query:
            return
        request_id = data.get("id")
        if not isinstance(request_id, str):
            return
        session_id = params.get("session_id")
        ts = time.time()
        if isinstance(session_id, str) and session_id:
            await store.record_user(request_id=request_id, session_id=session_id, query=query, ts=ts)
        else:
            # 首条请求常无 session_id（由 AgentServer 生成、从回复回来）—— 暂存等回填。
            pending[request_id] = {"query": query, "ts": ts, "method": method}
            logger.debug(
                "[history] 暂存 pending user(无 sid): rid=%s method=%s pending=%d",
                request_id, method, len(pending),
            )

    async def _handle_uplink(data: dict[str, Any]) -> None:
        if data.get("type") != "event":
            # 对话流回复走 event；type=res 是非 chat 请求-响应（无对应 user），不采集。
            return
        event = data.get("event")
        if event not in _FINAL_EVENTS:
            return  # 中间事件（*.delta / tool_call / processing_status 等）—— 不采集
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        session_id = payload.get("session_id")
        request_id = data.get("request_id")
        if not isinstance(session_id, str) or not session_id:
            logger.warning(
                "[history] 终态帧缺 session_id，丢弃: event=%s rid=%s",
                event, request_id if isinstance(request_id, str) else "",
            )
            return
        if event == "chat.final":
            content = payload.get("content")
        else:  # chat.error
            content = payload.get("error") or payload.get("content")
        if not isinstance(content, str) or not content:
            logger.debug("[history] 终态帧无内容，丢弃: event=%s rid=%s", event, request_id)
            return
        ts = time.time()
        # pending 回填：若该 request_id 曾因无 sid 暂存 user，现在用 final 带回的 sid 补落。
        if isinstance(request_id, str) and request_id in pending:
            p = pending.pop(request_id)
            await store.record_user(
                request_id=request_id, session_id=session_id, query=p["query"], ts=p["ts"]
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


def build_history_router(rt: "WebRuntime") -> APIRouter:
    """构造历史查询路由：GET /api/sessions、GET /api/sessions/{session_id}。

    从 ``rt.history_store`` 取存储；未配置时返回 503。
    """
    router = APIRouter()

    @router.get("/api/sessions")
    async def list_sessions(limit: int = 20, offset: int = 0) -> JSONResponse:
        store = rt.history_store
        if store is None:
            return JSONResponse({"error": "history_disabled"}, status_code=503)
        try:
            rows = await store.list_sessions(limit=limit, offset=offset)
            return JSONResponse({"sessions": rows})
        except Exception:
            logger.exception("[history] list_sessions 失败")
            return JSONResponse({"error": "internal"}, status_code=500)

    @router.get("/api/sessions/{session_id}")
    async def get_session(session_id: str) -> JSONResponse:
        store = rt.history_store
        if store is None:
            return JSONResponse({"error": "history_disabled"}, status_code=503)
        try:
            detail = await store.get_session_detail(session_id)
            if detail is None:
                return JSONResponse({"error": "not_found"}, status_code=404)
            return JSONResponse(detail)
        except Exception:
            logger.exception("[history] get_session_detail 失败: sid=%s", session_id)
            return JSONResponse({"error": "internal"}, status_code=500)

    return router
