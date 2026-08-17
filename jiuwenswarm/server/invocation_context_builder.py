"""Build and adapt explicit invocation context at the AgentServer boundary."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from jiuwenswarm.common.invocation_context.codec import attach_invocation_context
from jiuwenswarm.common.invocation_context.models import (
    INVOCATION_CONTEXT_VERSION,
    InvocationContext,
)
from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.server.xiaoyi_invocation import (
    build_xiaoyi_invocation_extension,
    build_xiaoyi_trace_context,
)

logger = logging.getLogger(__name__)


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def build_invocation_context(request: AgentRequest) -> InvocationContext:
    """Construct one invocation identity from an incoming ``AgentRequest``.

    Platform-specific request parsing is delegated to its private adapter.
    """

    if not isinstance(request, AgentRequest):
        raise TypeError("request must be an AgentRequest")

    request_id = _first_text(request.request_id)
    if request_id is None:
        raise ValueError("request_id is required to build InvocationContext")
    channel_id = _first_text(request.channel_id)
    if channel_id is None:
        raise ValueError("channel_id is required to build InvocationContext")

    context = InvocationContext(
        version=INVOCATION_CONTEXT_VERSION,
        invocation_id=f"inv_{uuid.uuid4().hex}",
        request_id=request_id,
        session_id=_first_text(request.session_id),
        channel_id=channel_id,
        chat_id=_first_text(request.chat_id),
        trace=build_xiaoyi_trace_context(request),
        metadata=build_xiaoyi_invocation_extension(request),
    )
    logger.info(
        "[INVOCATION_CTX] BUILT invocation_id=%s request_id=%s session_id=%s channel_id=%s",
        context.invocation_id,
        context.request_id,
        context.session_id,
        context.channel_id,
    )
    return context


__all__ = [
    "attach_invocation_context",
    "build_invocation_context",
]
