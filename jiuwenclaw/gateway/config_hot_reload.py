# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""配置热更新处理逻辑."""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jiuwenclaw.gateway.agent_client import AgentServerClient

from jiuwenclaw.e2a.gateway_normalize import e2a_from_agent_fields
from jiuwenclaw.schema.message import ReqMethod
from jiuwenclaw.utils import restart_process
from jiuwenclaw.extensions.extension_config_sync import decrypt_extensions_sensitive_for_agent

logger = logging.getLogger(__name__)

BROWSER_RUNTIME_KEYS = frozenset(
    {
        "MODEL_PROVIDER",
        "MODEL_NAME",
        "API_BASE",
        "API_KEY",
        "VIDEO_PROVIDER",
        "VIDEO_MODEL_NAME",
        "VIDEO_API_BASE",
        "VIDEO_API_KEY",
        "AUDIO_PROVIDER",
        "AUDIO_MODEL_NAME",
        "AUDIO_API_BASE",
        "AUDIO_API_KEY",
        "VISION_PROVIDER",
        "VISION_MODEL_NAME",
        "VISION_API_BASE",
        "VISION_API_KEY",
    }
)


async def handle_config_hot_reload(
    client: AgentServerClient,
    updated_env_keys: set[str] | None = None,
    env_updates: dict[str, str] | None = None,
    config_payload: dict[str, Any] | None = None,
) -> bool:
    """处理配置热更新.

    Args:
        client: AgentServer 客户端
        updated_env_keys: 更新的环境变量键集合
        env_updates: 环境变量更新字典
        config_payload: 完整配置载荷

    Returns:
        是否成功
    """
    try:
        client.set_or_update_server_config(
            config=dict(config_payload or {}),
            env=dict(env_updates or {}),
        )

        # 发送给 AgentServer 前解密扩展敏感配置
        decrypted_config = decrypt_extensions_sensitive_for_agent(config_payload or {})
        reload_env = e2a_from_agent_fields(
            request_id=f"agent-reload-{uuid.uuid4().hex[:8]}",
            channel_id="",
            req_method=ReqMethod.AGENT_RELOAD_CONFIG,
            params={
                "config": dict(decrypted_config or {}),
                "env": dict(env_updates or {}),
            },
        )
        await client.send_request(reload_env)

        if updated_env_keys and (BROWSER_RUNTIME_KEYS & set(updated_env_keys)):
            restart_env = e2a_from_agent_fields(
                request_id=f"browser-restart-{uuid.uuid4().hex[:8]}",
                channel_id="",
                req_method=ReqMethod.BROWSER_RUNTIME_RESTART,
            )
            await client.send_request(restart_env)
        return True
    except Exception as e:
        logger.warning("[App] 配置热更新失败，将延迟重启: %s", e)
        restart_process(delay=2.0)
        return False


__all__ = [
    "BROWSER_RUNTIME_KEYS",
    "handle_config_hot_reload",
]
