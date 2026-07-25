# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Auth extension entry (discovered by ExtensionLoader).

默认不注册任何认证器（核心 Registry 已提供 PassthroughAuthenticator）。
仅当 ``config.yaml`` 中 ``extensions.auth.type=agentos`` 时，
才 lazy-import 并注册 AgentOSAuthenticator。
"""

from __future__ import annotations

from typing import Any

from jiuwenswarm.common.config import get_config, resolve_env_vars
from jiuwenswarm.common.utils import logger


def _load_auth_config() -> dict[str, Any]:
    """从主配置读取 extensions.auth。"""
    root = get_config()
    extensions = root.get("extensions") if isinstance(root, dict) else None
    auth = extensions.get("auth") if isinstance(extensions, dict) else None
    return auth if isinstance(auth, dict) else {}


async def register_extensions(registry):
    """按配置条件注册认证器；非 agentos 时保持 Registry 默认 Passthrough。"""
    auth_config = resolve_env_vars(_load_auth_config()) or {}
    auth_type = str(auth_config.get("type") or "passthrough").strip().lower()
    if auth_type != "agentos":
        logger.info("[auth] skip AgentOS authenticator (type=%s)", auth_type or "passthrough")
        return []

    agentos_config = auth_config.get("agentos")
    if not isinstance(agentos_config, dict):
        agentos_config = {}
    agentos_config = resolve_env_vars(agentos_config) or {}

    auth_service_url = str(agentos_config.get("auth_service_url") or "").strip()
    if not auth_service_url:
        raise ValueError(
            "extensions.auth.agentos.auth_service_url is required when auth.type=agentos"
        )

    # lazy import：未启用 agentos 时不加载 jose/httpx 等依赖与实现模块
    from jiuwenswarm.extensions.auth.agentos_authenticator import AgentOSAuthenticator

    gateway_secret_key = agentos_config.get("gateway_secret_key")
    if isinstance(gateway_secret_key, str):
        gateway_secret_key = gateway_secret_key.strip() or None

    authenticator = AgentOSAuthenticator(
        auth_service_url=auth_service_url,
        gateway_secret_key=gateway_secret_key,
    )
    registry.register_authenticator(authenticator)
    logger.info("[auth] registered AgentOSAuthenticator url=%s", auth_service_url)
    return []