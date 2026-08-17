"""Dataclasses used to carry one Jiuwen agent invocation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


INVOCATION_CONTEXT_VERSION = 1
TRACE_CONTEXT_VERSION = 1


@dataclass(frozen=True, slots=True)
class TraceContext:
    """Platform-neutral trace identity approved for outbound propagation."""

    version: int
    trace_id: str
    conversation_id: str | None = None
    interaction_id: str | None = None


@dataclass(frozen=True, slots=True)
class InvocationContext:
    """Identity and routing data for a single agent execution."""

    version: int

    invocation_id: str
    request_id: str

    session_id: str | None
    channel_id: str
    chat_id: str | None

    trace: TraceContext | None = None

    metadata: dict[str, Any] = field(default_factory=dict)
