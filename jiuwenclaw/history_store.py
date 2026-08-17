# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Web 会话历史采集与查询（release 旧架构版：http.server + websockets）。

职责：
- ``ChatHistoryStore``：按数据库类型分支存储 sessions / messages，幂等去重（由 ws loop 写）。
  - ``sqlite``：本机 SQLite 文件（``WEB_HISTORY_SQLITE_PATH`` 可覆盖默认路径）
  - ``mysql``：MySQL 独立库 ``web``（``WEB_DB_*``）；缺 ``WEB_DB_HOST`` 则不可用，不回退 SQLite
  - ``postgresql`` 等：暂不支持，明确不可用（不静默回退 SQLite）
- 选型与 Gateway 的 ``GATEWAY_DB_TYPE`` 对称：``WEB_DB_TYPE`` → ``DB_TYPE`` →
  （已配 ``WEB_DB_HOST`` 则视为 mysql）→ 默认 ``sqlite``。与 ``DEPLOYMENT_MODE`` 无关。
- ``make_history_callback(store)``：产出 ``EnterpriseWebWsServer.on_frame`` 回调——白名单过滤 +
  pending（首条请求无 session_id 时暂存、final 回填）+ 调 store 落盘。
- ``list_sessions_sync`` / ``get_session_detail_sync``：同步只读，供 http.server 线程调用。

写失败 / 库不可用时不阻断聊天（fail-soft）。
异步路径（``record_*`` / ``list_sessions`` / ``get_session_detail``）经 ``asyncio.to_thread``
执行同步 DB I/O，避免 MySQL 卡住时堵住 WebSocket 事件循环。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

logger = logging.getLogger("jiuwenclaw.history")

_REQUEST_METHODS = frozenset({"chat.send", "chat.resume", "chat.user_answer"})
_FINAL_EVENTS = frozenset({"chat.final", "chat.error"})

_TITLE_LEN = 30
_PREVIEW_LEN = 100
_MAX_LIST_LIMIT = 100

FrameCallback = Callable[[str, str, "str | None"], Awaitable[None]]
HistoryBackend = Literal["memory", "sqlite", "mysql"]

_DEFAULT_DB_NAME = "web"
_DEFAULT_SQLITE_NAME = "web_history.db"

_CREATE_DATABASE_SQL = (
    "CREATE DATABASE IF NOT EXISTS `{db}` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
)

