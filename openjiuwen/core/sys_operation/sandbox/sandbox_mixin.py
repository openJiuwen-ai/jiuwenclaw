# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import re
from typing import Any, AsyncIterator

from openjiuwen.core.common.logging.utils import get_session_id
from openjiuwen.core.sys_operation.sandbox.run_config import SandboxRunConfig
from openjiuwen.core.sys_operation.sandbox.gateway.gateway_client import SandboxGatewayClient

# Template placeholder for session_id
_TEMPLATE_SESSION_PLACEHOLDER = "{session_id}"


def _resolve_isolation_key_template(template: str) -> str:
    """Resolve the isolation key template by replacing {session_id} with actual session_id.

    Args:
        template: Isolation key template with {session_id} placeholder

    Returns:
        Resolved isolation key with actual session_id
    """
    if _TEMPLATE_SESSION_PLACEHOLDER in template:
        session_id = get_session_id() or "default_session"
        return template.replace(_TEMPLATE_SESSION_PLACEHOLDER, session_id)
    return template


class SandboxGatewayClientMixin:
    """Mixin providing gateway client management and invoke/invoke_stream for sandbox operations."""

    def _init_client_context(self, run_config: SandboxRunConfig, op_type: str):
        self._config = run_config.config
        self._isolation_key_template = run_config.isolation_key_template
        self._op_type = op_type

    def _get_resolved_isolation_key(self) -> str:
        """Resolve the isolation key template with current session_id from context var."""
        return _resolve_isolation_key_template(self._isolation_key_template)

    async def _get_gateway_client(self) -> SandboxGatewayClient:
        if not hasattr(self, '_gateway_client'):
            self._gateway_client = SandboxGatewayClient(
                config=self._config,
                isolation_key=self._get_resolved_isolation_key(),
            )
        return self._gateway_client

    async def invoke(self, method: str, **params) -> Any:
        """Invoke a provider method through the gateway full-chain routing."""
        client = await self._get_gateway_client()
        return await client.invoke(self._op_type, method, **params)

    async def invoke_stream(self, method: str, **params) -> AsyncIterator:
        """Invoke a streaming provider method through the gateway full-chain routing."""
        client = await self._get_gateway_client()
        async for item in client.invoke_stream(self._op_type, method, **params):
            yield item


class BaseSandboxMixin(SandboxGatewayClientMixin):
    """Mixin for sandbox operations. Provides invoke() and invoke_stream() after init."""

    def _init_sandbox_context(self, run_config: SandboxRunConfig, op_type: str):
        self._init_client_context(run_config, op_type)
