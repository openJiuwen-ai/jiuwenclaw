# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Manager WebSocket 线协议（与 Claw Manager manager_ws_server 对齐）。"""

from __future__ import annotations

from typing import Any

FRAME_TYPE_EVENT = "event"
FRAME_TYPE_REGISTER = "register"
FRAME_TYPE_CONFIG_PUSH = "config.push"
FRAME_TYPE_CONFIG_ACK = "config.ack"
FRAME_TYPE_ERROR = "error"

EVENT_CONNECTION_ACK = "connection.ack"
EVENT_REGISTER_ACK = "register.ack"


def build_register(
    *,
    service_type: str = "gateway",
    service_id: str,
) -> dict[str, Any]:
    sid = str(service_id or "").strip()
    return {
        "type": FRAME_TYPE_REGISTER,
        "payload": {
            "service_type": service_type,
            "service_id": sid,
        },
    }


def build_config_ack(
    *,
    revision: str,
    ok: bool = True,
    error: str | None = None,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"revision": revision, "ok": ok}
    if error:
        payload["error"] = error
    if result:
        payload["result"] = result
    return {"type": FRAME_TYPE_CONFIG_ACK, "payload": payload}
