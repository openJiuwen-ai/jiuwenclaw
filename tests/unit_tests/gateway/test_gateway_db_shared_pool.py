"""GatewayDb 单例与 ensure_db_handler 共用连接池。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jiuwenclaw.infrastructure.module_importer import import_manager_ws_client_module


@pytest.fixture
def gateway_db_modules():
    db_mod = import_manager_ws_client_module("infrastructure.db")
    gateway_db_mod = import_manager_ws_client_module("core.enterprise_config.gateway_db")
    previous_current = gateway_db_mod.GatewayDb._current
    gateway_db_mod.GatewayDb._current = None
    yield db_mod, gateway_db_mod
    gateway_db_mod.GatewayDb._current = previous_current


def test_gateway_db_bind_reuses_singleton(gateway_db_modules):
    _, gateway_db_mod = gateway_db_modules

    first = gateway_db_mod.GatewayDb.bind("inst-1")
    second = gateway_db_mod.GatewayDb.bind("inst-2")

    assert first is second
    assert second.jiuwenclaw_id == "inst-2"
    assert isinstance(first, gateway_db_mod.GatewayDb)


def test_gateway_db_extends_database(gateway_db_modules):
    db_mod, gateway_db_mod = gateway_db_modules

    db = gateway_db_mod.GatewayDb.current()
    assert isinstance(db, db_mod.Database)


@pytest.mark.asyncio
async def test_gateway_db_and_ensure_db_handler_share_same_instance(
    gateway_db_modules,
    monkeypatch: pytest.MonkeyPatch,
):
    db_mod, gateway_db_mod = gateway_db_modules
    shared = gateway_db_mod.GatewayDb.current()
    handler = MagicMock(name="db_handler")
    ensure_ready = AsyncMock(return_value=handler)
    monkeypatch.setattr(shared, "ensure_ready", ensure_ready)

    db = gateway_db_mod.GatewayDb.bind("inst-1")
    handler_via_gateway_db = await db.ensure_ready(log_prefix="enterprise_config")
    handler_via_helper = await db_mod.ensure_db_handler(log_prefix="log_masking")

    assert db is shared
    assert handler_via_gateway_db is handler
    assert handler_via_helper is handler
    assert ensure_ready.await_count == 2
