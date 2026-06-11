"""Manager WebSocket 线协议（Claw Manager ↔ Gateway）。"""

from __future__ import annotations

from typing import Any

FRAME_TYPE_EVENT = "event"
FRAME_TYPE_REGISTER = "register"
FRAME_TYPE_HEARTBEAT = "heartbeat"
FRAME_TYPE_HEARTBEAT_ACK = "heartbeat.ack"
FRAME_TYPE_CONFIG_PUSH = "config.push"
FRAME_TYPE_CONFIG_ACK = "config.ack"
FRAME_TYPE_ERROR = "error"

EVENT_CONNECTION_ACK = "connection.ack"
EVENT_REGISTER_ACK = "register.ack"


def build_register_ack(
    *,
    jiuwenclaw_id: str,
    sign_pubkey: str | None = None,
    sign_alg: str | None = None,
    key_version: str | None = None,
    sign_pubkey_fp: str | None = None,
) -> dict[str, Any]:
    jid = str(jiuwenclaw_id or "").strip()
    payload: dict[str, Any] = {"status": "ok", "jiuwenclaw_id": jid}
    if sign_pubkey:
        payload["sign_pubkey"] = sign_pubkey
        payload["sign_alg"] = sign_alg or "Ed25519"
        payload["key_version"] = key_version or "v1"
        payload["sign_pubkey_fp"] = sign_pubkey_fp or ""
    return {
        "type": FRAME_TYPE_EVENT,
        "event": EVENT_REGISTER_ACK,
        "payload": payload,
    }


def build_connection_ack(*, manager_id: str) -> dict[str, Any]:
    return {
        "type": FRAME_TYPE_EVENT,
        "event": EVENT_CONNECTION_ACK,
        "payload": {"status": "ready", "manager_id": manager_id},
    }


def build_heartbeat_ack(
    *,
    jiuwenclaw_id: str,
    seq: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "ok",
        "jiuwenclaw_id": str(jiuwenclaw_id or "").strip(),
    }
    if seq is not None:
        payload["seq"] = seq
    return {"type": FRAME_TYPE_HEARTBEAT_ACK, "payload": payload}


def build_config_push(
    *,
    revision: str,
    jiuwenclaw_id: str,
    config: dict[str, Any],
    enc: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "revision": revision,
        "jiuwenclaw_id": jiuwenclaw_id,
        "config": config,
    }
    if enc:
        payload["enc"] = enc
    return {
        "type": FRAME_TYPE_CONFIG_PUSH,
        "payload": payload,
    }


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


def build_error(message: str) -> dict[str, Any]:
    return {"type": FRAME_TYPE_ERROR, "payload": {"message": message}}
