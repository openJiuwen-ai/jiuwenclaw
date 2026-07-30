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
from jiuwenswarm.extensions.agentos.agentos_router.third_agent import AgentOSThirdAgent
from jiuwenswarm.extensions.sdk.agent_server_client import (
    AgentServerClientExtension,
)
from jiuwenswarm.extensions.sdk.third_agent import ThirdAgentExtension
from jiuwenswarm.extensions.yuanrong_frontend_client import (
    YuanrongFrontendAgentClient,
)
from jiuwenswarm.gateway.routing.agent_client import AgentServerClient
from jiuwenswarm.gateway.routing.third_agent import ThirdAgent


class AgentOSRouter(AgentServerClientExtension, ThirdAgentExtension):
    """AgentOS southbound Router extension (AgentServerClient + ThirdAgent)."""

    def __init__(self, config: RouterConfig) -> None:
        self._config = config
        self._yuanrong_client = YuanrongFrontendAgentClient(
            frontend_endpoint=config.frontend_endpoint,
            function_version_urn=config.function_version_urn,
            concurrency=config.concurrency,
            invoke_timeout_s=config.invoke_timeout_s,
            agent_timeout_s=config.agent_timeout_s,
            agent_namespace=config.agent_namespace,
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
        self._third_agent = AgentOSThirdAgent(self._router_client)
        self._closed = False

    async def initialize(self, config) -> None:
        del config

    def get_client(self) -> AgentServerClient:
        return self._router_client

    def get_third_agent(self) -> ThirdAgent:
        return self._third_agent

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
    registry.register_third_agent(extension)

    from jiuwenswarm.common.config import resolve_env_vars
    from jiuwenswarm.extensions.agentos.agentos_router.agentos_authenticator import AgentOSAuthenticator

    # 默认 passthrough（Registry 初始值）；仅 agentos 生效且 auth.type=agentos 时注入远程鉴权
    auth_config = config.get("auth") if isinstance(config, dict) else {}
    if not isinstance(auth_config, dict):
        auth_config = {}
    auth_config = resolve_env_vars(auth_config) or {}
    auth_type = str(auth_config.get("type") or "passthrough").strip().lower()

    if auth_type != "agentos":
        return [extension]

    agentos_auth_config = auth_config.get("agentos")
    if not isinstance(agentos_auth_config, dict):
        agentos_auth_config = {}
    agentos_auth_config = resolve_env_vars(agentos_auth_config) or {}

    auth_service_url = str(agentos_auth_config.get("auth_service_url") or "").strip()
    if not auth_service_url:
        raise ValueError("auth.agentos.auth_service_url is required when auth.type=agentos")

    timeout_raw = agentos_auth_config.get("timeout", 10.0)
    try:
        timeout = float(timeout_raw)
    except (TypeError, ValueError):
        timeout = 10.0
    authenticator = AgentOSAuthenticator(
        auth_service_url=auth_service_url,
        timeout=timeout,
    )

    registry.register_authenticator(authenticator)
    extension._authenticator = authenticator

    return [extension]
