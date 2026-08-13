"""Optional host capabilities injected into the transport-neutral Runtime.

The process CLI deliberately leaves these capabilities unset.  AgentServer may
install a push handler for its service lifetime without making Runtime import or
discover a Server/Gateway singleton.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

RuntimePushHandler = Callable[[dict[str, Any]], Awaitable[None]]
RuntimeWakeHandler = Callable[[Any], Awaitable[None]]
RuntimeXiaoyiChannelProvider = Callable[[str], Any]

_runtime_push_handler: RuntimePushHandler | None = None
_runtime_wake_handler: RuntimeWakeHandler | None = None
_runtime_xiaoyi_channel_provider: RuntimeXiaoyiChannelProvider | None = None


def install_runtime_push_handler(
    handler: RuntimePushHandler,
) -> RuntimePushHandler | None:
    """Install a host-owned push handler and return the previous handler."""
    global _runtime_push_handler

    previous = _runtime_push_handler
    _runtime_push_handler = handler
    return previous


def restore_runtime_push_handler(
    handler: RuntimePushHandler,
    previous: RuntimePushHandler | None,
) -> None:
    """Restore a previous handler only when ``handler`` still owns the slot."""
    global _runtime_push_handler

    if _runtime_push_handler == handler:
        _runtime_push_handler = previous


async def send_runtime_push(message: dict[str, Any]) -> bool:
    """Send through the optional host adapter; return False when unavailable."""
    handler = _runtime_push_handler
    if handler is None:
        return False
    result = handler(message)
    if inspect.isawaitable(result):
        await result
    return True


class RuntimeHostPushTransport:
    """Structural push transport backed only by an explicitly installed host."""

    async def send_push(self, message: dict[str, Any]) -> None:
        await send_runtime_push(message)


def install_runtime_wake_handler(
    handler: RuntimeWakeHandler,
) -> RuntimeWakeHandler | None:
    """Install a host-owned wake dispatcher and return its predecessor."""
    global _runtime_wake_handler

    previous = _runtime_wake_handler
    _runtime_wake_handler = handler
    return previous


def restore_runtime_wake_handler(
    handler: RuntimeWakeHandler,
    previous: RuntimeWakeHandler | None,
) -> None:
    global _runtime_wake_handler

    if _runtime_wake_handler == handler:
        _runtime_wake_handler = previous


async def send_runtime_wake(message: Any) -> bool:
    """Dispatch a wake request through an explicitly installed host adapter."""
    handler = _runtime_wake_handler
    if handler is None:
        return False
    result = handler(message)
    if inspect.isawaitable(result):
        await result
    return True


def install_runtime_xiaoyi_channel_provider(
    provider: RuntimeXiaoyiChannelProvider,
) -> RuntimeXiaoyiChannelProvider | None:
    """Install the optional Gateway-owned Xiaoyi channel lookup adapter."""
    global _runtime_xiaoyi_channel_provider

    previous = _runtime_xiaoyi_channel_provider
    _runtime_xiaoyi_channel_provider = provider
    return previous


def get_runtime_xiaoyi_channel(channel_id: str = "xiaoyi") -> Any:
    provider = _runtime_xiaoyi_channel_provider
    return provider(channel_id) if provider is not None else None


__all__ = [
    "RuntimePushHandler",
    "RuntimeHostPushTransport",
    "RuntimeWakeHandler",
    "RuntimeXiaoyiChannelProvider",
    "get_runtime_xiaoyi_channel",
    "install_runtime_wake_handler",
    "install_runtime_push_handler",
    "install_runtime_xiaoyi_channel_provider",
    "restore_runtime_wake_handler",
    "restore_runtime_push_handler",
    "send_runtime_push",
    "send_runtime_wake",
]
