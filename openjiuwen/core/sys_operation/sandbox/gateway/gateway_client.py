# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from typing import Any, AsyncIterator, Optional

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.sys_operation.config import GatewayInvokeRequest, SandboxCreateRequest, SandboxGatewayConfig
from openjiuwen.core.sys_operation.sandbox.gateway.gateway import SandboxGateway, GatewayResponse, SandboxEndpoint


class SandboxGatewayClient:
    """Client wrapper for sandbox gateway — supports both endpoint resolution and full-chain invoke."""

    def __init__(
            self,
            *,
            config: SandboxGatewayConfig,
            isolation_key: Optional[str],
            gateway: Optional[SandboxGateway] = None
    ):
        self._config = config
        self._isolation_key = isolation_key
        self._gateway = gateway or SandboxGateway.get_instance()

    # ── Full-chain routing API ──

    async def invoke(self, op_type: str, method: str, **params) -> Any:
        """Send an invoke request through the gateway full-chain routing."""
        request = GatewayInvokeRequest(
            op_type=op_type, method=method, params=params, isolation_key=self._isolation_key,
        )
        response = await self._gateway.handle_request(self._config, request)
        self._raise_if_failed(response)
        return response.data

    async def invoke_stream(self, op_type: str, method: str, **params) -> AsyncIterator:
        """Send a streaming invoke request through the gateway full-chain routing."""
        request = GatewayInvokeRequest(
            op_type=op_type, method=method, params=params, isolation_key=self._isolation_key,
        )
        stream = await self._gateway.handle_stream_request(self._config, request)
        async for item in stream:
            yield item

    # ── Legacy endpoint-only API (kept for backward compatibility) ──

    async def get_endpoint(self) -> SandboxEndpoint:
        request = SandboxCreateRequest(isolation_key=self._isolation_key, config=self._config)
        response = await self._gateway.get_sandbox(request=request)
        self._raise_if_failed(response)

        endpoint = response.data
        if isinstance(endpoint, SandboxEndpoint):
            return endpoint
        if isinstance(endpoint, dict):
            return SandboxEndpoint(**endpoint)
        raise TypeError(f"Invalid endpoint payload: {type(endpoint).__name__}")

    @staticmethod
    async def release(isolation_key: str, on_stop: str = "delete") -> None:
        """Static release method: Only the key is required to notify the gateway to reclaim the resources."""
        gateway = SandboxGateway.get_instance()
        response = await gateway.release_sandbox(isolation_key, on_stop=on_stop)
        SandboxGatewayClient._raise_if_failed(response)

    @staticmethod
    def _raise_if_failed(response: GatewayResponse) -> None:
        legacy_success = getattr(response, "success", None)
        if legacy_success is not None:
            success = bool(legacy_success)
        else:
            success = getattr(response, "code", 1) == 0

        if success:
            return

        error_msg = getattr(response, "error", None) or getattr(response, "message", "unknown error")

        raise build_error(
            status=StatusCode.SYS_OPERATION_SANDBOX_GATEWAY_ERROR,
            operation=f"gateway_{getattr(response, 'op_type', 'unknown')}",
            error_msg=error_msg
        )
