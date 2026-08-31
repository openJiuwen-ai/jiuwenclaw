# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved

"""会话历史存储：个人版纯内存；企业版 mysql/pg 经 foundation DB-actor。

写路径可来自：
- ``app_web`` WS 反代（``HistoryFrameRunner``）
- Gateway ``WebChannel`` Listen

存储后端：
- ``memory``（个人版）：进程内内存，不落库、不引 foundation。
- ``mysql``/``postgresql``（企业版）：经 foundation 高层 CRUD
  (``list_records``/``get``/``create``/``update``)，统一在 ``HistoryDbActor`` 专用 loop
  内执行（asyncpg/aiomysql engine 绑 event loop，而本库被多 loop/线程消费——写经
  ``HistoryFrameRunner`` 独立 loop、读经同步 HTTP 线程）。

sqlite 已废弃（两端均不支持）。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Literal

from .settings import WebHistoryDbSettings, resolve_history_db_type

logger = logging.getLogger("jiuwenswarm.web.history")

_TITLE_LEN = 30
_PREVIEW_LEN = 100
_MAX_LIST_LIMIT = 100

HistoryBackend = Literal["memory", "mysql", "postgresql"]


class ChatHistoryStore:
    """会话历史存储：memory（个人版）/ mysql·pg（企业版）。"""

    def __init__(
        self,
        settings: WebHistoryDbSettings | None = None,
        *,
        memory: bool = False,
        db_type: str | None = None,
    ) -> None:
        self._settings = settings
        self._memory = memory
        self._db_type = (db_type or "").strip().lower() or None
        self._mem_lock = threading.Lock()
        self._mem_sessions: dict[str, dict[str, Any]] = {}
        self._mem_messages: list[dict[str, Any]] = []
        self._actor: Any = None  # HistoryDbActor（惰性，仅企业版远程路径）

    @classmethod
    def for_db_type(cls, db_type: str) -> "ChatHistoryStore":
        normalized = str(db_type or "").strip().lower() or "memory"
        if normalized == "memory":
            return cls(memory=True)
        if normalized in ("postgresql", "postgres", "pg"):
            return cls(settings=WebHistoryDbSettings.from_env(), db_type="postgresql")
        if normalized == "mysql":
            return cls(settings=WebHistoryDbSettings.from_env(), db_type="mysql")
        logger.warning(
            "[history] 不支持的历史库类型 %r，回退内存（不支持 sqlite）",
            normalized,
        )
        return cls(memory=True)

    @classmethod
    def from_env(cls) -> "ChatHistoryStore":
        return cls.for_db_type(resolve_history_db_type())

    @classmethod
    def memory(cls) -> "ChatHistoryStore":
        return cls(memory=True)

    @property
    def backend(self) -> HistoryBackend:
        if self._memory:
            return "memory"
        if self._db_type == "postgresql":
            return "postgresql"
        if self._db_type == "mysql":
            return "mysql"
        return "memory"

    @property
    def mysql_settings(self) -> WebHistoryDbSettings | None:
        return self._settings

    @property
    def available(self) -> bool:
        if self._memory:
            return True
        return self._settings is not None

    def _get_actor(self) -> Any:
        if self._actor is None:
            from .db_actor import HistoryDbActor

            self._actor = HistoryDbActor(self._settings, self._db_type)
        return self._actor

    def _insert_message_and_upsert_session(
        self,
        _conn: Any,
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
        """memory 写入（企业版远程走 actor，不经此方法）。"""
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
        if self._memory:
            inserted = await asyncio.to_thread(
                self._insert_message_and_upsert_session,
                None,
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
        else:
            inserted = await self._get_actor().record_message(
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
        if self._memory:
            inserted = await asyncio.to_thread(
                self._insert_message_and_upsert_session,
                None,
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
        else:
            inserted = await self._get_actor().record_message(
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
        try:
            return self._get_actor().list_sessions_sync(
                limit=limit, offset=offset, user=user,
            )
        except Exception:
            logger.exception("[history] %s 读取会话列表失败", self.backend.upper())
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
        try:
            return self._get_actor().get_session_detail_sync(session_id, user=user)
        except Exception:
            logger.exception("[history] %s 读取会话详情失败", self.backend.upper())
            return None

    async def list_sessions(
        self, *, limit: int = 20, offset: int = 0,
    ) -> list[dict[str, Any]]:
        if self._memory:
            return await asyncio.to_thread(
                self.list_sessions_blocking, limit=limit, offset=offset, user=None,
            )
        return await self._get_actor().list_sessions(limit=limit, offset=offset, user=None)

    async def get_session_detail(self, session_id: str) -> dict[str, Any] | None:
        if self._memory:
            return await asyncio.to_thread(
                self.get_session_detail_blocking, session_id, user=None,
            )
        return await self._get_actor().get_session_detail(session_id, user=None)

    async def close(self) -> None:
        if self._actor is not None:
            self._actor.stop()
        logger.info("[history] store 已关闭 backend=%s", self.backend)
