# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Request-scoped accounting for auxiliary LLM calls."""

from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

AuxLlmUsageSink = Callable[[Any], Awaitable[None]]
_AUX_LLM_USAGE_SINK: ContextVar[AuxLlmUsageSink | None] = ContextVar(
    "jiuwenclaw_aux_llm_usage_sink",
    default=None,
)


def bind_aux_llm_usage_sink(sink: AuxLlmUsageSink) -> Token:
    """Bind the adapter-owned sink for the current request context."""
    return _AUX_LLM_USAGE_SINK.set(sink)


def reset_aux_llm_usage_sink(token: Token) -> None:
    _AUX_LLM_USAGE_SINK.reset(token)


def _serialize_llm_usage(usage_metadata: Any) -> dict[str, Any]:
    if isinstance(usage_metadata, dict):
        return dict(usage_metadata)
    for method_name in ("model_dump", "dict"):
        serializer = getattr(usage_metadata, method_name, None)
        if not callable(serializer):
            continue
        try:
            payload = serializer()
        except Exception:  # noqa: BLE001 -- try the next representation
            continue
        if isinstance(payload, dict):
            return payload
    result: dict[str, Any] = {}
    for key in (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cache_tokens",
        "cache_read_input_tokens",
        "input_cost",
        "output_cost",
        "total_cost",
    ):
        value = getattr(usage_metadata, key, None)
        if value is not None:
            result[key] = value
    return result


async def emit_llm_usage_to_session(session: Any, usage_metadata: Any) -> None:
    """Report auxiliary usage to the active request, falling back to the session stream."""
    if not usage_metadata:
        return

    sink = _AUX_LLM_USAGE_SINK.get()
    if sink is not None:
        try:
            await sink(usage_metadata)
            return
        except Exception:  # noqa: BLE001 -- retain the legacy stream fallback
            logger.warning("[llm_usage] request sink failed", exc_info=True)

    if session is None:
        return
    payload = _serialize_llm_usage(usage_metadata)
    if not payload:
        return
    try:
        from openjiuwen.core.session.stream import OutputSchema  # type: ignore

        await session.write_stream(
            OutputSchema(
                type="llm_usage",
                index=0,
                payload={"usage_metadata": payload},
            )
        )
    except Exception:  # noqa: BLE001 -- accounting must not break the agent result
        logger.warning("[llm_usage] auxiliary usage.stream_failed", exc_info=True)


__all__ = [
    "AuxLlmUsageSink",
    "bind_aux_llm_usage_sink",
    "emit_llm_usage_to_session",
    "reset_aux_llm_usage_sink",
]
