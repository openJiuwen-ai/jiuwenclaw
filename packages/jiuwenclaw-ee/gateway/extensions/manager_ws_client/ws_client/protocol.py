# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Manager WebSocket 线协议（与 Claw Manager manager_ws_server 对齐）。"""

from __future__ import annotations

from typing import Any

FRAME_TYPE_EVENT = "event"
FRAME_TYPE_REGISTER = "register"
FRAME_TYPE_HEARTBEAT = "heartbeat"
FRAME_TYPE_HEARTBEAT_ACK = "heartbeat.ack"
FRAME_TYPE_CONFIG_PUSH = "config.push"
FRAME_TYPE_CONFIG_ACK = "config.ack"
FRAME_TYPE_POD_STATUS_REPORT = "pod_status.report"
FRAME_TYPE_ERROR = "error"

EVENT_CONNECTION_ACK = "connection.ack"
EVENT_REGISTER_ACK = "register.ack"


def build_register(
    *,
    service_type: str = "gateway",
    jiuwenclaw_id: str | None = None,
    enc_pubkey: str | None = None,
    enc_alg: str | None = None,
    enc_pubkey_fp: str | None = None,
) -> dict[str, Any]:
    """构建 register 帧。

    若环境或参数带有 ``jiuwenclaw_id``（如 ``provision-local`` 注入的
    ``JIUWENCLAW_ID``），则上报给 Manager 复用已有 ``instance_info``；
    否则由 Manager 分配新 id，并在 ack 中回传。

    握手期同时上交 Gateway 的加密公钥（``enc_pubkey``），供 Manager 包裹 DEK。
    """
    from ..infrastructure.utils import get_gateway_register_identity, get_jiuwenclaw_id

    payload: dict[str, Any] = {"service_type": service_type}
    jid = str(jiuwenclaw_id or "").strip() or get_jiuwenclaw_id()
    if jid:
        payload["jiuwenclaw_id"] = jid
    payload.update(get_gateway_register_identity())
    if enc_pubkey:
        payload["enc_pubkey"] = enc_pubkey
        payload["enc_alg"] = enc_alg or "X25519"
        payload["enc_pubkey_fp"] = enc_pubkey_fp or ""
    return {
        "type": FRAME_TYPE_REGISTER,
        "payload": payload,
    }


def build_heartbeat(
    *,
    jiuwenclaw_id: str,
    service_type: str = "gateway",
    version: str | None = None,
    endpoint: str | None = None,
    seq: int | None = None,
) -> dict[str, Any]:
    """构建 heartbeat 帧（Gateway → Manager，用于刷新 ``instance_info.status``）。"""
    payload: dict[str, Any] = {
        "jiuwenclaw_id": str(jiuwenclaw_id or "").strip(),
        "service_type": service_type,
    }
    if version:
        payload["version"] = version
    if endpoint:
        payload["endpoint"] = endpoint
    if seq is not None:
        payload["seq"] = seq
    return {"type": FRAME_TYPE_HEARTBEAT, "payload": payload}


def build_pod_status_report(
    *,
    jiuwenclaw_id: str,
    service_type: str = "gateway",
    snapshot_time: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """构建 Pod 状态上报帧（Gateway -> Manager）。"""
    return {
        "type": FRAME_TYPE_POD_STATUS_REPORT,
        "payload": {
            "jiuwenclaw_id": str(jiuwenclaw_id or "").strip(),
            "service_type": service_type,
            "snapshot_time": snapshot_time,
            "data": data,
        },
    }


def build_heartbeat_ack(
    *,
    jiuwenclaw_id: str,
    seq: int | None = None,
) -> dict[str, Any]:
    """构建 heartbeat.ack 帧（Manager → Gateway）。"""
    payload: dict[str, Any] = {
        "status": "ok",
        "jiuwenclaw_id": str(jiuwenclaw_id or "").strip(),
    }
    if seq is not None:
        payload["seq"] = seq
    return {"type": FRAME_TYPE_HEARTBEAT_ACK, "payload": payload}


def build_config_ack(
    *,
    revision: str,
    success_flag: bool = True,
    error_message: str | None = None,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"revision": revision, "success_flag": success_flag}
    if error_message:
        payload["error_message"] = error_message
    if result:
        payload["result"] = result
    return {"type": FRAME_TYPE_CONFIG_ACK, "payload": payload}
