from __future__ import annotations

from typing import Any

from jiuwenclaw.schema.hook_event import GatewayHookEvents
from jiuwenclaw.schema.hooks_context import (
    GatewayLocalRpcRequestHookContext,
    GatewayLocalRpcResponseHookContext,
)

from jiuwenclaw.extensions.registry import ExtensionRegistry


def _get_extension_registry() -> ExtensionRegistry | None:
    try:
        return ExtensionRegistry.get_instance()
    except RuntimeError:
        return None


class LocalRpcHookDispatcher:
    def __init__(self) -> None:
        # 这是一份request信息的缓存，根据 ws_id和request_id 组成key。
        # 因为after的截断，从websocket里看不到这个响应对应哪个session、channel，需要通过此方式缓存
        # 提供了一些缓存清理的机制，但是对用法有些要求。例如ws连接生命周期结束时要清理掉所有该ws下的缓存。
        self._requests: dict[tuple[int, str], GatewayLocalRpcRequestHookContext] = {}

    @staticmethod
    def _key(ws: Any, request_id: str) -> tuple[int, str]:
        return id(ws), request_id

    async def before_request(
        self,
        ws: Any,
        *,
        request_id: str,
        channel_id: str,
        session_id: str | None,
        method: str,
        params: dict[str, Any],
        source: str,
        route: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ctx = GatewayLocalRpcRequestHookContext(
            request_id=request_id,
            channel_id=channel_id,
            session_id=session_id,
            method=method,
            params=params,
            source=source,
            route=route,
            metadata=dict(metadata or {}),
        )
        registry = _get_extension_registry()
        if registry is not None:
            await registry.trigger(GatewayHookEvents.BEFORE_LOCAL_RPC_REQUEST, ctx)
        self._requests[self._key(ws, request_id)] = ctx
        return ctx.params

    async def after_response(
        self,
        ws: Any,
        request_id: str,
        *,
        ok: bool,
        payload: dict[str, Any] | None,
        error: str | None,
        code: str | None,
    ) -> tuple[bool, dict[str, Any] | None, str | None, str | None]:
        request_ctx = self._requests.pop(self._key(ws, request_id), None)
        if request_ctx is None:
            return ok, payload, error, code

        response_ctx = GatewayLocalRpcResponseHookContext(
            request_id=request_id,
            channel_id=request_ctx.channel_id,
            session_id=request_ctx.session_id,
            method=request_ctx.method,
            ok=ok,
            payload=payload or {},
            error=error,
            code=code,
            source=request_ctx.source,
            route=request_ctx.route,
            metadata=dict(request_ctx.metadata or {}),
        )
        registry = _get_extension_registry()
        if registry is not None:
            await registry.trigger(GatewayHookEvents.AFTER_LOCAL_RPC_RESPONSE, response_ctx)
        return response_ctx.ok, response_ctx.payload, response_ctx.error, response_ctx.code


    def discard(self, ws: Any, request_id: str) -> None:
        self._requests.pop(self._key(ws, request_id), None)

    def clear_ws(self, ws: Any) -> None:
        ws_id = id(ws)
        stale_keys = [key for key in self._requests if key[0] == ws_id]
        for key in stale_keys:
            self._requests.pop(key, None)

    def clear(self) -> None:
        self._requests.clear()
