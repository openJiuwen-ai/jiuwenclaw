"""Manager WebSocket 线协议（Claw Manager ↔ Gateway）。"""

from __future__ import annotations

from typing import Any

FRAME_TYPE_EVENT = "event"
FRAME_TYPE_REGISTER = "register"
FRAME_TYPE_HEARTBEAT = "heartbeat"
FRAME_TYPE_CONFIG_PUSH = "config.push"
FRAME_TYPE_CONFIG_ACK = "config.ack"
FRAME_TYPE_ERROR = "error"

EVENT_CONNECTION_ACK = "connection.ack"
EVENT_REGISTER_ACK = "register.ack"


def build_register_ack(*, jiuwenclaw_id: str) -> dict[str, Any]:
    jid = str(jiuwenclaw_id or "").strip()
    return {
        "type": FRAME_TYPE_EVENT,
        "event": EVENT_REGISTER_ACK,
        "payload": {"status": "ok", "jiuwenclaw_id": jid},
    }


def build_connection_ack(*, manager_id: str) -> dict[str, Any]:
    return {
        "type": FRAME_TYPE_EVENT,
        "event": EVENT_CONNECTION_ACK,
        "payload": {"status": "ready", "manager_id": manager_id},
    }


def build_config_push(*, revision: str, config: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": FRAME_TYPE_CONFIG_PUSH,
        "payload": {"revision": revision, "config": config},
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


def build_error(message: str) -> dict[str, Any]:
    return {"type": FRAME_TYPE_ERROR, "payload": {"message": message}}
