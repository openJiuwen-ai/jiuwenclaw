# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared helpers for skill evolution events and status pushes."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)

TEAM_EVOLUTION_IDLE_SLEEP_SEC = 1.0
TEAM_EVOLUTION_EVENT_TIMEOUT_SEC = 900.0
TEAM_EVOLUTION_START_STAGE = "collecting"
TEAM_EVOLUTION_START_MESSAGE = "Running team skill evolution analysis..."
TEAM_EVOLUTION_NOOP_STAGE = "no_evolution_generated"
TEAM_EVOLUTION_HIDDEN_STAGE = "hidden"
TEAM_EVOLUTION_NOOP_MARKERS = (
    "no existing skill found",
    "no evolution signals detected",
    "no evolution records generated",
)


@dataclass(frozen=True)
class EvolutionPushContext:
    transport: Any
    channel_id: str | None
    session_id: str


@dataclass(frozen=True)
class EvolutionStatusUpdate:
    request_id: str
    status: str
    stage: str
    message: str = ""


def event_payload_dict(evt: Any) -> dict[str, Any]:
    if hasattr(evt, "payload") and isinstance(evt.payload, dict):
        return dict(evt.payload)
    if isinstance(evt, dict):
        return dict(evt)
    return {}


def event_type(evt: Any) -> str:
    evt_type = getattr(evt, "type", None)
    if isinstance(evt_type, str) and evt_type:
        return evt_type
    payload = event_payload_dict(evt)
    payload_type = payload.get("event_type")
    return payload_type if isinstance(payload_type, str) else ""


def is_evolution_approval_event(evt: Any) -> bool:
    if event_type(evt) == "chat.ask_user_question":
        return True
    payload = event_payload_dict(evt)
    return payload.get("event_type") == "chat.ask_user_question"


def evolution_event_kind(evt: Any) -> str:
    payload = event_payload_dict(evt)
    meta = payload.get("_evolution_meta")
    if isinstance(meta, dict):
        event_kind = meta.get("event_kind")
        if isinstance(event_kind, str) and event_kind:
            return event_kind
    if is_evolution_approval_event(evt):
        return "approval"
    return "stream"


def is_evolution_outcome_event(evt: Any) -> bool:
    return evolution_event_kind(evt) == "outcome"


def evolution_outcome_from_event(evt: Any) -> dict[str, str]:
    payload = event_payload_dict(evt)
    if not isinstance(payload, dict):
        return {"status": "completed", "message": str(payload)}
    meta = payload.get("_evolution_meta")
    meta_status = meta.get("status") if isinstance(meta, dict) else None
    status = str(payload.get("status") or meta_status or "completed").strip().lower() or "completed"
    message = str(payload.get("message") or payload.get("content") or "")
    return {"status": status, "message": message}


def extract_evolution_request_id(evt: Any) -> str | None:
    payload = event_payload_dict(evt)
    request_id = payload.get("request_id")
    if isinstance(request_id, str):
        request_id = request_id.strip()
    return request_id or None


def is_evolution_started_progress(evt: Any) -> bool:
    payload = event_payload_dict(evt)
    meta = payload.get("_evolution_meta")
    if not isinstance(meta, dict):
        return False
    event_kind = str(meta.get("event_kind") or "").strip().lower()
    stage = str(payload.get("stage") or meta.get("stage") or "").strip().lower()
    return event_kind == "progress" and stage == "started"


def team_evolution_terminal_progress(evt: Any) -> dict[str, str] | None:
    payload = event_payload_dict(evt)
    meta = payload.get("_evolution_meta")
    meta_status = meta.get("status") if isinstance(meta, dict) else None
    meta_stage = meta.get("stage") if isinstance(meta, dict) else None
    status = str(payload.get("status") or meta_status or "").strip().lower()
    stage = str(payload.get("stage") or meta_stage or "").strip().lower()
    message = str(payload.get("message") or payload.get("content") or "")
    message_lower = message.lower()
    if any(marker in message_lower for marker in TEAM_EVOLUTION_NOOP_MARKERS):
        return {
            "status": "completed",
            "stage": TEAM_EVOLUTION_NOOP_STAGE,
            "message": message or "No evolution generated",
        }
    if status == "end" or stage in {"completed", "failed", "timed_out"}:
        return {
            "status": status or "end",
            "stage": stage or "completed",
            "message": message,
        }
    return None


def build_evolution_status_update(
    request_id: str,
    status: str,
    stage: str,
    message: str = "",
) -> EvolutionStatusUpdate:
    return EvolutionStatusUpdate(
        request_id=request_id,
        status=status,
        stage=stage,
        message=message,
    )


