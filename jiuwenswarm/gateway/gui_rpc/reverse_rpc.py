"""Xiaoyi GUI capability adapter for the generic Reverse RPC dispatcher."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from jiuwenswarm.common.gui_rpc.models import (
    GUI_RPC_REQUEST_MESSAGE_TYPE,
    GuiRpcRequest,
)
from jiuwenswarm.common.gui_rpc.reverse_rpc import (
    GuiReverseRpcPayload,
    GuiReverseRpcResult,
    XIAOYI_GUI_MAX_TIMEOUT_SECONDS,
    XIAOYI_GUI_REVERSE_RPC_METHOD,
)
from jiuwenswarm.common.reverse_rpc.constants import (
    ERROR_INVALID_REQUEST,
    ERROR_ROUTE_NOT_FOUND,
)
from jiuwenswarm.gateway.gui_rpc.executor import (
    GuiExecutionError,
    XiaoyiGuiExecutor,
)
from jiuwenswarm.gateway.reverse_rpc.errors import CapabilityError
from jiuwenswarm.gateway.reverse_rpc.registry import (
    CapabilityRegistry,
    CapabilitySpec,
    ReverseRpcCapabilityContext,
)
from jiuwenswarm.gateway.routing.keys import ChannelKey


class XiaoyiGuiChannelResolver:
    """Resolve a XiaoyiChannel from explicit generic routing information."""

    def __init__(self, channel_manager: Any) -> None:
        self._channel_manager = channel_manager

    def resolve(self, ctx: ReverseRpcCapabilityContext) -> Any:
        route = ctx.route
        if route.channel_id != "xiaoyi":
            raise CapabilityError(
                ERROR_ROUTE_NOT_FOUND,
                "Xiaoyi GUI Reverse RPC requires route.channel_id=xiaoyi",
            )

        if route.app_id:
            channel = self._channel_manager.get_by_key(
                ChannelKey("xiaoyi", route.app_id)
            )
        else:
            candidates = self._channel_manager.get_channels_by_id("xiaoyi")
            if len(candidates) != 1:
                raise CapabilityError(
                    ERROR_ROUTE_NOT_FOUND,
                    "Xiaoyi GUI route is ambiguous or unavailable",
                    details={"candidate_count": len(candidates)},
                )
            channel = candidates[0]

        if channel is None:
            raise CapabilityError(
                "CHANNEL_NOT_FOUND",
                "XiaoyiChannel is not active for the requested route",
            )
        if not bool(getattr(channel, "is_ready", False)):
            raise CapabilityError(
                "CHANNEL_NOT_READY",
                "XiaoyiChannel has no active device connection",
            )
        return channel


class XiaoyiGuiCapability:
    """Typed GUI business handler; lifecycle stays in the generic dispatcher."""

    def __init__(
        self,
        channel_manager: Any,
        executor: XiaoyiGuiExecutor | None = None,
    ) -> None:
        self._resolver = XiaoyiGuiChannelResolver(channel_manager)
        self._executor = executor or XiaoyiGuiExecutor()

    async def handle(
        self,
        ctx: ReverseRpcCapabilityContext,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            gui_payload = GuiReverseRpcPayload.from_dict(payload)
        except ValueError as exc:
            raise CapabilityError(ERROR_INVALID_REQUEST, str(exc)) from exc

        source_request_id = str(ctx.origin.request_id or "").strip()
        if not source_request_id:
            raise CapabilityError(
                ERROR_INVALID_REQUEST,
                "origin.request_id is required for Xiaoyi GUI",
            )

        channel = self._resolver.resolve(ctx)
        request = GuiRpcRequest(
            message_type=GUI_RPC_REQUEST_MESSAGE_TYPE,
            rpc_id=ctx.rpc_id,
            query=gui_payload.query,
            source_request_id=source_request_id,
            jiuwen_session_id=ctx.origin.session_id,
            xiaoyi_session_id=gui_payload.xiaoyi_session_id,
            xiaoyi_task_id=gui_payload.xiaoyi_task_id,
            xiaoyi_message_id=gui_payload.xiaoyi_message_id,
            device_id=gui_payload.device_id,
            deadline=time.time() + ctx.timeout_seconds,
        )
        try:
            result = await self._executor.execute(request, channel=channel)
        except GuiExecutionError as exc:
            if exc.error_code == "CANCELLED" and isinstance(
                exc.__cause__,
                asyncio.CancelledError,
            ):
                # Preserve task cancellation so the generic dispatcher can
                # distinguish caller cancel from its own execution timeout.
                raise asyncio.CancelledError from exc
            raise CapabilityError(
                exc.error_code,
                str(exc),
                details={"rpc_id": ctx.rpc_id},
            ) from exc
        return GuiReverseRpcResult(
            rpc_id=ctx.rpc_id,
            result=result,
        ).to_dict()


def register_xiaoyi_gui_reverse_rpc(
    registry: CapabilityRegistry,
    channel_manager: Any,
) -> None:
    """Register the GUI adapter explicitly at Gateway composition time."""

    registry.register(
        CapabilitySpec(
            method=XIAOYI_GUI_REVERSE_RPC_METHOD,
            handler=XiaoyiGuiCapability(channel_manager),
            supports_cancel=True,
            cancel_on_disconnect=True,
            max_timeout_seconds=XIAOYI_GUI_MAX_TIMEOUT_SECONDS,
        )
    )
