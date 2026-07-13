"""Timeout policy for Gateway -> AgentServer unary requests."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

AGENT_SERVER_TIMEOUT_CODE = "AGENT_SERVER_TIMEOUT"
AGENT_SERVER_TIMEOUT_ERROR = "AgentServer request timed out"

_TUI_CHANNEL_ID = "tui"
_TUI_DEFAULT_UNARY_TIMEOUT_SECONDS = 25.0
_TUI_MAX_UNARY_TIMEOUT_SECONDS = 55.0
_TUI_CLIENT_TIMEOUT_GRACE_SECONDS = 5.0
_TUI_EXTENDED_UNARY_METHOD_PREFIXES = (
    "permissions.tools.",
    "permissions.rules.",
)


class AgentRequestTimeoutError(TimeoutError):
    """Raised when Gateway stops waiting for a slow AgentServer unary request."""

    def __init__(self, message: str = AGENT_SERVER_TIMEOUT_ERROR) -> None:
        super().__init__(message)
        self.code = AGENT_SERVER_TIMEOUT_CODE


def coerce_client_timeout_ms(value: Any) -> int | None:
    """Parse client-provided timeout metadata without accepting invalid values."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        timeout_ms = value
    elif isinstance(value, float) and value.is_integer():
        timeout_ms = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        timeout_ms = int(value.strip())
    else:
        return None
    return timeout_ms if timeout_ms > 0 else None


def resolve_agent_request_timeout_seconds(
    *,
    channel_id: str | None,
    method: str | None,
    is_stream: bool,
    client_timeout_ms: Any = None,
) -> float | None:
    """Return a Gateway-side wait limit, or None to keep AgentClient's default."""
    if is_stream or channel_id != _TUI_CHANNEL_ID:
        return None

    parsed_client_timeout_ms = coerce_client_timeout_ms(client_timeout_ms)
    if parsed_client_timeout_ms is not None:
        client_timeout_seconds = parsed_client_timeout_ms / 1000.0
        timeout_seconds = max(
            1.0,
            client_timeout_seconds - _TUI_CLIENT_TIMEOUT_GRACE_SECONDS,
        )
        return min(timeout_seconds, _TUI_MAX_UNARY_TIMEOUT_SECONDS)

    method_value = str(method or "")
    if method_value.startswith(_TUI_EXTENDED_UNARY_METHOD_PREFIXES):
        return _TUI_MAX_UNARY_TIMEOUT_SECONDS

    return _TUI_DEFAULT_UNARY_TIMEOUT_SECONDS


def request_timeout_from_envelope(env: Any) -> float | None:
    channel_context = getattr(env, "channel_context", None)
    if not isinstance(channel_context, dict):
        channel_context = {}
    metadata = getattr(env, "metadata", None)
    if not isinstance(metadata, dict):
        metadata = {}
    return resolve_agent_request_timeout_seconds(
        channel_id=getattr(env, "channel", None),
        method=getattr(env, "method", None),
        is_stream=bool(getattr(env, "is_stream", False)),
        client_timeout_ms=channel_context.get(
            "client_timeout_ms",
            metadata.get("client_timeout_ms"),
        ),
    )


async def send_agent_request_with_timeout(
    agent_client: Any,
    env: Any,
    *,
    label: str,
    timeout_seconds: float | None = None,
) -> Any:
    timeout = request_timeout_from_envelope(env) if timeout_seconds is None else timeout_seconds
    if timeout is None:
        return await agent_client.send_request(env)

    try:
        return await asyncio.wait_for(agent_client.send_request(env), timeout=timeout)
    except asyncio.TimeoutError as exc:
        logger.warning(
            "[%s] AgentServer unary request timed out: request_id=%s method=%s "
            "channel=%s timeout=%ss",
            label,
            getattr(env, "request_id", ""),
            getattr(env, "method", ""),
            getattr(env, "channel", ""),
            timeout,
        )
        raise AgentRequestTimeoutError() from exc
