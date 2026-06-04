# coding: utf-8
"""manager_ws_client Database.ensure_ready 并发安全。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenclaw.infrastructure.module_importer import (
    import_manager_ws_client_module,
)

_db_mod = import_manager_ws_client_module("infrastructure.db")
Database = _db_mod.Database


@pytest.mark.asyncio
async def test_ensure_ready_waits_for_table_init_before_returning_handler():
    """handler 已创建但 tables_registered 未完成时，并发调用不得提前返回。"""
    db = Database()
    handler = MagicMock()
    handler.init_database = AsyncMock()
    handler.connect = AsyncMock()
    init_started = asyncio.Event()
    init_done = asyncio.Event()

    async def slow_init_all_tables(_handler: object) -> None:
        init_started.set()
        await init_done.wait()

    with (
        patch.object(db, "create_handler", return_value=handler),
        patch.object(_db_mod, "init_all_tables", side_effect=slow_init_all_tables),
    ):
        leader = asyncio.create_task(db.ensure_ready())
        await init_started.wait()

        waiter = asyncio.create_task(db.ensure_ready())
        await asyncio.sleep(0.05)
        assert not waiter.done(), "second ensure_ready must wait for init_all_tables"

        init_done.set()
        h1, h2 = await asyncio.gather(leader, waiter)

    assert h1 is handler
    assert h2 is handler
    assert db.tables_registered is True


@pytest.mark.asyncio
async def test_close_resets_tables_registered():
    db = Database()
    handler = MagicMock()
    handler.init_database = AsyncMock()
    handler.connect = AsyncMock()
    handler.disconnect = AsyncMock()

    with (
        patch.object(db, "_create_sqlite_handler", return_value=handler),
        patch.object(_db_mod, "init_all_tables", new=AsyncMock()),
    ):
        await db.ensure_ready()
        assert db.tables_registered is True

    await db.close()

    handler.disconnect.assert_awaited_once()
    assert db.tables_registered is False
    with pytest.raises(RuntimeError, match="not initialized"):
        _ = db.handler
