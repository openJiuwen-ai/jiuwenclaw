# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

from jiuwenswarm.common.config import get_config
from jiuwenswarm.extensions.agentos.agentos_router.agent_manager import AgentManager
from jiuwenswarm.extensions.agentos.agentos_router.config import (
    RouterConfig,
    agentos_router_selected,
    load_router_config,
)
from jiuwenswarm.extensions.agentos.agentos_router.registry_client import RegistryClient
from jiuwenswarm.extensions.agentos.agentos_router.router_client import AgentOSRouterClient
from jiuwenswarm.extensions.sdk.agent_server_client import (
    AgentServerClientExtension,
)
from jiuwenswarm.extensions.yuanrong_frontend_client import (
    YuanrongFrontendAgentClient,
)
from jiuwenswarm.gateway.routing.agent_client import AgentServerClient


class AgentOSRouter(AgentServerClientExtension):
    """AgentOS southbound Router extension."""

    def __init__(self, config: RouterConfig) -> None:
        self._config = config
        self._yuanrong_client = YuanrongFrontendAgentClient(
            frontend_endpoint=config.frontend_endpoint,
            function_version_urn=config.function_version_urn,
            concurrency=config.concurrency,
            invoke_timeout_s=config.invoke_timeout_s,
        )
        self._registry_client = RegistryClient(config.registry)
        self._agent_manager = AgentManager(
            creating_timeout_seconds=config.creating_timeout_seconds,
            key_fields=config.agent_key_fields,
        )
        self._router_client = AgentOSRouterClient(
            self._yuanrong_client,
            self._registry_client,
            self._agent_manager,
        )
        self._closed = False

    async def initialize(self, config) -> None:
        del config

    def get_client(self) -> AgentServerClient:
        return self._router_client

    async def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._router_client.shutdown()


async def register_extensions(registry):
    config = get_config()
    if not agentos_router_selected(config):
        return []
    extension = AgentOSRouter(load_router_config(config))
    registry.register_agent_server_client(extension)
    return [extension]
