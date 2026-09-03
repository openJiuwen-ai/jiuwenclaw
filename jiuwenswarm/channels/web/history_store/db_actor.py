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
        await self._ensure_pin_columns()
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

    async def _count_sessions(self, *, user: str) -> int:
        """全量会话计数（供分页 total）。DB 异常向上抛，由调用方决定回退。"""
        if not user:
            user = "guest"
        handler = await self._get_handler()
        rows = await handler.list_records(
            "sessions", {"user": user}, limit=100_000, offset=0, order_by="-updated_at",
        )
        return len(rows)

    async def count_sessions(self, **kw: Any) -> int:
        return await self.run_async(self._count_sessions(**kw))

    def count_sessions_sync(self, **kw: Any) -> int:
        return self.run_sync(self._count_sessions(**kw))

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

    async def _ensure_pin_columns(self) -> None:
        """存量表列迁移：sessions 表补 ``pinned`` / ``pin_order`` 列（幂等）。

        foundation ``init_table`` 只 create_all，不给已存在的表加列（与
        service_config_template 缺列事故同型）；ORM 模型带上新列后，任何 SELECT
        都会引用它们，存量表缺列会直接 UndefinedColumn。这里按方言直连 information_schema
        探测后 ALTER 补列；失败仅告警（新库由 create_all 直接建出，不需要迁移）。
        """
        if self._settings is None:
            return
        try:
            if self._db_type == "postgresql":
                import asyncpg

                conn = await asyncpg.connect(
                    host=self._settings.host,
                    port=int(self._settings.port),
                    user=self._settings.user,
                    password=self._settings.password,
                    database=self._settings.database,
                )
                try:
                    schema = self._settings.pg_schema or "public"
                    rows = await conn.fetch(
                        "SELECT column_name FROM information_schema.columns"
                        " WHERE table_schema=$1 AND table_name='sessions'",
                        schema,
                    )
                    names = {r["column_name"] for r in rows}
                    table = f'"{schema}"."sessions"'
                    if "pinned" not in names:
                        await conn.execute(
                            f'ALTER TABLE {table} ADD COLUMN "pinned" BOOLEAN NOT NULL DEFAULT FALSE'
                        )
                    if "pin_order" not in names:
                        await conn.execute(
                            f'ALTER TABLE {table} ADD COLUMN "pin_order" INTEGER NOT NULL DEFAULT 0'
                        )
                finally:
                    await conn.close()
            elif self._db_type == "mysql":
                import aiomysql

                conn = await aiomysql.connect(
                    host=self._settings.host,
                    port=int(self._settings.port),
                    user=self._settings.user,
                    password=self._settings.password,
                    db=self._settings.database,
                )
                try:
                    cur = await conn.cursor()
                    await cur.execute(
                        "SELECT column_name FROM information_schema.columns"
                        " WHERE table_schema=%s AND table_name='sessions'",
                        (self._settings.database,),
                    )
                    names = {r[0] for r in await cur.fetchall()}
                    if "pinned" not in names:
                        await cur.execute(
                            "ALTER TABLE `sessions` ADD COLUMN `pinned` TINYINT(1) NOT NULL DEFAULT 0"
                        )
                    if "pin_order" not in names:
                        await cur.execute(
                            "ALTER TABLE `sessions` ADD COLUMN `pin_order` INT NOT NULL DEFAULT 0"
                        )
                    await conn.commit()
                finally:
                    conn.close()
            else:
                return
            logger.info("[history] sessions 置顶列迁移完成 db_type=%s", self._db_type)
        except Exception:  # noqa: BLE001
            logger.warning("[history] sessions 置顶列迁移失败（可能已存在）", exc_info=True)

    async def _set_session_pinned(
        self, session_id: str, pinned: bool, *, user: str,
    ) -> tuple[bool, int] | None:
        """remote 模式置顶/取消置顶（语义与本地 ``set_session_pinned`` 一致）。

        目标行不存在返回 ``None``（调用方回 NOT_FOUND）；成功返回 ``(pinned, pin_order)``。
        重编号与目标状态写入共用 handler CRUD，pg/mysql 通用。
        """
        if not user:
            user = "guest"
        handler = await self._get_handler()
        target = await handler.get("sessions", {"session_id": session_id, "user": user})
        if target is None:
            return None
        # 1. 写目标状态：置顶保留原 pin_order 供排序（新置顶为 0 → 排最前）；取消清零
        if pinned:
            await handler.update("sessions", {"session_id": session_id}, {"pinned": True})
        else:
            await handler.update(
                "sessions", {"session_id": session_id}, {"pinned": False, "pin_order": 0}
            )
        # 2. 收集该用户全部置顶会话（含刚置顶的），按 pin_order 稳定排序
        rows = await handler.list_records(
            "sessions", {"user": user, "pinned": True}, limit=10_000, offset=0, order_by="pin_order"
        )
        ordered = sorted(
            (_row_to_dict(r) for r in rows),
            key=lambda d: int(d.get("pin_order") or 0),
        )
        # 3. 紧凑重编号 1..N（幂等：已在位则跳过写）
        new_orders: dict[str, int] = {}
        for idx, d in enumerate(ordered, start=1):
            sid = str(d.get("session_id") or "")
            if int(d.get("pin_order") or 0) != idx:
                await handler.update(
                    "sessions", {"session_id": sid}, {"pinned": True, "pin_order": idx}
                )
            if sid:
                new_orders[sid] = idx
        return pinned, new_orders.get(session_id, 0)

    async def _list_pinned_sessions(self, *, user: str) -> list[dict[str, Any]]:
        """该用户全部置顶会话，按 pin_order 升序。"""
        if not user:
            user = "guest"
        handler = await self._get_handler()
        rows = await handler.list_records(
            "sessions", {"user": user, "pinned": True}, limit=10_000, offset=0, order_by="pin_order"
        )
        return [_row_to_dict(r) for r in rows]

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

    async def _delete_session(self, session_id: str, *, user: str | None) -> bool:
        """删除会话的 sessions 行与全部 messages 行。

        ``user`` 非空时校验归属（行不属于该用户则拒绝删除）；为空则仅按
        session_id 删除（与本地文件删除的无归属校验语义一致）。
        """
        handler = await self._get_handler()
        if user:
            s = await handler.get("sessions", {"session_id": session_id, "user": user})
            if s is None:
                return False
        await handler.delete("messages", {"session_id": session_id})
        await handler.delete("sessions", {"session_id": session_id})
        return True

    async def delete_session(self, session_id: str, *, user: str | None = None) -> bool:
        return await self.run_async(self._delete_session(session_id, user=user))

    def delete_session_sync(self, session_id: str, *, user: str | None = None) -> bool:
        return self.run_sync(self._delete_session(session_id, user=user))

    async def set_session_pinned(
        self, session_id: str, pinned: bool, *, user: str | None = None
    ) -> tuple[bool, int] | None:
        return await self.run_async(self._set_session_pinned(session_id, pinned, user=user or ""))

    def set_session_pinned_sync(
        self, session_id: str, pinned: bool, *, user: str | None = None
    ) -> tuple[bool, int] | None:
        return self.run_sync(self._set_session_pinned(session_id, pinned, user=user or ""))

    async def list_pinned_sessions(self, *, user: str | None = None) -> list[dict[str, Any]]:
        return await self.run_async(self._list_pinned_sessions(user=user or ""))

    def list_pinned_sessions_sync(self, *, user: str | None = None) -> list[dict[str, Any]]:
        return self.run_sync(self._list_pinned_sessions(user=user or ""))

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
