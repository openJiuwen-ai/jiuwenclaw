from __future__ import annotations

import logging
import time
from collections import OrderedDict
from typing import Any, ClassVar

from jiuwenavatar.common.schema.agent import AgentRequest, AgentResponse
from jiuwenavatar.common.enterprise import (
    TenantRuntimeContext,
    bind_tenant_context,
    extract_routing,
    is_enterprise_mode,
)
from jiuwenavatar.server.runtime.agent_manager import AgentManager

logger = logging.getLogger(__name__)


class TenantAgentPool:
    """AgentManager 管理器（单例）.

    职责：
    1. 管理 AgentManager 实例的创建和生命周期
    2. 提供统一的函数调用接口
    3. 调用 AgentManager 的方法（简单分发）
    """

    _instance: ClassVar[TenantAgentPool | None] = None

    def __init__(self) -> None:
        # 单个 AgentManager 实例
        self._agent_manager = AgentManager()
        self._tenant_managers: OrderedDict[str, tuple[AgentManager, float]] = OrderedDict()
        self._max_managers = self._load_max_managers()
        logger.info("[TenantAgentPool] Initialized max_managers=%s", self._max_managers)

    @staticmethod
    def _load_max_managers() -> int:
        try:
            return max(1, int(__import__("os").getenv("TENANT_AGENT_POOL_MAX", "64")))
        except (TypeError, ValueError):
            return 64

    @staticmethod
    def _manager_key(context: TenantRuntimeContext) -> str:
        if not is_enterprise_mode() or not context.has_identity:
            return "__standalone__"
        return "::".join(
            [
                context.service_id or "default-service",
                context.agent_id or context.user_id or "default-agent",
            ]
        )

    def get_manager_for_request(self, request: AgentRequest) -> AgentManager:
        """Return the AgentManager isolated for this request's routing key."""

        context = extract_routing(request.params or {})
        key = self._manager_key(context)
        if key == "__standalone__":
            return self._agent_manager

        existing = self._tenant_managers.get(key)
        if existing is not None:
            manager, _last_used = existing
            self._tenant_managers.move_to_end(key)
            self._tenant_managers[key] = (manager, time.time())
            return manager

        manager = AgentManager()
        self._tenant_managers[key] = (manager, time.time())
        self._tenant_managers.move_to_end(key)
        logger.info("[TenantAgentPool] Created tenant AgentManager key=%s", key)
        self._evict_if_needed()
        return manager

    def context_for_request(self, request: AgentRequest) -> TenantRuntimeContext:
        return extract_routing(request.params or {})

    def _evict_if_needed(self) -> None:
        while len(self._tenant_managers) > self._max_managers:
            key, (manager, _last_used) = self._tenant_managers.popitem(last=False)
            logger.info("[TenantAgentPool] Evicting tenant AgentManager key=%s", key)
            # Best-effort cleanup is scheduled by callers that are already inside
            # an event loop; synchronous eviction must not block request routing.

    @classmethod
    def get_instance(cls) -> "TenantAgentPool":
        """获取单例实例."""
        if cls._instance is None:
            cls._instance = cls()
            logger.info("[TenantAgentPool] Created singleton instance")
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例（仅用于测试）."""
        if cls._instance is not None:
            logger.info("[TenantAgentPool] Resetting singleton instance")
        cls._instance = None

    async def process_message(self, request: AgentRequest) -> AgentResponse:
        """处理非流式请求（简单分发到 AgentManager）.

        Args:
            request: AgentRequest 对象

        Returns:
            AgentResponse 对象
        """
        try:
            logger.info(
                "[TenantAgentPool] process_message called | request_id=%s | channel_id=%s",
                request.request_id,
                request.channel_id,
            )
            manager = self.get_manager_for_request(request)
            context = self.context_for_request(request)
            with bind_tenant_context(context):
                return await manager.process_message(request)
        except Exception as e:
            logger.error(f"[TenantAgentPool] Error in process_message: {e}", exc_info=True)
            # 可以选择返回错误响应或重新抛出异常
            raise

    async def process_message_stream(self, request: AgentRequest):
        """处理流式请求（简单分发到 AgentManager）.

        Args:
            request: AgentRequest 对象

        Yields:
            AgentResponseChunk 对象
        """
        try:
            logger.info(
                "[TenantAgentPool] process_message_stream called | request_id=%s | channel_id=%s",
                request.request_id,
                request.channel_id,
            )
            manager = self.get_manager_for_request(request)
            context = self.context_for_request(request)
            with bind_tenant_context(context):
                async for chunk in manager.process_message_stream(request):
                    yield chunk
        except Exception as e:
            logger.error(f"[TenantAgentPool] Error in process_message_stream: {e}", exc_info=True)
            raise

    async def cleanup(self) -> None:
        """清理资源."""
        logger.info("[TenantAgentPool] Cleaning up...")
        await self._agent_manager.cleanup()
        for key, (manager, _last_used) in list(self._tenant_managers.items()):
            try:
                await manager.cleanup()
            except Exception:
                logger.warning("[TenantAgentPool] tenant cleanup failed key=%s", key, exc_info=True)
        self._tenant_managers.clear()
        logger.info("[TenantAgentPool] Cleanup complete")
