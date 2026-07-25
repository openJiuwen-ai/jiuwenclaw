# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jiuwenswarm.extensions.agentos.agentos_router.agent_manager import (
    DEFAULT_AGENT_KEY_FIELDS,
    normalize_agent_key_fields,
)
from jiuwenswarm.extensions.agentos.agentos_router.registry_client import RegistryConfig
from jiuwenswarm.extensions.agentos.agentos_router.ssh_relay import (
    YuanrongSshSettings,
    load_yuanrong_ssh_settings,
)


@dataclass(frozen=True)
class SshChannelEndpoint:
    """Northbound ``channels.ssh`` listen address for ``3rdagent.switch``."""

    ip: str = ""
    port: int = 0


@dataclass(frozen=True)
class RouterConfig:
    frontend_endpoint: str
    function_version_urn: str
    concurrency: int
    invoke_timeout_s: float
    registry: RegistryConfig
    agent_namespace: str = "default"
    agent_timeout_s: float = 300.0
    creating_timeout_seconds: float = 60.0
    agent_key_fields: tuple[str, ...] = DEFAULT_AGENT_KEY_FIELDS
    ssh: YuanrongSshSettings = YuanrongSshSettings()
    ssh_channel: SshChannelEndpoint | None = None


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


def load_ssh_channel_endpoint(config: dict[str, Any]) -> SshChannelEndpoint | None:
    """Load northbound SSH listen ip/port from ``channels.ssh``.

    Returns ``None`` when the channel is disabled or listen address is incomplete.
    """
    channels = config.get("channels") if isinstance(config, dict) else None
    if not isinstance(channels, dict):
        return None
    ssh = channels.get("ssh")
    if not isinstance(ssh, dict):
        return None
    if not bool(ssh.get("enabled", False)):
        return None
    ip = str(ssh.get("listen_host") or "").strip()
    try:
        port = int(ssh.get("listen_port") or 0)
    except (TypeError, ValueError):
        return None
    if not ip or port <= 0:
        return None
    return SshChannelEndpoint(ip=ip, port=port)


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
        agent_namespace=str(agent_client.get("agent_namespace") or "default").strip() or "default",
        agent_timeout_s=float(agent_client.get("agent_timeout_s") or 300.0),
        registry=RegistryConfig(
            endpoint=str(registry.get("endpoint") or "").strip(),
            request_timeout_s=float(registry.get("request_timeout_s") or 10.0),
            node=str(registry.get("node") or "").strip(),
        ),
        creating_timeout_seconds=float(
            agentos.get("creating_timeout_seconds") or 60.0
        ),
        agent_key_fields=normalize_agent_key_fields(
            agentos.get("agent_key_fields")
        ),
        ssh=load_yuanrong_ssh_settings(agentos.get("ssh")),
        ssh_channel=load_ssh_channel_endpoint(config),
    )
