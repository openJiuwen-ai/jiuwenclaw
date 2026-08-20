"""Capability-neutral AgentServer Reverse RPC client."""

from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from jiuwenswarm.common.reverse_rpc.codec import build_cancel_wire, build_request_wire
from jiuwenswarm.common.reverse_rpc.constants import REVERSE_RPC_VERSION
from jiuwenswarm.common.reverse_rpc.errors import (
    ReverseRpcRemoteError,
    ReverseRpcTimeoutError,
)
from jiuwenswarm.common.reverse_rpc.models import (
    ReverseRpcCancel,
    ReverseRpcOrigin,
    ReverseRpcRequest,
    ReverseRpcResponse,
    ReverseRpcRoute,
)
from jiuwenswarm.server.reverse_rpc.pending_registry import (
    PendingReverseRpc,
    ReverseRpcPendingRegistry,
)
from jiuwenswarm.server.reverse_rpc.transport import ReverseRpcTransport

logger = logging.getLogger(__name__)
_CANCEL_SEND_TIMEOUT_SECONDS = 1.0


class ReverseRpcClient:
    def __init__(
        self,
        *,
        registry: ReverseRpcPendingRegistry | None = None,
        transport: ReverseRpcTransport | None = None,
        before_call: Callable[[ReverseRpcRequest], Awaitable[None] | None]
        | None = None,
    ) -> None:
        self._registry = registry or ReverseRpcPendingRegistry()
        self._transport = transport
        self._before_call = before_call

    @property
    def registry(self) -> ReverseRpcPendingRegistry:
        return self._registry

    def set_transport(self, transport: ReverseRpcTransport) -> None:
        self._transport = transport

    def set_before_call_hook(
        self,
        hook: Callable[[ReverseRpcRequest], Awaitable[None] | None] | None,
    ) -> None:
        self._before_call = hook

    async def call(
        self,
        *,
        method: str,
        payload: dict[str, Any],
        origin: ReverseRpcOrigin,
        route: ReverseRpcRoute,
        timeout: float,
        remote_cancel: bool = True,
    ) -> Any:
        if self._transport is None:
            raise RuntimeError("Reverse RPC transport is not configured")
        if timeout <= 0:
            raise ValueError("timeout must be positive")

        loop = asyncio.get_running_loop()
        rpc_id = f"rrpc_{uuid.uuid4().hex}"
        request = ReverseRpcRequest.from_dict(
            {
                "version": REVERSE_RPC_VERSION,
                "rpc_id": rpc_id,
                "method": method,
                "payload": payload,
                "timeout_ms": max(1, int(timeout * 1000)),
                "origin": origin.to_dict(),
                "route": route.to_dict(),
            }
        )
        future: asyncio.Future[ReverseRpcResponse] = loop.create_future()
        if self._before_call is not None:
            hook_result = self._before_call(request)
            if inspect.isawaitable(hook_result):
                await hook_result
        self._registry.register(
            PendingReverseRpc(
                request=request,
                future=future,
                created_at=loop.time(),
            )
        )
        logger.info(
            "[REVERSE_RPC] phase=CLIENT_PENDING_ADDED rpc_id=%s method=%s "
            "execution_id=%s request_id=%s session_id=%s channel_id=%s",
            rpc_id,
            method,
            origin.execution_id,
            origin.request_id,
            origin.session_id,
            origin.channel_id,
        )

        try:
            await self._transport.send(build_request_wire(request), route)
            response = await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
            if not response.ok:
                assert response.error is not None
                raise ReverseRpcRemoteError(
                    response.error.code,
                    response.error.message,
                    retryable=response.error.retryable,
                    details=response.error.details,
                )
            return response.result
        except asyncio.TimeoutError as exc:
            logger.info(
                "[REVERSE_RPC] phase=CLIENT_TIMEOUT rpc_id=%s method=%s",
                rpc_id,
                method,
            )
            if remote_cancel:
                await self._try_send_cancel(request, "caller timeout")
            raise ReverseRpcTimeoutError(
                f"Reverse RPC timed out: method={method} timeout={timeout}s"
            ) from exc
        except asyncio.CancelledError:
            logger.info(
                "[REVERSE_RPC] phase=CLIENT_CANCEL rpc_id=%s method=%s",
                rpc_id,
                method,
            )
            if remote_cancel:
                await self._try_send_cancel(request, "caller cancelled")
            raise
        finally:
            self._registry.remove(rpc_id)
            if not future.done():
                future.cancel()
            logger.debug(
                "[REVERSE_RPC] phase=CLIENT_CLEANUP rpc_id=%s method=%s",
                rpc_id,
                method,
            )

    async def _try_send_cancel(
        self,
        request: ReverseRpcRequest,
        reason: str,
    ) -> None:
        transport = self._transport
        if transport is None:
            return
        cancel = ReverseRpcCancel(
            version=REVERSE_RPC_VERSION,
            rpc_id=request.rpc_id,
            reason=reason,
        )
        try:
            await asyncio.wait_for(
                transport.send(build_cancel_wire(cancel, request), request.route),
                timeout=_CANCEL_SEND_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "[REVERSE_RPC] phase=CLIENT_CANCEL_SEND_TIMEOUT rpc_id=%s "
                "method=%s timeout=%ss",
                request.rpc_id,
                request.method,
                _CANCEL_SEND_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError:
            logger.warning(
                "[REVERSE_RPC] phase=CLIENT_CANCEL_SEND_INTERRUPTED rpc_id=%s method=%s",
                request.rpc_id,
                request.method,
            )
        except Exception:
            logger.warning(
                "[REVERSE_RPC] phase=CLIENT_CANCEL_SEND_FAILED rpc_id=%s method=%s",
                request.rpc_id,
                request.method,
                exc_info=True,
            )

    def complete(self, response: ReverseRpcResponse) -> bool:
        return self._registry.complete(response)

    def fail(self, rpc_id: str, exc: BaseException) -> bool:
        return self._registry.fail(rpc_id, exc)

    def fail_all(self, exc: BaseException) -> None:
        self._registry.fail_all(exc)
