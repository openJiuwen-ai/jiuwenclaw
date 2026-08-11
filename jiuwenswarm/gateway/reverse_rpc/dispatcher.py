"""Generic Gateway request, cancellation, timeout, and response lifecycle."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from jiuwenswarm.common.reverse_rpc.codec import cancel_from_wire, request_from_wire
from jiuwenswarm.common.reverse_rpc.constants import (
    ERROR_CANCELLED,
    ERROR_INTERNAL,
    ERROR_INVALID_REQUEST,
    ERROR_METHOD_NOT_FOUND,
    ERROR_ROUTE_NOT_FOUND,
    ERROR_TIMEOUT,
    ERROR_UNSUPPORTED_VERSION,
    REVERSE_RPC_CANCEL_KIND,
    REVERSE_RPC_REQUEST_KIND,
    REVERSE_RPC_VERSION,
)
from jiuwenswarm.common.reverse_rpc.errors import ReverseRpcValidationError
from jiuwenswarm.common.reverse_rpc.models import (
    ReverseRpcErrorPayload,
    ReverseRpcOrigin,
    ReverseRpcRequest,
    ReverseRpcResponse,
    ReverseRpcRoute,
)
from jiuwenswarm.gateway.reverse_rpc.errors import CapabilityError
from jiuwenswarm.gateway.reverse_rpc.registry import (
    CapabilityRegistry,
    CapabilitySpec,
    ReverseRpcCapabilityContext,
)
from jiuwenswarm.gateway.reverse_rpc.transport import ReverseRpcResponseTransport

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ReverseRpcExecution:
    request: ReverseRpcRequest
    spec: CapabilitySpec
    task: asyncio.Task[None]
    connection_generation: int


class ReverseRpcDispatcher:
    def __init__(
        self,
        registry: CapabilityRegistry,
        response_transport: ReverseRpcResponseTransport,
        before_execute: Callable[
            [ReverseRpcCapabilityContext, str, dict[str, Any]],
            Awaitable[None] | None,
        ]
        | None = None,
    ) -> None:
        self._registry = registry
        self._response_transport = response_transport
        self._executions: dict[str, ReverseRpcExecution] = {}
        self._connection_generation = 0
        self._before_execute = before_execute

    @property
    def execution_count(self) -> int:
        return len(self._executions)

    async def handle(self, wire: dict[str, Any]) -> None:
        response_kind = str(wire.get("response_kind") or "")
        if response_kind == REVERSE_RPC_REQUEST_KIND:
            await self._handle_request(wire)
            return
        if response_kind == REVERSE_RPC_CANCEL_KIND:
            await self._handle_cancel(wire)
            return
        raise ReverseRpcValidationError(
            f"unsupported Reverse RPC response_kind: {response_kind}"
        )

    async def _handle_request(self, wire: dict[str, Any]) -> None:
        try:
            request = request_from_wire(wire)
        except ReverseRpcValidationError as exc:
            logger.warning(
                "[REVERSE_RPC] phase=GATEWAY_REQUEST_INVALID",
                exc_info=True,
            )
            await self._try_send_invalid_request_error(wire, exc)
            return

        if request.rpc_id in self._executions:
            await self._send_error(
                request,
                ERROR_INVALID_REQUEST,
                "duplicate in-flight rpc_id",
            )
            return

        spec = self._registry.resolve(request.method)
        if spec is None:
            await self._send_error(
                request,
                ERROR_METHOD_NOT_FOUND,
                f"Reverse RPC method is not registered: {request.method}",
            )
            return
        if request.route.is_empty and not spec.allow_empty_route:
            await self._send_error(
                request,
                ERROR_ROUTE_NOT_FOUND,
                "Reverse RPC route is required",
            )
            return

        generation = self._connection_generation
        task = asyncio.create_task(self._run_request(request, spec, generation))
        self._executions[request.rpc_id] = ReverseRpcExecution(
            request=request,
            spec=spec,
            task=task,
            connection_generation=generation,
        )
        logger.info(
            "[REVERSE_RPC] phase=GATEWAY_EXECUTION_STARTED rpc_id=%s method=%s "
            "execution_id=%s request_id=%s session_id=%s channel_id=%s",
            request.rpc_id,
            request.method,
            request.origin.execution_id,
            request.origin.request_id,
            request.origin.session_id,
            request.origin.channel_id,
        )
        await task

    async def _run_request(
        self,
        request: ReverseRpcRequest,
        spec: CapabilitySpec,
        generation: int,
    ) -> None:
        timeout_seconds = min(
            request.timeout_ms / 1000.0,
            spec.max_timeout_seconds,
        )
        context = ReverseRpcCapabilityContext(
            rpc_id=request.rpc_id,
            origin=request.origin,
            route=request.route,
            timeout_seconds=timeout_seconds,
            connection_generation=generation,
        )
        try:
            result = await asyncio.wait_for(
                self._execute_handler(context, request, spec),
                timeout=timeout_seconds,
            )
            response = ReverseRpcResponse(
                version=REVERSE_RPC_VERSION,
                rpc_id=request.rpc_id,
                ok=True,
                result=result,
            )
        except CapabilityError as exc:
            response = self._error_response(
                request,
                exc.code,
                exc.message,
                retryable=exc.retryable,
                details=exc.details,
            )
        except asyncio.TimeoutError:
            response = self._error_response(
                request,
                ERROR_TIMEOUT,
                "Gateway capability execution timed out",
            )
        except asyncio.CancelledError:
            response = self._error_response(
                request,
                ERROR_CANCELLED,
                "Gateway capability execution was cancelled",
            )
        except Exception:
            logger.exception(
                "[REVERSE_RPC] phase=GATEWAY_EXECUTION_FAILED rpc_id=%s method=%s",
                request.rpc_id,
                request.method,
            )
            response = self._error_response(
                request,
                ERROR_INTERNAL,
                "Gateway capability execution failed",
            )
        finally:
            self._executions.pop(request.rpc_id, None)

        if generation != self._connection_generation:
            logger.info(
                "[REVERSE_RPC] phase=GATEWAY_RESPONSE_DROPPED rpc_id=%s "
                "reason=stale_connection_generation",
                request.rpc_id,
            )
            return
        await self._response_transport.send(response, request)
        logger.info(
            "[REVERSE_RPC] phase=GATEWAY_RESPONSE_SENT rpc_id=%s method=%s status=%s",
            request.rpc_id,
            request.method,
            "ok" if response.ok else response.error.code if response.error else "error",
        )

    async def _execute_handler(
        self,
        context: ReverseRpcCapabilityContext,
        request: ReverseRpcRequest,
        spec: CapabilitySpec,
    ) -> Any:
        if self._before_execute is not None:
            hook_result = self._before_execute(
                context,
                request.method,
                request.payload,
            )
            if inspect.isawaitable(hook_result):
                await hook_result
        return await spec.handler.handle(context, request.payload)

    async def _handle_cancel(self, wire: dict[str, Any]) -> None:
        try:
            cancel = cancel_from_wire(wire)
        except ReverseRpcValidationError:
            logger.warning(
                "[REVERSE_RPC] phase=GATEWAY_CANCEL_INVALID",
                exc_info=True,
            )
            return
        execution = self._executions.get(cancel.rpc_id)
        if execution is None:
            logger.info(
                "[REVERSE_RPC] phase=GATEWAY_CANCEL_IGNORED rpc_id=%s reason=unknown",
                cancel.rpc_id,
            )
            return
        if not execution.spec.supports_cancel:
            logger.info(
                "[REVERSE_RPC] phase=GATEWAY_CANCEL_UNSUPPORTED rpc_id=%s method=%s",
                cancel.rpc_id,
                execution.request.method,
            )
            return
        execution.task.cancel()

    async def on_agent_disconnect(self, exc: BaseException | None = None) -> None:
        del exc
        self._connection_generation += 1
        tasks = [
            execution.task
            for execution in self._executions.values()
            if execution.spec.cancel_on_disconnect and not execution.task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _send_error(
        self,
        request: ReverseRpcRequest,
        code: str,
        message: str,
    ) -> None:
        await self._response_transport.send(
            self._error_response(request, code, message),
            request,
        )

    async def _try_send_invalid_request_error(
        self,
        wire: dict[str, Any],
        exc: ReverseRpcValidationError,
    ) -> None:
        body = wire.get("body")
        if not isinstance(body, dict):
            return
        rpc_id = str(body.get("rpc_id") or "").strip()
        if not rpc_id:
            return
        origin_raw = body.get("origin")
        route_raw = body.get("route")
        try:
            origin = ReverseRpcOrigin.from_dict(origin_raw)
        except ReverseRpcValidationError:
            origin = ReverseRpcOrigin()
        try:
            route = ReverseRpcRoute.from_dict(route_raw)
        except ReverseRpcValidationError:
            route = ReverseRpcRoute()
        version = body.get("version")
        code = (
            ERROR_UNSUPPORTED_VERSION
            if isinstance(version, int)
            and not isinstance(version, bool)
            and version != REVERSE_RPC_VERSION
            else ERROR_INVALID_REQUEST
        )
        request = ReverseRpcRequest(
            version=REVERSE_RPC_VERSION,
            rpc_id=rpc_id,
            method=str(body.get("method") or "invalid"),
            payload={},
            timeout_ms=1,
            origin=origin,
            route=route,
        )
        await self._send_error(request, code, str(exc))

    @staticmethod
    def _error_response(
        request: ReverseRpcRequest,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> ReverseRpcResponse:
        return ReverseRpcResponse(
            version=REVERSE_RPC_VERSION,
            rpc_id=request.rpc_id,
            ok=False,
            error=ReverseRpcErrorPayload(
                code=code,
                message=message,
                retryable=retryable,
                details=details,
            ),
        )
