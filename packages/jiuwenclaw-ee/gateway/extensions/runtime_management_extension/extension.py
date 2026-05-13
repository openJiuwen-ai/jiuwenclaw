# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Runtime Management Extension."""

from __future__ import annotations

import asyncio
import logging

from jiuwenclaw.extensions import ExtensionConfig
from jiuwenclaw.extensions.sdk.agent_server_client import AgentServerClientExtension
from .runtime_management_client import RuntimeManagementAgentClient

logger = logging.getLogger(__name__)


class RuntimeManagementExtension(AgentServerClientExtension):
    """Runtime 管理扩展。"""

    def __init__(self, client: RuntimeManagementAgentClient) -> None:
        self._client = client

    async def initialize(self, config: ExtensionConfig) -> None:
        return None

    def get_client(self) -> RuntimeManagementAgentClient:
        return self._client

    async def shutdown(self) -> None:
        try:
            await self._client.disconnect()
        except Exception as exc:
            logger.warning("[RuntimeManagement] shutdown error: %s", exc)

    def setup_gateway_shutdown_signals(self, shutdown_requested: asyncio.Event) -> None:
        """Gateway 调用：注册 SIGINT/SIGTERM；退出路径由 Gateway ``finally`` 里 ``await client.disconnect()``（与本扩展 shutdown 一致）。"""
        import signal

        logger.info(
            "[RuntimeManagement] 注册停机信号处理器（K8s 删 Pod/SIGINT）；"
            "退出时将执行 RuntimeManagementAgentClient.disconnect()。"
        )

        def _on_signal() -> None:
            logger.info("[RuntimeManagement] 收到停机信号 (SIGINT/SIGTERM)，准备退出…")
            shutdown_requested.set()

        loop = asyncio.get_running_loop()
        try:
            loop.add_signal_handler(signal.SIGINT, _on_signal)
            loop.add_signal_handler(signal.SIGTERM, _on_signal)
        except (NotImplementedError, OSError) as exc:
            logger.debug("[RuntimeManagement] 无法注册 SIGINT/SIGTERM: %s", exc)


async def register_extensions(registry) -> list[RuntimeManagementExtension]:
    """注册 Runtime Management 扩展。"""
    client = RuntimeManagementAgentClient()
    ext = RuntimeManagementExtension(client)
    registry.register_agent_server_client(ext)
    return [ext]
