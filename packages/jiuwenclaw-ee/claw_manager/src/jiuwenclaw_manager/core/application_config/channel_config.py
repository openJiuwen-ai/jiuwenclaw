# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved
"""Claw Manager 向 Gateway 推送 channel_config 变更。"""

from __future__ import annotations

from typing import Any

from jiuwenclaw_manager.manager_ws_server import ManagerWsServer
from jiuwenclaw_manager.manager_ws_server.server import push_to_instance


async def push_channel_config_op(
    jiuwenclaw_id: str,
    op: str,
    *,
    channel: dict[str, Any] | None = None,
    channel_id: str | None = None,
    server: ManagerWsServer | None = None,
) -> dict[str, Any]:
    """推送 channel 配置变更（``config.channel_config``），返回 config.ack payload。"""
    payload: dict[str, Any] = {"op": op}
    if channel is not None:
        payload["channel"] = channel
    if channel_id is not None:
        payload["channel_id"] = channel_id
    return await push_to_instance(
        jiuwenclaw_id,
        config={"channel_config": payload},
        server=server,
    )
