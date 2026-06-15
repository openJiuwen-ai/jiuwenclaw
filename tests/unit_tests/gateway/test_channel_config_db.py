# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from jiuwenclaw.gateway.channel_config_db import (
    _load_manager_ws_client_modules,
    load_active_channel_config_rows,
)


def test_load_manager_ws_client_modules_returns_ensure_db_and_list_fn():
    ensure_db_handler, list_fn = _load_manager_ws_client_modules()
    assert callable(ensure_db_handler)
    assert callable(list_fn)


@pytest.mark.asyncio
async def test_load_active_channel_config_rows_uses_ensure_db_handler():
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
    ensure_db_handler = AsyncMock(return_value=handler)
    list_active = AsyncMock(return_value=rows)

    with patch(
        "jiuwenclaw.gateway.channel_config_db._load_manager_ws_client_modules",
        return_value=(ensure_db_handler, list_active),
    ):
        result = await load_active_channel_config_rows()

    assert result == rows
    ensure_db_handler.assert_awaited_once_with(log_prefix="channel_config_db")
    list_active.assert_awaited_once_with(handler)
