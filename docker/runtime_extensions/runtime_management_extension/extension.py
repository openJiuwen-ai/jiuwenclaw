# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Runtime Management Extension.

集成 Runtime Management Agent Client 和运行时管理能力：
- 提供 RuntimeManagementAgentClient（继承 AgentServerClientExtension）
- 管理 openjiuwen_runtime.management.orchestrator.Access 实例
"""

from __future__ import annotations

import logging
from typing import Any

from jiuwenclaw.config import get_config
from jiuwenclaw.extensions.sdk.agent_server_client import AgentServerClientExtension
from .runtime_management_client import RuntimeManagementAgentClient

logger = logging.getLogger(__name__)


class RuntimeManagementExtension(AgentServerClientExtension):
    """Runtime 管理扩展，提供客户端实例和运行时管理能力。"""

    def __init__(self, client: RuntimeManagementAgentClient) -> None:
        self._client = client
        self._initialized = False

    async def initialize(self, config: Any) -> None:
        """初始化扩展。

        Args:
            config: 扩展配置对象
        """
        from jiuwenclaw.extensions.registry import ExtensionRegistry

        registry = ExtensionRegistry.get_instance()

        logger.info("[RuntimeManagement] 扩展初始化开始")

        # 注册运行时管理事件回调（可选）
        await self._register_runtime_hooks(registry)

        self._initialized = True
        logger.info("[RuntimeManagement] 扩展初始化完成")

    async def _register_runtime_hooks(self, registry) -> None:
        """注册运行时管理钩子（可选）。"""
        # 如需在聊天请求前执行特定逻辑，可在此注册
        pass

    def get_client(self) -> RuntimeManagementAgentClient:
        """返回 Runtime Management 客户端实例。"""
        return self._client

    async def shutdown(self) -> None:
        """关闭扩展，清理资源。"""
        logger.info("[RuntimeManagement] 扩展关闭")
        try:
            await self._client.disconnect()
        except Exception as exc:
            logger.warning("[RuntimeManagement] shutdown error: %s", exc)
        self._initialized = False


async def register_extensions(registry) -> list[RuntimeManagementExtension]:
    """注册 Runtime Management 扩展。

    Args:
        registry: 扩展注册表

    Returns:
        注册的扩展列表
    """
    cfg = get_config()
    gateway = cfg.get("gateway") if isinstance(cfg, dict) else {}
    agent_client = gateway.get("agent_client") if isinstance(gateway, dict) else {}

    if not isinstance(agent_client, dict):
        logger.info("[RuntimeManagement] 未配置 agent_client，跳过注册")
        return []

    client_type = str(agent_client.get("type") or "").strip().lower()
    if client_type != "runtime_orchestrator":
        logger.info(
            "[RuntimeManagement] client_type=%s 不是 runtime_orchestrator，跳过注册",
            client_type,
        )
        return []

    # 创建 Access 实例
    try:
        from openjiuwen_runtime.management.orchestrator.access import Access, AccessConfig
        from openjiuwen_runtime.foundation.db.handler import DBHandler

        db_config = agent_client.get("db_config", {})
        db_handler = DBHandler(**db_config) if db_config else None

        access_config = AccessConfig(
            db_handler=db_handler,
            image=agent_client.get("image", "jiuwenclaw-agent:latest"),
            max_concurrency=int(agent_client.get("max_concurrency", 200)),
            min_idle_services=int(agent_client.get("min_idle_services", 1)),
            max_services=int(agent_client.get("max_services", 10)),
            target_port=int(agent_client.get("target_port", 8000)),
            invoke_path=agent_client.get("invoke_path", "/invoke"),
            service_ttl=int(agent_client.get("service_ttl", 300)),
            queue_size=int(agent_client.get("queue_size", 100)),
            message_timeout=float(agent_client.get("message_timeout", 30)),
            max_retries=int(agent_client.get("max_retries", 3)),
        )

        access_instance = Access(config=access_config)
        logger.info("[RuntimeManagement] Access 实例创建成功")

    except ImportError as exc:
        logger.error("[RuntimeManagement] 无法导入 openjiuwen_runtime: %s", exc)
        raise ValueError(
            "openjiuwen_runtime 未安装，请运行: pip install openjiuwen-runtime"
        ) from exc
    except Exception as exc:
        logger.error("[RuntimeManagement] 创建 Access 实例失败: %s", exc)
        raise

    # 创建客户端
    client = RuntimeManagementAgentClient(
        access_instance=access_instance,
        concurrency=int(agent_client.get("concurrency", 1)),
        invoke_timeout_s=float(agent_client.get("invoke_timeout_s", 60.0)),
    )

    ext = RuntimeManagementExtension(client)
    registry.register_agent_server_client(ext)

    logger.info("[RuntimeManagement] 扩展注册成功")
    return [ext]