_MYSQL_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id     VARCHAR(128) NOT NULL,
    user           VARCHAR(128) NOT NULL DEFAULT 'guest',
    title          VARCHAR(255) NULL,
    message_count  INT NOT NULL DEFAULT 0,
    last_preview   TEXT NULL,
    created_at     DOUBLE NOT NULL,
    updated_at     DOUBLE NOT NULL,
    PRIMARY KEY (session_id),
    KEY idx_sessions_user_updated (user, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS messages (
    id            BIGINT NOT NULL AUTO_INCREMENT,
    session_id    VARCHAR(128) NOT NULL,
    request_id    VARCHAR(128) NOT NULL,
    role          VARCHAR(32) NOT NULL,
    content       MEDIUMTEXT NOT NULL,
    event_type    VARCHAR(64) NULL,
    timestamp     DOUBLE NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_msg_sid_rid_role (session_id, request_id, role),
    KEY idx_msg_session_ts (session_id, timestamp)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

_MYSQL_INSERT_MESSAGE_SQL = (
    "INSERT IGNORE INTO messages (session_id, request_id, role, content, event_type, timestamp) "
    "VALUES (%s, %s, %s, %s, %s, %s)"
)

_MYSQL_UPSERT_SESSION_SQL = """
INSERT INTO sessions (session_id, user, title, message_count, last_preview, created_at, updated_at)
VALUES (%s, %s, %s, 1, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    message_count = message_count + 1,
    last_preview  = VALUES(last_preview),
    updated_at    = VALUES(updated_at),
    title         = COALESCE(title, VALUES(title))
"""

_SQLITE_SCHEMA = """
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

_SQLITE_INSERT_MESSAGE_SQL = (
    "INSERT OR IGNORE INTO messages (session_id, request_id, role, content, event_type, timestamp) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)

_SQLITE_UPSERT_SESSION_SQL = """
INSERT INTO sessions (session_id, user, title, message_count, last_preview, created_at, updated_at)
VALUES (?, ?, ?, 1, ?, ?, ?)
ON CONFLICT(session_id) DO UPDATE SET
    message_count = message_count + 1,
    last_preview  = excluded.last_preview,
    updated_at    = excluded.updated_at,
    title         = COALESCE(sessions.title, excluded.title)
"""


def _env(*names: str, default: str = "") -> str:
    for name in names:
        raw = os.getenv(name)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    return default


def resolve_history_db_type() -> str:
    """解析 Web 会话历史库类型（与 Gateway ``GATEWAY_DB_TYPE`` 对称）。

    优先级：
    1. ``WEB_DB_TYPE``
    2. 总开关 ``DB_TYPE``
    3. 未显式指定类型但已配置 ``WEB_DB_HOST`` → ``mysql``（兼容企业仅注入 HOST）
    4. 默认 ``sqlite``
    """
    explicit = _env("WEB_DB_TYPE") or _env("DB_TYPE")
    if explicit:
        return explicit.strip().lower()
    if _env("WEB_DB_HOST"):
        return "mysql"
    return "sqlite"


def default_history_sqlite_path() -> Path:
    """SQLite 历史默认路径：``workspace_default/web_history.db``（可被环境变量覆盖）。"""
    override = _env("WEB_HISTORY_SQLITE_PATH")
    if override:
        return Path(override).expanduser().resolve()
    from jiuwenclaw.utils import get_multi_tenant_user_workspace_dir

    return get_multi_tenant_user_workspace_dir(workspace_key="default") / _DEFAULT_SQLITE_NAME


@dataclass(frozen=True)
class WebHistoryDbSettings:
    """Web 历史库 MySQL 连接（独立 database ``web``）。"""

    host: str
    port: int
    user: str
    password: str
    database: str = _DEFAULT_DB_NAME

    @classmethod
    def from_env(cls) -> WebHistoryDbSettings | None:
        host = _env("WEB_DB_HOST")
        if not host:
            return None
        port_raw = _env("WEB_DB_PORT", default="3306")
        try:
            port = int(port_raw)
        except ValueError:
            port = 3306
        return cls(
            host=host,
            port=port,
            user=_env("WEB_DB_USER", default="root"),
            password=_env("WEB_DB_PASSWORD"),
            database=_env("WEB_DB_NAME", default=_DEFAULT_DB_NAME) or _DEFAULT_DB_NAME,
        )


def _quote_ident(name: str) -> str:
    cleaned = "".join(ch for ch in name if ch.isalnum() or ch in {"_", "$"})
    return cleaned or _DEFAULT_DB_NAME


class ChatHistoryStore:
    """会话历史存储：SQLite / MySQL；单测可用 memory=True。"""

    def __init__(
        self,
        settings: WebHistoryDbSettings | None = None,
        *,
        db_path: str | Path | None = None,
        memory: bool = False,
    ) -> None:
        self._settings = settings
        self._db_path = Path(db_path) if db_path is not None else None
        self._memory = memory
        self._ready = False
        self._init_lock = threading.Lock()
        self._mem_lock = threading.Lock()
        self._mem_sessions: dict[str, dict[str, Any]] = {}
        self._mem_messages: list[dict[str, Any]] = []

    @classmethod
    def for_db_type(cls, db_type: str) -> ChatHistoryStore:
        """按库类型构造：sqlite → 本机文件；mysql → WEB_DB_*（缺 HOST 则不可用，不回退）。"""
        normalized = str(db_type or "").strip().lower() or "sqlite"
        if normalized in ("postgresql", "postgres", "pg"):
            logger.warning(
                "[history] Web 会话历史暂不支持 PostgreSQL（db_type=%s），"
                "会话历史不可用（不回退 SQLite）",
                normalized,
            )
            return cls(settings=None, memory=False)
        if normalized == "mysql":
            return cls(settings=WebHistoryDbSettings.from_env(), memory=False)
        if normalized == "sqlite":
            return cls(db_path=default_history_sqlite_path(), memory=False)
        logger.warning(
            "[history] 不支持的历史库类型 %r，会话历史不可用（不回退 SQLite）",
            normalized,
        )
        return cls(settings=None, memory=False)

    @classmethod
    def from_env(cls) -> ChatHistoryStore:
        return cls.for_db_type(resolve_history_db_type())

    @classmethod
    def memory(cls) -> ChatHistoryStore:
        return cls(settings=None, db_path=None, memory=True)

    @property
    def backend(self) -> HistoryBackend:
        if self._memory:
            return "memory"
        if self._db_path is not None:
            return "sqlite"
        return "mysql"

    @property
    def db_path(self) -> Path | None:
        return self._db_path

    @property
    def mysql_settings(self) -> WebHistoryDbSettings | None:
        """公开只读：MySQL 连接配置（供日志/诊断；勿在类外读 ``_settings``）。"""
        return self._settings

    @property
    def available(self) -> bool:
        if self._memory:
            return True
        if self._db_path is not None:
            return True
        return self._settings is not None

    def _connect_mysql(self, *, with_database: bool):
        import pymysql

        if self._settings is None:
            raise RuntimeError("MySQL settings 未配置，无法建立连接")
        kwargs: dict[str, Any] = {
            "host": self._settings.host,
            "port": self._settings.port,
            "user": self._settings.user,
            "password": self._settings.password,
            "charset": "utf8mb4",
            "autocommit": False,
            "cursorclass": pymysql.cursors.DictCursor,
        }
        if with_database:
            kwargs["database"] = self._settings.database
        return pymysql.connect(**kwargs)

    def _connect_sqlite(self) -> sqlite3.Connection:
        if self._db_path is None:
            raise RuntimeError("SQLite db_path 未配置，无法建立连接")
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> bool:
        if self._memory:
            return True
        if self.backend == "sqlite":
            return self._ensure_sqlite_schema()
        return self._ensure_mysql_schema()

    def _ensure_sqlite_schema(self) -> bool:
        if self._db_path is None:
            logger.error("[history] SQLite 未配置 db_path，会话历史不可用")
            return False
        if self._ready:
            return True
        with self._init_lock:
            if self._ready:
                return True
            try:
                self._db_path.parent.mkdir(parents=True, exist_ok=True)
                conn = self._connect_sqlite()
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA foreign_keys=ON")
                    conn.executescript(_SQLITE_SCHEMA)
                    try:
                        conn.execute("ALTER TABLE sessions ADD COLUMN user TEXT")
                    except sqlite3.OperationalError as e:
                        if "duplicate column" not in str(e).lower():
                            raise
                        logger.debug("[history] sessions.user 列已存在，跳过 ALTER")
                    conn.execute("UPDATE sessions SET user = 'guest' WHERE user IS NULL")
                    conn.commit()
                finally:
                    conn.close()
                self._ready = True
                logger.info("[history] SQLite store 初始化完成: db=%s", self._db_path)
                return True
            except Exception:
                logger.exception("[history] SQLite 初始化失败，会话历史暂不可用")
                return False

    def _ensure_mysql_schema(self) -> bool:
        if self._settings is None:
            logger.error("[history] MySQL 未配置（缺少 WEB_DB_HOST），会话历史不可用")
            return False
        if self._ready:
            return True
        with self._init_lock:
            if self._ready:
                return True
            db_name = _quote_ident(self._settings.database)
            try:
                conn = self._connect_mysql(with_database=False)
                try:
                    with conn.cursor() as cur:
                        cur.execute(_CREATE_DATABASE_SQL.format(db=db_name))
                    conn.commit()
                finally:
                    conn.close()
                conn = self._connect_mysql(with_database=True)
                try:
                    with conn.cursor() as cur:
                        for stmt in _MYSQL_CREATE_TABLES_SQL.split(";"):
                            sql = stmt.strip()
                            if sql:
                                cur.execute(sql)
                    conn.commit()
                finally:
                    conn.close()
                self._ready = True
                logger.info(
                    "[history] MySQL store 初始化完成: %s:%s/%s",
                    self._settings.host,
                    self._settings.port,
                    self._settings.database,
                )
                return True
            except Exception:
                logger.exception("[history] MySQL 初始化失败，会话历史暂不可用")
                return False

    def _run_write(self, fn: Callable[[Any], bool]) -> bool:
        if self._memory:
            return fn(None)
        if not self._ensure_schema():
            return False
        try:
            if self.backend == "sqlite":
                conn = self._connect_sqlite()
                try:
                    inserted = fn(conn)
                    conn.commit()
                    return inserted
                finally:
                    conn.close()
            conn = self._connect_mysql(with_database=True)
            try:
                inserted = fn(conn)
                conn.commit()
                return inserted
            finally:
                conn.close()
        except Exception:
            logger.exception("[history] %s 写入失败", self.backend.upper())
            return False

    async def _run_write_async(self, fn: Callable[[Any], bool]) -> bool:
        """把同步 DB I/O 丢到线程池，避免堵住 WebSocket 事件循环。"""
        return await asyncio.to_thread(self._run_write, fn)

    def _insert_message_and_upsert_session(
        self,
        conn: Any,
        *,
        session_id: str,
        request_id: str,
        role: str,
        content: str,
        event_type: str | None,
        ts: float,
        user: str | None,
        title: str | None,
        preview: str,
    ) -> bool:
        if self._memory:
            key = (session_id, request_id, role)
            with self._mem_lock:
                if any(
                    (m["session_id"], m["request_id"], m["role"]) == key
                    for m in self._mem_messages
                ):
                    return False
                self._mem_messages.append({
                    "session_id": session_id,
                    "request_id": request_id,
                    "role": role,
                    "content": content,
                    "event_type": event_type,
                    "timestamp": ts,
                })
                existing = self._mem_sessions.get(session_id)
                if existing is None:
                    self._mem_sessions[session_id] = {
                        "session_id": session_id,
                        "user": user or "guest",
                        "title": title,
                        "message_count": 1,
                        "last_preview": preview,
                        "created_at": ts,
                        "updated_at": ts,
                    }
                else:
                    existing["message_count"] = int(existing.get("message_count") or 0) + 1
                    existing["last_preview"] = preview
                    existing["updated_at"] = ts
                    if not existing.get("title") and title:
                        existing["title"] = title
            return True

        if self.backend == "sqlite":
            cur = conn.execute(
                _SQLITE_INSERT_MESSAGE_SQL,
                (session_id, request_id, role, content, event_type, ts),
            )
            inserted = cur.rowcount > 0
            if inserted:
                conn.execute(
                    _SQLITE_UPSERT_SESSION_SQL,
                    (session_id, user or "guest", title, preview, ts, ts),
                )
            return inserted

        with conn.cursor() as cur:
            cur.execute(
                _MYSQL_INSERT_MESSAGE_SQL,
                (session_id, request_id, role, content, event_type, ts),
            )
            inserted = cur.rowcount > 0
            if inserted:
                cur.execute(
                    _MYSQL_UPSERT_SESSION_SQL,
                    (session_id, user or "guest", title, preview, ts, ts),
                )
        return inserted

    async def record_user(
        self,
        *,
        request_id: str,
        session_id: str,
        query: str,
        ts: float,
        user: str | None = None,
    ) -> bool:
        if not user:
            user = "guest"
        title = query[:_TITLE_LEN]
        preview = query[:_PREVIEW_LEN]
        inserted = await self._run_write_async(
            lambda conn: self._insert_message_and_upsert_session(
                conn,
                session_id=session_id,
                request_id=request_id,
                role="user",
                content=query,
                event_type=None,
                ts=ts,
                user=user,
                title=title,
                preview=preview,
            )
        )
        if inserted:
            logger.info(
                "[history] 落盘 user: rid=%s sid=%s user=%s len=%d",
                request_id, session_id, user, len(query),
            )
        return inserted

    async def record_assistant(
        self,
        *,
        request_id: str,
        session_id: str,
        content: str,
        event_type: str,
        ts: float,
    ) -> bool:
        preview = content[:_PREVIEW_LEN]
        inserted = await self._run_write_async(
            lambda conn: self._insert_message_and_upsert_session(
                conn,
                session_id=session_id,
                request_id=request_id,
                role="assistant",
                content=content,
                event_type=event_type,
                ts=ts,
                user="guest",
                title=None,
                preview=preview,
            )
        )
        if inserted:
            logger.info(
                "[history] 落盘 assistant: rid=%s sid=%s event=%s len=%d",
                request_id, session_id, event_type, len(content),
            )
        return inserted

    def list_sessions_blocking(
        self, *, limit: int, offset: int, user: str | None,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(limit, _MAX_LIST_LIMIT))
        offset = max(0, offset)
        if not user:
            user = "guest"
        if self._memory:
            with self._mem_lock:
                rows = [
                    dict(s) for s in self._mem_sessions.values()
                    if s.get("user") == user
                ]
            rows.sort(key=lambda r: float(r.get("updated_at") or 0), reverse=True)
            return rows[offset:offset + limit]
        if not self._ensure_schema():
            return []
        if self.backend == "sqlite":
            try:
                conn = self._connect_sqlite()
                try:
                    rows = conn.execute(
                        "SELECT session_id, user, title, message_count, last_preview, created_at, updated_at "
                        "FROM sessions WHERE user = ? ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                        (user, limit, offset),
                    ).fetchall()
                    return [dict(r) for r in rows]
                finally:
                    conn.close()
            except Exception:
                logger.exception("[history] SQLite 读取会话列表失败")
                return []
        try:
            conn = self._connect_mysql(with_database=True)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT session_id, user, title, message_count, last_preview, created_at, updated_at "
                        "FROM sessions WHERE user = %s ORDER BY updated_at DESC LIMIT %s OFFSET %s",
                        (user, limit, offset),
                    )
                    return list(cur.fetchall() or [])
            finally:
                conn.close()
        except Exception:
            logger.exception("[history] MySQL 读取会话列表失败")
            return []

    def get_session_detail_blocking(
        self, session_id: str, *, user: str | None,
    ) -> dict[str, Any] | None:
        if not user:
            user = "guest"
        if self._memory:
            with self._mem_lock:
                s = self._mem_sessions.get(session_id)
                if s is None or s.get("user") != user:
                    return None
                msgs = [
                    {
                        "role": m["role"],
                        "content": m["content"],
                        "event_type": m["event_type"],
                        "timestamp": m["timestamp"],
                        "request_id": m["request_id"],
                    }
                    for m in self._mem_messages
                    if m["session_id"] == session_id
                ]
            msgs.sort(key=lambda m: float(m.get("timestamp") or 0))
            return {**s, "messages": msgs}
        if not self._ensure_schema():
            return None
        if self.backend == "sqlite":
            try:
                conn = self._connect_sqlite()
                try:
                    s = conn.execute(
                        "SELECT session_id, user, title, message_count, last_preview, created_at, updated_at "
                        "FROM sessions WHERE session_id = ? AND user = ?",
                        (session_id, user),
                    ).fetchone()
                    if s is None:
                        return None
                    msgs = conn.execute(
                        "SELECT role, content, event_type, timestamp, request_id "
                        "FROM messages WHERE session_id = ? ORDER BY timestamp ASC",
                        (session_id,),
                    ).fetchall()
                    return {**dict(s), "messages": [dict(m) for m in msgs]}
                finally:
                    conn.close()
            except Exception:
                logger.exception("[history] SQLite 读取会话详情失败")
                return None
        try:
            conn = self._connect_mysql(with_database=True)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT session_id, user, title, message_count, last_preview, created_at, updated_at "
                        "FROM sessions WHERE session_id = %s AND user = %s",
                        (session_id, user),
                    )
                    s = cur.fetchone()
                    if s is None:
                        return None
                    cur.execute(
                        "SELECT role, content, event_type, timestamp, request_id "
                        "FROM messages WHERE session_id = %s ORDER BY timestamp ASC",
                        (session_id,),
                    )
                    msgs = list(cur.fetchall() or [])
                    return {**s, "messages": msgs}
            finally:
                conn.close()
        except Exception:
            logger.exception("[history] MySQL 读取会话详情失败")
            return None

    async def list_sessions(self, *, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self.list_sessions_blocking, limit=limit, offset=offset, user=None,
        )

    async def get_session_detail(self, session_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self.get_session_detail_blocking, session_id, user=None,
        )

    async def close(self) -> None:
        self._ready = False
        logger.info("[history] store 已关闭 backend=%s", self.backend)


def make_history_callback(store: ChatHistoryStore) -> FrameCallback:
    """产出 on_frame 回调：白名单 → pending 回填 → store.record_*。

    闭包内持有 ``pending: {request_id → {query, ts, method}}``，跨帧保持。
    回调内部 catch 所有异常，绝不冒泡到中继（broker 侧另有一层兜底）。
    """
    pending: dict[str, dict[str, Any]] = {}
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
        user = params.get("user") or params.get("user_id")
        if not isinstance(user, str) or not user.strip():
            user = None
        else:
            user = user.strip()
        ts = time.time()
        if isinstance(session_id, str) and session_id:
            await store.record_user(
                request_id=request_id, session_id=session_id, query=query, ts=ts, user=user,
            )
        else:
            pending[request_id] = {"query": query, "ts": ts, "method": method, "user": user}
            logger.debug(
                "[history] 暂存 pending user(无 sid): rid=%s method=%s pending=%d",
                request_id, method, len(pending),
            )

    async def _handle_uplink(data: dict[str, Any]) -> None:
        if data.get("type") != "event":
            return
        event = data.get("event")
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        request_id = data.get("request_id")

        if event == "chat.delta" and isinstance(request_id, str):
            delta = payload.get("content")
            if isinstance(delta, str) and delta:
                assistant_buf[request_id] = assistant_buf.get(request_id, "") + delta
            return

        if event not in _FINAL_EVENTS:
            return
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            logger.warning(
                "[history] 终态帧缺 session_id，丢弃: event=%s rid=%s",
                event, request_id if isinstance(request_id, str) else "",
            )
            return
        if event == "chat.final":
            content = assistant_buf.pop(request_id, "") or payload.get("content") or ""
        else:
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


_default_store: ChatHistoryStore | None = None
_default_store_lock = threading.Lock()


def get_default_store() -> ChatHistoryStore:
    """进程内单例，避免 HTTP 每次请求重复建库/建表。"""
    global _default_store
    with _default_store_lock:
        if _default_store is None:
            _default_store = ChatHistoryStore.from_env()
        return _default_store


def set_default_store(store: ChatHistoryStore) -> None:
    global _default_store
    with _default_store_lock:
        _default_store = store


def list_sessions_sync(
    store: ChatHistoryStore | None = None,
    *,
    limit: int = 20,
    offset: int = 0,
    user: str | None = None,
) -> list[dict[str, Any]]:
    """同步读会话列表（http.server 线程用）。库不可用返回空。按 user 过滤（空归一为 guest）。"""
    st = store if store is not None else get_default_store()
    if not st.available:
        return []
    return st.list_sessions_blocking(limit=limit, offset=offset, user=user)


def get_session_detail_sync(
    session_id: str,
    store: ChatHistoryStore | None = None,
    *,
    user: str | None = None,
) -> dict[str, Any] | None:
    """同步读会话详情。库不可用 / 会话不存在返回 None。校验 user 归属。"""
    if not session_id:
        return None
    st = store if store is not None else get_default_store()
    if not st.available:
        return None
    return st.get_session_detail_blocking(session_id, user=user)
