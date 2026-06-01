# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenclaw.gateway.channel_config_db import (
    _load_manager_ws_client_modules,
    load_active_channel_config_rows,
)


def test_load_manager_ws_client_modules_returns_database_and_list_fn():
    ext_root, database_cls, list_fn = _load_manager_ws_client_modules()
    assert isinstance(ext_root, Path)
    assert database_cls.__name__ == "Database"
    assert callable(list_fn)


@pytest.mark.asyncio
async def test_load_active_channel_config_rows_uses_database_ensure_ready():
    rows = [
        {
            "channel_id": "feishu-bot-1",
            "channel_type": "feishu",
            "bot_id": "feishu-bot-1",
            "config": {"app_id": "cli_test", "app_secret": "secret"},
            "status": "active",
        }
    ]
    handler = object()
    db = MagicMock()
    db.ensure_ready = AsyncMock(return_value=handler)
    database_cls = MagicMock(return_value=db)
    list_active = AsyncMock(return_value=rows)
    ext_root = Path("/tmp/manager_ws_client")

    with patch(
        "jiuwenclaw.gateway.channel_config_db._load_manager_ws_client_modules",
        return_value=(ext_root, database_cls, list_active),
    ):
        result = await load_active_channel_config_rows()

    assert result == rows
    database_cls.assert_called_once_with(relative_root=ext_root)
    db.ensure_ready.assert_awaited_once_with(log_prefix="channel_config_db")
    list_active.assert_awaited_once_with(handler)
