"""Dataclasses used to carry one Jiuwen agent invocation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


INVOCATION_CONTEXT_VERSION = 1


@dataclass(frozen=True, slots=True)
class XiaoyiInvocationContext:
    """Xiaoyi routing fields associated with an invocation.

    ``scheduled_device`` and ``cron`` are intentionally opaque dictionaries:
    the shape belongs to the existing Device RPC/scheduled-device contract and
    must not be duplicated or changed here.
    """

    root_session_id: str | None = None
    params_session_id: str | None = None
    task_id: str | None = None
    message_id: str | None = None
    device_id: str | None = None

    scheduled_device: dict[str, Any] | None = None
    cron: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class InvocationContext:
    """Identity and routing data for a single agent execution."""

    version: int

    invocation_id: str
    request_id: str

    session_id: str | None
    channel_id: str
    chat_id: str | None

    xiaoyi: XiaoyiInvocationContext | None = None

    metadata: dict[str, Any] = field(default_factory=dict)
