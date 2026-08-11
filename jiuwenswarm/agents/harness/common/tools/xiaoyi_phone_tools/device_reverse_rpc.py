"""Typed AgentServer client for Xiaoyi Device over generic Reverse RPC."""

from __future__ import annotations

import uuid
from typing import Any

from jiuwenswarm.common.device_rpc.models import (
    DeviceCommandContext,
    DeviceCommandResponse,
)
from jiuwenswarm.common.device_rpc.reverse_rpc import (
    DeviceReverseRpcPayload,
    DeviceReverseRpcResult,
    XIAOYI_DEVICE_REVERSE_RPC_METHOD,
)
from jiuwenswarm.common.reverse_rpc.errors import ReverseRpcRemoteError
from jiuwenswarm.common.reverse_rpc.models import ReverseRpcOrigin, ReverseRpcRoute
from jiuwenswarm.server.reverse_rpc import ReverseRpcClient, get_reverse_rpc_client


def _optional_metadata_text(metadata: dict[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class XiaoyiDeviceReverseRpcClient:
    """Business adapter; the generic client remains unaware of Device models."""

    def __init__(self, client: ReverseRpcClient | None = None) -> None:
        self._client = client or get_reverse_rpc_client()

    async def call(
        self,
        *,
        intent_name: str,
        command: dict[str, Any],
        context: DeviceCommandContext,
        timeout: float = 60.0,
    ) -> DeviceCommandResponse:
        operation_id = f"xiaoyi_op_{uuid.uuid4().hex}"
        payload = DeviceReverseRpcPayload(
            operation_id=operation_id,
            intent_name=intent_name,
            command=dict(command),
            context=context,
        )
        metadata = context.metadata if isinstance(context.metadata, dict) else {}
        origin = ReverseRpcOrigin(
            execution_id=_optional_metadata_text(metadata, "invocation_id"),
            request_id=context.source_request_id,
            session_id=context.jiuwen_session_id,
            channel_id=context.channel_id,
        )
        route = ReverseRpcRoute(
            channel_id="xiaoyi",
            app_id=_optional_metadata_text(metadata, "app_id"),
            binding_id=_optional_metadata_text(metadata, "binding_id"),
        )
        try:
            raw_result = await self._client.call(
                method=XIAOYI_DEVICE_REVERSE_RPC_METHOD,
                payload=payload.to_dict(),
                origin=origin,
                route=route,
                timeout=timeout,
                remote_cancel=False,
            )
        except ReverseRpcRemoteError as exc:
            details = exc.details if isinstance(exc.details, dict) else {}
            return DeviceCommandResponse(
                rpc_id=str(details.get("rpc_id") or ""),
                operation_id=str(details.get("operation_id") or operation_id),
                ok=False,
                error_code=exc.code,
                error_message=exc.message,
            )

        result = DeviceReverseRpcResult.from_dict(raw_result)
        if result.operation_id != operation_id:
            raise RuntimeError(
                "Device Reverse RPC operation_id mismatch: "
                f"expected={operation_id} actual={result.operation_id}"
            )
        return DeviceCommandResponse(
            rpc_id=result.rpc_id,
            operation_id=result.operation_id,
            ok=True,
            result=result.result,
        )


_device_reverse_rpc_client = XiaoyiDeviceReverseRpcClient()


def get_xiaoyi_device_reverse_rpc_client() -> XiaoyiDeviceReverseRpcClient:
    return _device_reverse_rpc_client
