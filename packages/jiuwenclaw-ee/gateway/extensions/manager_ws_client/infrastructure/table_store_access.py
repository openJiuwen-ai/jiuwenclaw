# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Manager WS 写库入口：经 Gateway ``PersistentStore`` 注入，兼容现有 ``DBHandler`` 调用面。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

PersistentProvider = Callable[[], Awaitable[Any]]

_provider: PersistentProvider | None = None
_cached_handler: PersistentStoreHandlerAdapter | None = None


class PersistentStoreHandlerAdapter:
    """将 ``PersistentStore`` 适配为 EE ``DBHandler`` 同款 CRUD 面（表名 == store name）。"""

    def __init__(self, provider: PersistentProvider) -> None:
        self._provider = provider

    async def _store(self) -> Any:
        return await self._provider()

    @staticmethod
    def _as_row(data: dict[str, Any] | None) -> Any | None:
        if data is None:
            return None
        return SimpleNamespace(**data)

    async def get(self, table: str, filters: dict[str, Any]) -> Any | None:
        store = await self._store()
        row = await store.get(table, dict(filters))
        return self._as_row(row)

    async def create(self, table: str, record: dict[str, Any]) -> Any:
        store = await self._store()
        created = await store.create(table, dict(record))
        row = self._as_row(created)
        if row is None:
            raise RuntimeError(f"create {table!r} returned empty record")
        return row

    async def update(
        self,
        table: str,
        filters: dict[str, Any],
        updates: dict[str, Any],
    ) -> Any | None:
        store = await self._store()
        updated = await store.update(table, dict(filters), dict(updates))
        return self._as_row(updated)

    async def delete(self, table: str, filters: dict[str, Any]) -> bool:
        store = await self._store()
        return bool(await store.delete(table, dict(filters)))

    async def list_records(
        self,
        table: str,
        filters: dict[str, Any] | None = None,
        *,
        limit: int = 10_000,
        offset: int = 0,
        order_by: str = "",
    ) -> list[Any]:
        store = await self._store()
        rows = await store.list(
            table,
            filters=dict(filters or {}),
            order_by=order_by,
            limit=limit,
            offset=offset,
        )
        return [self._as_row(row) for row in rows if row is not None]


def set_table_store_provider(provider: PersistentProvider | None) -> None:
    """Gateway 启动时注入 ``StorageContext.persistent``（或等价 async 工厂）。"""
    global _provider, _cached_handler
    _provider = provider
    _cached_handler = None


def clear_table_store_provider() -> None:
    set_table_store_provider(None)


async def ensure_table_store() -> PersistentStoreHandlerAdapter:
    """返回经 ``PersistentStore`` 驱动的表 CRUD 适配器。"""
    global _cached_handler
    if _provider is None:
        raise RuntimeError(
            "PersistentStore provider is not wired; "
            "call wire_manager_ws_table_store() during Gateway startup"
        )
    if _cached_handler is None:
        _cached_handler = PersistentStoreHandlerAdapter(_provider)
    store = await _provider()
    await store.ensure_ready()
    return _cached_handler


async def get_table_store_handler_if_wired() -> PersistentStoreHandlerAdapter | None:
    if _provider is None:
        return None
    return await ensure_table_store()


__all__ = [
    "PersistentStoreHandlerAdapter",
    "clear_table_store_provider",
    "ensure_table_store",
    "get_table_store_handler_if_wired",
    "set_table_store_provider",
]
