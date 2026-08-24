# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Bounded WebSocket wire sending for AgentServer responses."""

from __future__ import annotations

import json
import logging
from typing import Any

from jiuwenswarm.common.e2a.constants import E2A_WIRE_SERVER_PUSH_KEY
from jiuwenswarm.common.e2a.wire_codec import (
    encode_agent_chunk_for_wire,
    encode_agent_response_for_wire,
)
from jiuwenswarm.common.schema.agent import AgentResponse, AgentResponseChunk
from jiuwenswarm.common.ws_limits import AGENT_WS_SEND_BUDGET_BYTES
from jiuwenswarm.common.ws_chunking import split_wire_payload_for_chunking

logger = logging.getLogger(__name__)

_ROUTING_KEYS = (
    "session_id",
    "task_id",
    "context_id",
    "correlation_id",
)


def _oversized_payload(actual_bytes: int) -> dict[str, Any]:
    return {
        "error": "AgentServer response exceeds WebSocket send budget",
        "code": "response_too_large",
        "actual_bytes": actual_bytes,
        "max_bytes": AGENT_WS_SEND_BUDGET_BYTES,
    }


def _build_oversized_fallback(
    wire: dict[str, Any], actual_bytes: int
) -> dict[str, Any]:
    request_id = str(wire.get("request_id") or "")
    response_id = str(wire.get("response_id") or request_id)
    channel_id = str(wire.get("channel") or "")
    sequence = int(wire.get("sequence") or 0)
    agent_ref = wire.get("agent_ref")
    payload = _oversized_payload(actual_bytes)

    if wire.get("is_stream"):
        payload["event_type"] = "chat.error"
        fallback = encode_agent_chunk_for_wire(
            AgentResponseChunk(
                request_id=request_id,
                channel_id=channel_id,
                payload=payload,
                is_complete=True,
                agent_ref=agent_ref,
            ),
            response_id=response_id,
            sequence=sequence,
        )
    elif wire.get("type") == "event":
        fallback = {
            "type": "event",
            "event": "response.error",
            "payload": payload,
        }
    else:
        fallback = encode_agent_response_for_wire(
            AgentResponse(
                request_id=request_id,
                channel_id=channel_id,
                ok=False,
                payload=payload,
                agent_ref=agent_ref,
            ),
            response_id=response_id,
            sequence=sequence,
        )

    for key in _ROUTING_KEYS:
        if wire.get(key) is not None:
            fallback[key] = wire[key]

    source_metadata = wire.get("metadata")
    if (
        isinstance(source_metadata, dict)
        and source_metadata.get(E2A_WIRE_SERVER_PUSH_KEY) is True
    ):
        metadata = dict(fallback.get("metadata") or {})
        metadata[E2A_WIRE_SERVER_PUSH_KEY] = True
        fallback["metadata"] = metadata

    return fallback


async def send_wire_payload(ws: Any, wire: dict[str, Any]) -> bool:
    """Send one bounded wire payload, replacing oversized data with an error.

    If the payload exceeds the send budget, it will be automatically split
    into chunks and sent as multiple messages. The receiver will reassemble
    the chunks into the original message.
    """
    serialized = json.dumps(wire, ensure_ascii=False)
    actual_bytes = len(serialized.encode("utf-8"))
    if actual_bytes <= AGENT_WS_SEND_BUDGET_BYTES:
        await ws.send(serialized)
        return True

    # Payload too large - try to chunk it
    _preview = serialized[:1000]
    if len(serialized) > 1000:
        _preview += "...(truncated)"
    logger.warning(
        "AgentServer WebSocket response too large, attempting chunking: "
        "request_id=%s session_id=%s channel=%s type=%s is_stream=%s "
        "response_kind=%s actual_bytes=%d max_bytes=%d preview=%s",
        wire.get("request_id"),
        wire.get("session_id"),
        wire.get("channel") or wire.get("channel_id"),
        wire.get("type"),
        wire.get("is_stream"),
        wire.get("response_kind"),
        actual_bytes,
        AGENT_WS_SEND_BUDGET_BYTES,
        _preview,
    )

    # Split into chunks
    chunks = split_wire_payload_for_chunking(wire, AGENT_WS_SEND_BUDGET_BYTES)

    # If chunking failed (still only 1 chunk), send error fallback
    if len(chunks) == 1:
        logger.error(
            "Chunking failed, sending error fallback: request_id=%s",
            wire.get("request_id"),
        )
        fallback = _build_oversized_fallback(wire, actual_bytes)
        fallback_json = json.dumps(fallback, ensure_ascii=False)
        fallback_bytes = len(fallback_json.encode("utf-8"))
        if fallback_bytes > AGENT_WS_SEND_BUDGET_BYTES:
            raise RuntimeError(
                "oversized fallback exceeds WebSocket send budget: "
                f"actual_bytes={fallback_bytes} "
                f"max_bytes={AGENT_WS_SEND_BUDGET_BYTES}"
            )
        await ws.send(fallback_json)
        return False

    # Send all chunks
    logger.info(
        "Sending chunked payload: request_id=%s chunks=%d",
        wire.get("request_id"),
        len(chunks),
    )
    for i, chunk in enumerate(chunks):
        chunk_json = json.dumps(chunk, ensure_ascii=False)
        chunk_bytes = len(chunk_json.encode("utf-8"))

        # Verify each chunk fits
        if chunk_bytes > AGENT_WS_SEND_BUDGET_BYTES:
            logger.error(
                "Chunk %d/%d exceeds budget: chunk_bytes=%d max_bytes=%d",
                i + 1,
                len(chunks),
                chunk_bytes,
                AGENT_WS_SEND_BUDGET_BYTES,
            )
            # Send error fallback for the entire message
            fallback = _build_oversized_fallback(wire, actual_bytes)
            fallback_json = json.dumps(fallback, ensure_ascii=False)
            await ws.send(fallback_json)
            return False

        await ws.send(chunk_json)

    logger.info(
        "Successfully sent chunked payload: request_id=%s chunks=%d",
        wire.get("request_id"),
        len(chunks),
    )
    return True
