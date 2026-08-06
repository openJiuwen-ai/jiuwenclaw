from __future__ import annotations

import logging
from typing import Any, ClassVar

from jiuwenswarm.common.schema.agent import AgentRequest, AgentResponse
from jiuwenswarm.common.local_env_config import apply_env_removals
from jiuwenswarm.agents.harness.common.tools.multimodal_config import (
    infer_multimodal_env_removals,
    merge_reload_env_snapshot,
    sync_multimodal_env_omission_state,
)
from jiuwenswarm.server.runtime.reload_result import (
    ReloadAggregateResult,
    ReloadResult,
    log_agent_config_hot_reload,
    log_reload_config_changes,
)
from jiuwenswarm.server.runtime.agent_manager import AgentManager

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
        self._latest_config: dict[str, Any] | None = None
        self._latest_env: dict[str, Any] | None = None
        self._last_reload_trace_id: str | None = None
        logger.info("[TenantAgentPool] Initialized with AgentManager")

    @classmethod
    def get_instance(cls) -> "TenantAgentPool":
        """获取单例实例."""
        if cls._instance is None:
            cls._instance = cls()
            logger.info("[TenantAgentPool] Created singleton instance")
        return cls._instance

    @classmethod
    def peek_instance(cls) -> "TenantAgentPool | None":
        """返回已初始化的单例；若尚未创建则返回 None（不触发构造）。"""
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
            return await self._agent_manager.process_message(request)
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
            async for chunk in self._agent_manager.process_message_stream(request):
                yield chunk
        except Exception as e:
            logger.error(f"[TenantAgentPool] Error in process_message_stream: {e}", exc_info=True)
            raise

    async def reload_agents_config(
        self,
        config: dict[str, Any] | None,
        env: dict[str, Any] | None,
        *,
        reload_trace_id: str | None = None,
    ) -> ReloadAggregateResult:
        """Hot-reload config/env for all managed agents."""
        if self._agent_manager is None:
            return ReloadAggregateResult()
        if reload_trace_id:
            self._last_reload_trace_id = reload_trace_id
        previous_env = self._latest_env if isinstance(self._latest_env, dict) else None
        omission_removals = infer_multimodal_env_removals(
            previous_env,
            env if isinstance(env, dict) else None,
        )
        if omission_removals:
            apply_env_removals(omission_removals)
            log_agent_config_hot_reload(
                logger,
                reload_trace_id=reload_trace_id,
                phase="omission_removals",
                source="TenantAgentPool",
                env_removed_by_omission_keys=sorted(omission_removals.keys()),
            )
        sync_multimodal_env_omission_state(
            omission_removals,
            env if isinstance(env, dict) else None,
        )
        self._latest_config = config
        self._latest_env = merge_reload_env_snapshot(previous_env, env)
        # Staging/promote lives in AgentManager (idle-gated promote).

        aggregate = ReloadAggregateResult()
        # Delegate to the inner AgentManager
        inner_result = await self._agent_manager.reload_agents_config(
            config=config,
            env=env,
            reload_trace_id=reload_trace_id,
        )
        if isinstance(inner_result, ReloadAggregateResult):
            aggregate.applied += inner_result.applied
            aggregate.deferred += inner_result.deferred
            aggregate.failed.extend(inner_result.failed)
        return aggregate

    async def cleanup(self) -> None:
        """清理资源."""
        logger.info("[TenantAgentPool] Cleaning up...")
        await self._agent_manager.cleanup()
        logger.info("[TenantAgentPool] Cleanup complete")
