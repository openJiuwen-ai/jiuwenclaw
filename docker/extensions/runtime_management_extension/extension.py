# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Runtime Management Extension.

集成 Runtime Management Agent Client 和运行时管理能力：
- 提供 RuntimeManagementAgentClient（继承 AgentServerClientExtension）
- 管理 openjiuwen_runtime.management.orchestrator.Access 实例
"""

from __future__ import annotations

import logging

from jiuwenclaw.extensions import ExtensionConfig
from jiuwenclaw.extensions.sdk.agent_server_client import AgentServerClientExtension
from .runtime_management_client import RuntimeManagementAgentClient

logger = logging.getLogger(__name__)

class RuntimeManagementExtension(AgentServerClientExtension):
    """Runtime 管理扩展，提供客户端实例和运行时管理能力。"""

    def __init__(self, client: RuntimeManagementAgentClient) -> None:
        self._client = client

    async def initialize(self, config: ExtensionConfig) -> None:
        """扩展初始化

        Args:
            config: 扩展配置对象，包含全局配置和 logger
                   扩展可通过 self._load_config_from_yaml() 加载自己的 config.yaml
        """
        return

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


async def register_extensions(registry) -> list[RuntimeManagementExtension]:
    """注册 Runtime Management 扩展。

    Args:
        registry: 扩展注册表

    Returns:
        注册的扩展列表
    """
    # cfg = get_config()
    # gateway = cfg.get("gateway") if isinstance(cfg, dict) else {}
    # agent_client = gateway.get("agent_client") if isinstance(gateway, dict) else {}
    #
    # if not isinstance(agent_client, dict):
    #     logger.info("[RuntimeManagement] 未配置 agent_client，跳过注册")
    #     return []
    #
    # client_type = str(agent_client.get("type") or "").strip().lower()
    # if client_type != "runtime_orchestrator":
    #     logger.info(
    #         "[RuntimeManagement] client_type=%s 不是 runtime_orchestrator，跳过注册",
    #         client_type,
    #     )
    #     return []
    #
    # # 创建 ServiceManager 及相关组件
    # try:
    #     from openjiuwen_runtime.management.session.dual_queue import PriorityDualAsyncQueues
    #     from openjiuwen_runtime.management.session.service_manager import ServiceManager
    #     from openjiuwen_runtime.management.session.timer import Timer
    #     from openjiuwen_runtime.management.session.runtime import NoOpDeployController
    #     from openjiuwen_runtime.management.session.service_handler import ServiceHandler
    #     from openjiuwen_runtime.management.session.ws_client_channel import WSServiceMessageChannel
    #     from openjiuwen_runtime.management.session.interfaces import (
    #         IServiceInstanceFactory,
    #         IServiceHandler,
    #         IResponseParser,
    #         IServiceManager,
    #     )
    #
    #     # 创建 WebSocket 消息通道，用于连接下游 Agent Runtime 服务实例
    #     target_port = int(agent_client.get("target_port", 8080))
    #     invoke_path = agent_client.get("invoke_path", "/invoke")
    #     ws_use_tls = bool(agent_client.get("ws_use_tls", False))
    #     connect_timeout = float(agent_client.get("connect_timeout", 30.0))
    #
    #     message_channel = WSServiceMessageChannel(
    #         target_port=target_port,
    #         invoke_path=invoke_path,
    #         ws_use_tls=ws_use_tls,
    #         connect_timeout=connect_timeout,
    #     )
    #
    #     logger.info(
    #         "[RuntimeManagement] WebSocket 消息通道已创建: port=%s path=%s tls=%s timeout=%.1fs",
    #         target_port, invoke_path, ws_use_tls, connect_timeout
    #     )
    #
    #     # 创建服务工厂
    #     class _ServiceFactory(IServiceInstanceFactory):
    #         def __init__(self) -> None:
    #             self._response_parser: IResponseParser | None = None
    #
    #         async def new_service(self, response_parser: IResponseParser) -> IServiceHandler:
    #             self._response_parser = response_parser
    #             return ServiceHandler(
    #                 total_concurrency=int(agent_client.get("max_concurrency", 200)),
    #                 message_channel=message_channel,
    #                 response_parser=response_parser,
    #                 deploy_controller=NoOpDeployController(),
    #             )
    #
    #     service_factory = _ServiceFactory()
    #
    #     # 创建双队列
    #     dual_queue = PriorityDualAsyncQueues(
    #         user_queue_size=int(agent_client.get("queue_size", 1000)),
    #         system_queue_size=int(agent_client.get("system_queue_size", 100)),
    #     )
    #
    #     # 使用ServiceManager实例实现IServiceManager接口
    #     service_manager = ServiceManager(
    #         service_factory=service_factory,
    #         dual_queue=dual_queue,
    #         timer=Timer(),
    #         service_concurrency=int(agent_client.get("max_concurrency", 200)),
    #         min_idle_services=int(agent_client.get("min_idle_services", 0)),
    #         max_services=int(agent_client.get("max_services", 10)),
    #         autoscale_interval=float(agent_client.get("autoscale_interval", 0.5)),
    #         service_idle_ttl=int(agent_client.get("service_ttl", 300)),
    #     )
    #
    #     logger.info("[RuntimeManagement] ServiceManager 创建成功")
    #
    # except ImportError as exc:
    #     logger.error("[RuntimeManagement] 无法导入 openjiuwen_runtime: %s", exc)
    #     raise ValueError(
    #         "openjiuwen_runtime 未安装，请运行: pip install openjiuwen-runtime"
    #     ) from exc
    # except Exception as exc:
    #     logger.error("[RuntimeManagement] 创建 ServiceManager 失败: %s", exc)
    #     raise

    # 创建客户端
    client = RuntimeManagementAgentClient()

    ext = RuntimeManagementExtension(client)
    registry.register_agent_server_client(ext)

    logger.info("[RuntimeManagement] 扩展注册成功")
    return [ext]
