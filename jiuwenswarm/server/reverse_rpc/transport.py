"""Transport abstraction for AgentServer Reverse RPC calls."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from jiuwenswarm.common.reverse_rpc.errors import ReverseRpcValidationError
from jiuwenswarm.common.reverse_rpc.models import ReverseRpcRoute


class ReverseRpcTransport(Protocol):
    async def send(
        self,
        message: dict[str, Any],
        route: ReverseRpcRoute,
    ) -> None: ...


class SingleGatewayReverseRpcTransport:
    """V1 transport backed by the existing AgentServer server-push callback."""

    def __init__(
        self,
        send_push: Callable[[dict[str, Any]], Awaitable[None] | None],
    ) -> None:
        self._send_push = send_push

    async def send(
        self,
        message: dict[str, Any],
        route: ReverseRpcRoute,
    ) -> None:
        # V1 intentionally uses the sole active Gateway connection.  The route
        # is preserved in the protocol so a connection-registry transport can
        # replace this implementation without changing callers.
        if route.gateway_id is not None:
            raise ReverseRpcValidationError(
                "Reverse RPC V1 cannot route by gateway_id; "
                "only the sole active Gateway connection is supported"
            )
        result = self._send_push(message)
        if inspect.isawaitable(result):
            await result
