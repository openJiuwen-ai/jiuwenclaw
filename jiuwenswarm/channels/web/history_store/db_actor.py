# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved

"""Web 历史 DB-actor：mysql/pg 统一经 foundation 高层 CRUD。

asyncpg/aiomysql engine 绑 event loop；本库被多 loop/线程消费（写经
``HistoryFrameRunner`` 独立 loop、读经同步 HTTP 线程），故 mysql/pg 访问统一在
本 actor 专用 loop 内执行：同步调用 ``run_sync`` 阻塞等结果；async 调用
``run_async`` 投递并 await future。foundation CRUD 始终在 actor loop 内，不跨 loop。

仅企业版远程路径加载（``store._get_actor`` 惰性 import）；个人版纯内存不加载本模块，
故不引入 foundation 依赖。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Awaitable

logger = logging.getLogger("jiuwenswarm.web.history")


def _row_to_dict(row: Any) -> dict[str, Any]:
    """将 DB 行转 dict；兼容 dict / SQLAlchemy Row / ORM 实体。"""
    if isinstance(row, dict):
        return dict(row)
    if hasattr(row, "keys") and callable(getattr(row, "keys")) and not hasattr(row, "__table__"):
        return {k: row[k] for k in row.keys()}
    return {k: v for k, v in vars(row).items() if not k.startswith("_sa_")}


class HistoryDbActor:
    """mysql/pg 统一 DB-actor（专用 loop + 守护线程）。"""

    def __init__(self, settings: Any, db_type: str) -> None:
        self._settings = settings
        self._db_type = (db_type or "").strip().lower()
        self._loop: Any = None
        self._thread: threading.Thread | None = None
        self._started: bool = False
        self._handler: Any = None
        self._init_lock = threading.Lock()

    def ensure_started(self) -> None:
        if self._started:
            return
        with self._init_lock:
            if self._started:
                return
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._run_loop, name="web-history-db", daemon=True
            )
            self._thread.start()
            self._started = True

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def run_sync(self, coro: Awaitable[Any], *, timeout: float = 30.0) -> Any:
        """同步上下文：把 coro 投到 actor loop 执行并阻塞等结果。"""
        self.ensure_started()
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=timeout)

    async def run_async(self, coro: Awaitable[Any]) -> Any:
        """async 上下文（来自其他 loop）：投递到 actor loop 并 await future。"""
        self.ensure_started()
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return await asyncio.wrap_future(fut)

    async def _get_handler(self) -> Any:
        """构造/复用 foundation handler（mysql→MySQLHandler；pg→PostgreSQLHandler(schema)）。

        在 actor loop 内调用。
        """
        if self._handler is not None:
            return self._handler
        if self._settings is None:
            raise RuntimeError("Web 历史库远程 settings 未配置，无法建立连接")
        from openjiuwen_runtime.foundation.db import MySQLHandler, PostgreSQLHandler
        from .tables import init_web_history_tables

        if self._db_type == "postgresql":
            handler = PostgreSQLHandler(
                host=self._settings.host,
                port=int(self._settings.port),
                database=self._settings.database,
                schema=self._settings.pg_schema,
                user=self._settings.user,
                password=self._settings.password,
            )
        else:
            handler = MySQLHandler(
                host=self._settings.host,
                port=int(self._settings.port),
                database=self._settings.database,
                user=self._settings.user,
                password=self._settings.password,
            )
        await handler.init_database()
        await handler.connect()
        await init_web_history_tables(handler)
        self._handler = handler
        logger.info(
            "[history] %s store 初始化完成 %s:%s/%s%s",
            self._db_type.upper(),
            self._settings.host, self._settings.port, self._settings.database,
            f" schema={self._settings.pg_schema}" if self._db_type == "postgresql" else "",
        )
        return handler

    async def _record_message(
        self,
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
        handler = await self._get_handler()
        # message insert-ignore：唯一键 (session_id, request_id, role) 冲突则视为未插入
        if await handler.get(
            "messages",
            {"session_id": session_id, "request_id": request_id, "role": role},
        ) is not None:
            return False
        try:
            await handler.create(
                "messages",
                {
                    "session_id": session_id,
                    "request_id": request_id,
                    "role": role,
                    "content": content,
                    "event_type": event_type,
                    "timestamp": ts,
                },
            )
        except Exception as exc:  # noqa: BLE001
            low = str(exc).lower()
            if "unique" in low or "duplicate" in low or "conflict" in low:
                return False
            raise
        # upsert session（count+1，title COALESCE 保留已有非空值）
        sess = await handler.get("sessions", {"session_id": session_id})
        if sess is not None:
            d = _row_to_dict(sess)
            new_count = int(d.get("message_count") or 0) + 1
            new_title = d.get("title") or title
            await handler.update(
                "sessions",
                {"session_id": session_id},
                {
                    "message_count": new_count,
                    "last_preview": preview,
                    "updated_at": ts,
                    "title": new_title,
                },
            )
        else:
            await handler.create(
                "sessions",
                {
                    "session_id": session_id,
                    "user": user or "guest",
                    "title": title,
                    "message_count": 1,
                    "last_preview": preview,
                    "created_at": ts,
                    "updated_at": ts,
                },
            )
        return True

    async def _list_sessions(
        self, *, limit: int, offset: int, user: str,
    ) -> list[dict[str, Any]]:
        if not user:
            user = "guest"
        handler = await self._get_handler()
        rows = await handler.list_records(
            "sessions", {"user": user}, limit=limit, offset=offset, order_by="-updated_at"
        )
        return [_row_to_dict(r) for r in rows]

    async def _get_session_detail(
        self, session_id: str, *, user: str,
    ) -> dict[str, Any] | None:
        if not user:
            user = "guest"
        handler = await self._get_handler()
        s = await handler.get("sessions", {"session_id": session_id, "user": user})
        if s is None:
            return None
        msgs = await handler.list_records(
            "messages", {"session_id": session_id}, limit=10_000, offset=0, order_by="timestamp"
        )
        return {**_row_to_dict(s), "messages": [_row_to_dict(m) for m in msgs]}

    async def record_message(self, **kw: Any) -> bool:
        return await self.run_async(self._record_message(**kw))

    def record_message_sync(self, **kw: Any) -> bool:
        return self.run_sync(self._record_message(**kw))

    async def list_sessions(self, **kw: Any) -> list[dict[str, Any]]:
        return await self.run_async(self._list_sessions(**kw))

    def list_sessions_sync(self, **kw: Any) -> list[dict[str, Any]]:
        return self.run_sync(self._list_sessions(**kw))

    async def get_session_detail(self, **kw: Any) -> dict[str, Any] | None:
        return await self.run_async(self._get_session_detail(**kw))

    def get_session_detail_sync(self, **kw: Any) -> dict[str, Any] | None:
        return self.run_sync(self._get_session_detail(**kw))

    def stop(self) -> None:
        if not self._started:
            return
        try:
            if self._handler is not None and self._loop is not None:
                asyncio.run_coroutine_threadsafe(
                    self._handler.disconnect(), self._loop
                ).result(timeout=10)
        except Exception:  # noqa: BLE001
            logger.debug("[history] db handler disconnect failed", exc_info=True)
        self._handler = None
        try:
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:  # noqa: BLE001
            pass
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._started = False
        self._loop = None
        self._thread = None
