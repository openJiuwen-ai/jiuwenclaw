"""Xiaoyi Device capability adapter for the generic Reverse RPC dispatcher."""

from __future__ import annotations

from typing import Any

from jiuwenswarm.common.device_rpc.models import DeviceCommandRequest
from jiuwenswarm.common.device_rpc.reverse_rpc import (
    DeviceReverseRpcPayload,
    DeviceReverseRpcResult,
    XIAOYI_DEVICE_MAX_TIMEOUT_SECONDS,
    XIAOYI_DEVICE_REVERSE_RPC_METHOD,
)
from jiuwenswarm.common.reverse_rpc.constants import (
    ERROR_INVALID_REQUEST,
    ERROR_ROUTE_NOT_FOUND,
)
from jiuwenswarm.gateway.gateway_push.xiaoyi_device_command_handler import (
    execute_device_command_request,
)
from jiuwenswarm.gateway.reverse_rpc.errors import CapabilityError
from jiuwenswarm.gateway.reverse_rpc.registry import (
    CapabilityRegistry,
    CapabilitySpec,
    ReverseRpcCapabilityContext,
)
from jiuwenswarm.gateway.routing.keys import ChannelKey


class XiaoyiDeviceChannelResolver:
    """Resolve a XiaoyiChannel without relying on process-global latest state."""

    def __init__(self, channel_manager: Any) -> None:
        self._channel_manager = channel_manager

    def resolve(self, ctx: ReverseRpcCapabilityContext) -> Any:
        route = ctx.route
        if route.channel_id != "xiaoyi":
            raise CapabilityError(
                ERROR_ROUTE_NOT_FOUND,
                "Xiaoyi Device Reverse RPC requires route.channel_id=xiaoyi",
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
                    "Xiaoyi Device route is ambiguous or unavailable",
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


class XiaoyiDeviceCapability:
    """Typed Device business handler; generic lifecycle stays in the dispatcher."""

    def __init__(self, channel_manager: Any) -> None:
        self._resolver = XiaoyiDeviceChannelResolver(channel_manager)

    async def handle(
        self,
        ctx: ReverseRpcCapabilityContext,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            device_payload = DeviceReverseRpcPayload.from_dict(payload)
        except ValueError as exc:
            raise CapabilityError(ERROR_INVALID_REQUEST, str(exc)) from exc

        channel = self._resolver.resolve(ctx)
        request = DeviceCommandRequest(
            rpc_id=ctx.rpc_id,
            operation_id=device_payload.operation_id,
            intent_name=device_payload.intent_name,
            command=device_payload.command,
            context=device_payload.context,
            timeout_seconds=ctx.timeout_seconds,
        )
        response = await execute_device_command_request(request, channel)
        if not response.ok:
            raise CapabilityError(
                response.error_code or "DEVICE_EXECUTION_FAILED",
                response.error_message or "Device command failed",
                details={
                    "rpc_id": response.rpc_id,
                    "operation_id": response.operation_id,
                },
            )
        return DeviceReverseRpcResult(
            rpc_id=response.rpc_id,
            operation_id=response.operation_id,
            result=response.result or {},
        ).to_dict()


def register_xiaoyi_device_reverse_rpc(
    registry: CapabilityRegistry,
    channel_manager: Any,
) -> None:
    """Register the Device adapter explicitly at Gateway composition time."""

    registry.register(
        CapabilitySpec(
            method=XIAOYI_DEVICE_REVERSE_RPC_METHOD,
            handler=XiaoyiDeviceCapability(channel_manager),
            supports_cancel=False,
            cancel_on_disconnect=True,
            max_timeout_seconds=XIAOYI_DEVICE_MAX_TIMEOUT_SECONDS,
        )
    )
