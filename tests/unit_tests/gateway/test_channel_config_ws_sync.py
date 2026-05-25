# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Channel 配置 WS 同步单元测试（manager_ws_client apply_channel_config_sync）。"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from jiuwenclaw.gateway.channel_config_overlay import ChannelConfigChange


def _manager_ws_client_root() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "packages/jiuwenclaw-ee/gateway/extensions/manager_ws_client"
    )


def _ensure_package(name: str, path: str) -> None:
    if name in sys.modules:
        return
    pkg = types.ModuleType(name)
    pkg.__path__ = [path]
    sys.modules[name] = pkg


@pytest.fixture(scope="module")
def channel_config_sync_module():
    root = _manager_ws_client_root()
    base = "jiuwenclaw.loaded_extension.manager_ws_client"
    _ensure_package("jiuwenclaw.loaded_extension", str(root.parent.parent.parent))
    _ensure_package(base, str(root))
    _ensure_package(f"{base}.core", str(root / "core"))
    _ensure_package(f"{base}.core.application_config", str(root / "core" / "application_config"))
    _ensure_package(f"{base}.infrastructure", str(root / "infrastructure"))
    _ensure_package(f"{base}.models", str(root / "models"))
    _ensure_package(f"{base}.schemas", str(root / "schemas"))
    return importlib.import_module(
        "jiuwenclaw.loaded_extension.manager_ws_client.core.application_config.channel_config"
    )


def _make_handler(
    *,
    get_row: object | None = None,
    create_row: object | None = None,
    update_row: object | None = None,
    delete_ok: bool = True,
) -> AsyncMock:
    handler = AsyncMock()
    handler.get = AsyncMock(return_value=get_row)
    handler.create = AsyncMock(return_value=create_row)
    handler.update = AsyncMock(return_value=update_row)
    handler.delete = AsyncMock(return_value=delete_ok)
    return handler


def _row_obj(**kwargs: object) -> SimpleNamespace:
    defaults = {
        "id": 1,
        "channel_id": "feishu-1",
        "channel_name": "Feishu",
        "channel_type": "feishu",
        "bot_id": "feishu",
        "config": {"app_id": "a", "app_secret": "s"},
        "status": "active",
        "created_at": None,
        "updated_at": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_ws_sync_create_triggers_active_reload(channel_config_sync_module):
    apply_channel_config_sync = channel_config_sync_module.apply_channel_config_sync
    created = _row_obj()
    handler = _make_handler(get_row=None, create_row=created)
    reload = AsyncMock()

    with patch.object(channel_config_sync_module, "maybe_trigger_channel_config_reload", reload):
        result = await apply_channel_config_sync(
            handler,
            "create",
            {
                "channel": {
                    "channel_id": "feishu-1",
                    "channel_name": "Feishu",
                    "channel_type": "feishu",
                    "bot_id": "feishu",
                    "config": {"app_id": "a", "app_secret": "s"},
                    "status": "active",
                }
            },
        )

    assert result == {"channel_id": "feishu-1"}
    reload.assert_awaited_once()
    change = reload.await_args.args[0]
    assert isinstance(change, ChannelConfigChange)
    assert change.op == "upsert"


@pytest.mark.asyncio
async def test_ws_sync_deactivate_triggers_remove_reload(channel_config_sync_module):
    apply_channel_config_sync = channel_config_sync_module.apply_channel_config_sync
    existing = _row_obj(status="active")
    updated = _row_obj(status="inactive")
    handler = _make_handler(get_row=existing, update_row=updated)
    reload = AsyncMock()

    with patch.object(channel_config_sync_module, "maybe_trigger_channel_config_reload", reload):
        result = await apply_channel_config_sync(
            handler,
            "deactivate",
            {"channel_id": "feishu-1"},
        )

    assert result == {"channel_id": "feishu-1", "status": "inactive"}
    change = reload.await_args.args[0]
    assert change.op == "remove"


@pytest.mark.asyncio
async def test_ws_sync_delete_triggers_remove_reload(channel_config_sync_module):
    apply_channel_config_sync = channel_config_sync_module.apply_channel_config_sync
    existing = _row_obj()
    handler = _make_handler(get_row=existing, delete_ok=True)
    reload = AsyncMock()

    with patch.object(channel_config_sync_module, "maybe_trigger_channel_config_reload", reload):
        result = await apply_channel_config_sync(
            handler,
            "delete",
            {"channel_id": "feishu-1"},
        )

    assert result is None
    change = reload.await_args.args[0]
    assert change.op == "remove"
