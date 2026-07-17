# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jiuwenswarm.extensions.agentos.agentos_router.agent_manager import (
    DEFAULT_AGENT_KEY_FIELDS,
    normalize_agent_key_fields,
)
from jiuwenswarm.extensions.agentos.agentos_router.registry_client import RegistryConfig


@dataclass(frozen=True)
class RouterConfig:
    frontend_endpoint: str
    function_version_urn: str
    concurrency: int
    invoke_timeout_s: float
    registry: RegistryConfig
    creating_timeout_seconds: float = 60.0
    agent_key_fields: tuple[str, ...] = DEFAULT_AGENT_KEY_FIELDS


def agentos_router_selected(config: dict[str, Any]) -> bool:
    gateway = config.get("gateway") if isinstance(config, dict) else {}
    if not isinstance(gateway, dict):
        return False
    agent_client = gateway.get("agent_client")
    if not isinstance(agent_client, dict):
        agent_client = {}
    return (
        str(agent_client.get("type") or "websocket").strip().lower()
        == "agentos_router"
    )


def load_router_config(config: dict[str, Any]) -> RouterConfig:
    gateway = config.get("gateway") if isinstance(config, dict) else {}
    if not isinstance(gateway, dict):
        gateway = {}
    agent_client = gateway.get("agent_client")
    if not isinstance(agent_client, dict):
        agent_client = {}
    agentos = gateway.get("agentos")
    if not isinstance(agentos, dict):
        agentos = {}
    registry = agentos.get("registry")
    if not isinstance(registry, dict):
        registry = {}

    frontend_endpoint = str(agent_client.get("frontend_endpoint") or "").strip()
    function_version_urn = str(
        agent_client.get("function_version_urn") or ""
    ).strip()
    if not frontend_endpoint or not function_version_urn:
        raise ValueError(
            "gateway.agent_client.frontend_endpoint and function_version_urn "
            "are required in agentos_router mode"
        )

    return RouterConfig(
        frontend_endpoint=frontend_endpoint,
        function_version_urn=function_version_urn,
        concurrency=int(agent_client.get("concurrency") or 1),
        invoke_timeout_s=float(agent_client.get("invoke_timeout_s") or 60.0),
        registry=RegistryConfig(
            endpoint=str(registry.get("endpoint") or "").strip(),
            request_timeout_s=float(registry.get("request_timeout_s") or 10.0),
        ),
        creating_timeout_seconds=float(
            agentos.get("creating_timeout_seconds") or 60.0
        ),
        agent_key_fields=normalize_agent_key_fields(
            agentos.get("agent_key_fields")
        ),
    )
