# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""LLM request/response boundary tracing for debugging.

Tracing runs only when the ``jiuwenswarm`` logger is at **DEBUG**
(e.g. ``LOG_LEVEL=DEBUG``). Lines use ``logger.debug``.

``log_chat_final`` records the user-facing chat.final boundary without
logging final content (PR !1147 / commit f0a01b03).
"""

from __future__ import annotations

import logging
import os
from contextvars import ContextVar

logger = logging.getLogger("jiuwenswarm")

LLM_TRACE_SESSION_ID: ContextVar[str] = ContextVar("llm_trace_session_id", default="")
LLM_TRACE_REQUEST_ID: ContextVar[str] = ContextVar("llm_trace_request_id", default="")
LLM_TRACE_ITERATION: ContextVar[int | None] = ContextVar("llm_trace_iteration", default=None)
LLM_TRACE_MODEL_NAME: ContextVar[str] = ContextVar("llm_trace_model_name", default="")


def _max_part_bytes() -> int:
    for name in ("JIUWENSWARM_LLM_IO_TRACE_MAX_PART", "JIUWENCLAW_LLM_IO_TRACE_MAX_PART"):
        raw = (os.getenv(name) or "").strip()
        if not raw:
            continue
        try:
            return max(512, int(raw, 10))
        except ValueError:
            continue
    return 8192


def _llm_trace_active() -> bool:
    """Emit trace when DEBUG is on for the jiuwenswarm logger."""
    return logger.isEnabledFor(logging.DEBUG)


def _trace_header(
    *,
    session_id: str,
    request_id: str,
    iteration: int | None,
    model_name: str,
    event: str,
) -> str:
    it = "" if iteration is None else str(iteration)
    return (
        f"[LLM_IO_TRACE] event={event} session_id={session_id!r} "
        f"request_id={request_id!r} iteration={it} model_name={model_name!r}"
    )


def _log_body_parts(header: str, body: str) -> None:
    max_part = _max_part_bytes()
    if len(body) <= max_part:
        logger.debug("%s body=%s", header, body)
        return
    total = (len(body) + max_part - 1) // max_part
    for i in range(total):
        chunk = body[(i * max_part):(i + 1) * max_part]
        logger.debug("%s body_part=%s/%s body=%s", header, i + 1, total, chunk)


def log_chat_final(
    *,
    session_id: str,
    request_id: str,
    iteration: int | None,
    model_name: str,
) -> None:
    """Record the user-facing chat.final boundary without logging final content."""
    if not _llm_trace_active():
        return
    header = _trace_header(
        session_id=session_id,
        request_id=request_id,
        iteration=iteration,
        model_name=model_name,
        event="chat.final",
    )
    _log_body_parts(header, "")
