# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``channel_config`` 冷启动读库：经 manager_ws_client ``DBHandler``（与 WS 写库同栈）。"""

from __future__ import annotations

from typing import Any

from jiuwenclaw.infrastructure.module_importer import (
    import_manager_ws_client_module,
)
from jiuwenclaw.utils import logger

_LOG = "[channel_config_db]"


def _load_manager_ws_client_modules() -> tuple[Any, Any]:
    db_mod = import_manager_ws_client_module("infrastructure.db")
    channel_mod = import_manager_ws_client_module(
        "core.application_config.channel_config"
    )
    return db_mod.ensure_db_handler, channel_mod.list_active_channel_config_rows


async def load_active_channel_config_rows() -> list[dict[str, Any]]:
    """连接 manager_ws_client 库并列出 active ``channel_config`` 行；失败时返回空列表。"""
    try:
        ensure_db_handler, list_active = _load_manager_ws_client_modules()
        handler = await ensure_db_handler(log_prefix="channel_config_db")
        return await list_active(handler)
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s channel_config read failed: %s", _LOG, exc, exc_info=True)
        return []