def team_evolution_end_update(
    request_id: str,
    terminal: dict[str, str] | None,
) -> EvolutionStatusUpdate:
    if terminal is None:
        return build_evolution_status_update(
            request_id=request_id,
            status="end",
            stage="completed",
            message="Team skill evolution analysis completed",
        )
    stage = str(terminal.get("stage") or terminal.get("status") or "completed").strip().lower()
    message = str(terminal.get("message") or "")
    if stage in {"failed", "timed_out"}:
        return build_evolution_status_update(
            request_id=request_id,
            status="end",
            stage=TEAM_EVOLUTION_HIDDEN_STAGE,
            message=message,
        )
    if stage == TEAM_EVOLUTION_NOOP_STAGE:
        return build_evolution_status_update(
            request_id=request_id,
            status="end",
            stage=stage,
            message=message,
        )
    return build_evolution_status_update(
        request_id=request_id,
        status="end",
        stage=stage or "completed",
        message=message or "Team skill evolution analysis completed",
    )


def group_evolution_approvals(
    session_id: str,
    events: list[Any],
    *,
    warn_missing_request_id: Callable[[str], None] | None = None,
) -> tuple[dict[str, list[Any]], list[str]]:
    grouped: dict[str, list[Any]] = {}
    for evt in events:
        if not is_evolution_approval_event(evt):
            continue
        request_id = extract_evolution_request_id(evt)
        if request_id is None:
            if warn_missing_request_id is not None:
                warn_missing_request_id(session_id)
            continue
        grouped.setdefault(request_id, []).append(evt)
    return grouped, []


def make_team_evolution_cycle_request_id(session_id: str, cycle_index: int) -> str:
    return f"team_evolve_{session_id}_{cycle_index}"


async def push_evolution_status(
    push_context: EvolutionPushContext,
    status_update: EvolutionStatusUpdate,
    build_push_message: Callable[..., dict[str, Any]],
    *,
    include_payload_request_id: bool = True,
) -> None:
    payload = {
        "event_type": "chat.evolution_status",
        "status": status_update.status,
        "stage": status_update.stage,
        "message": status_update.message,
    }
    if include_payload_request_id:
        payload["request_id"] = status_update.request_id
    await push_context.transport.send_push(
        build_push_message(
            session_id=push_context.session_id,
            request_id=status_update.request_id,
            fallback_channel_id=push_context.channel_id,
            payload=payload,
        )
    )


async def push_evolution_event(
    push_context: EvolutionPushContext,
    request_id: str,
    evt: Any,
    build_push_message: Callable[..., dict[str, Any]],
) -> None:
    payload = event_payload_dict(evt)
    evt_type = event_type(evt)
    if evt_type and "event_type" not in payload:
        payload["event_type"] = evt_type
    payload.setdefault("request_id", request_id)
    await push_context.transport.send_push(
        build_push_message(
            session_id=push_context.session_id,
            request_id=request_id,
            fallback_channel_id=push_context.channel_id,
            payload=payload,
        )
    )


async def broadcast_evolution_progress(
    channel_id: str | None,
    session_id: str,
    events: list[Any],
    *,
    parse_stream_chunk: Callable[[Any], dict[str, Any] | None],
    broadcast_event: Callable[[str | None, str, dict[str, Any]], None],
) -> None:
    for evt in events:
        if (
            is_evolution_approval_event(evt)
            or is_evolution_outcome_event(evt)
            or team_evolution_terminal_progress(evt) is not None
        ):
            continue
        parsed = parse_stream_chunk(evt)
        if parsed is not None:
            broadcast_event(channel_id, session_id, parsed)


async def push_evolution_progress(
    push_context: EvolutionPushContext,
    request_id: str,
    events: list[Any],
    *,
    parse_stream_chunk: Callable[[Any], dict[str, Any] | None],
    build_push_message: Callable[..., dict[str, Any]],
) -> None:
    for evt in events:
        if (
            is_evolution_approval_event(evt)
            or is_evolution_outcome_event(evt)
            or team_evolution_terminal_progress(evt) is not None
        ):
            continue
        try:
            parsed = parse_stream_chunk(evt)
            if parsed is None:
                continue
            await push_context.transport.send_push(
                build_push_message(
                    session_id=push_context.session_id,
                    request_id=request_id,
                    fallback_channel_id=push_context.channel_id,
                    payload=parsed,
                )
            )
        except Exception as exc:
            logger.warning(
                "Failed to push evolution progress: request_id=%s session_id=%s error=%s",
                request_id,
                push_context.session_id,
                exc,
            )
