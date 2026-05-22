# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Channel 配置写库后的运行时热加载触发（WS 写库路径）。"""

from __future__ import annotations

from typing import Any

from jiuwenclaw.gateway.channel_config_overlay import (
    ChannelConfigChange,
    channel_config_overlay_enabled,
    trigger_channel_config_reload,
)


def channel_config_reload_change_for_row(row: dict[str, Any]) -> ChannelConfigChange:
    """create 后按 status 决定 upsert（active）或 remove（inactive）。"""
    status = str(row.get("status") or "active").strip().lower()
    if status == "active":
        return ChannelConfigChange.upsert(row)
    return ChannelConfigChange.remove(row)


async def maybe_trigger_channel_config_reload(
    change: ChannelConfigChange | None = None,
) -> None:
    """distributed 模式下触发 Gateway channel 热加载。"""
    if channel_config_overlay_enabled():
        await trigger_channel_config_reload(change)
