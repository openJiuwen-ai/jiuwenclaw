# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""TenantAgentPool - AgentManager 池（社区单例 / 企业多租户 LRU）."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, ClassVar

from jiuwenswarm.common.schema.agent import AgentRequest, AgentResponse
from jiuwenswarm.common.local_env_config import apply_env_removals
from jiuwenswarm.common.utils import AsyncLRUCache, get_multi_tenant_user_workspace_dir
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


def _is_enterprise_runtime() -> bool:
    return bool(os.getenv("AGENT_RUNTIME", "").strip())


class TenantAgentPool:
    """AgentManager 管理器（单例）.

    - 社区版（未设置 AGENT_RUNTIME）：单个 AgentManager，方法透传到该实例
    - 企业版（AGENT_RUNTIME）：按 agent_id + service_id LRU 缓存多个 AgentManager，
      并为每个租户隔离 workspace 目录
    """

    _instance: ClassVar[TenantAgentPool | None] = None

    def __init__(self, cache_max_size: int = 100, cache_ttl: int = 600) -> None:
        self._enterprise = _is_enterprise_runtime()
        self._locks: dict[str, asyncio.Lock] = {}
        # 必须持有 loop 对象本身，不能只存 id(loop)：asyncio.run 结束后旧 loop
        # 被回收时 id 可能复用到新 loop，误判为同一循环并复用已占用的旧锁，导致死锁。
        self._lock_loops: dict[str, asyncio.AbstractEventLoop] = {}
        self._global_lock = asyncio.Lock()
        self._latest_config: dict[str, Any] | None = None
        self._latest_env: dict[str, Any] | None = None
        self._last_reload_trace_id: str | None = None
        if self._enterprise:
            self._agent_wrappers = AsyncLRUCache(max_size=cache_max_size, ttl_seconds=cache_ttl)
            self._agent_manager: AgentManager | None = None
            logger.info(
                "[TenantAgentPool] Enterprise mode (AGENT_RUNTIME): multi-tenant LRU pool"
            )
        else:
            self._agent_wrappers = None
            self._agent_manager = AgentManager()
            logger.info("[TenantAgentPool] Community mode: single AgentManager")

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
            cls._instance.prepare_reset_for_tests()
            logger.info("[TenantAgentPool] Resetting singleton instance")
        cls._instance = None

    def prepare_reset_for_tests(self) -> None:
        """清理企业版池状态，供测试重置单例前调用."""
        if self._enterprise and self._agent_wrappers is not None:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._agent_wrappers.clear())
            except RuntimeError:
                asyncio.run(self._agent_wrappers.clear())
            self._locks.clear()
            self._lock_loops.clear()

    def __getattr__(self, name: str) -> Any:
        """社区版：未定义方法透传到内部 AgentManager."""
        if name.startswith("_"):
            raise AttributeError(name)
        manager = object.__getattribute__(self, "_agent_manager")
        if manager is not None:
            return getattr(manager, name)
        raise AttributeError(f"{type(self).__name__!r} object has no attribute {name!r}")

    @staticmethod
    def build_service_id(chat_id: str | None, bot_app_id: str | None) -> str:
        """根据 chat_id 和 bot_app_id 构建 service_id."""
        chat = chat_id or "unknown_chat_id"
        bot = bot_app_id or "unknown_bot_app_id"
        return f"{chat}_{bot}"

    def _get_lock(self, cache_key: str) -> asyncio.Lock:
        """获取或创建 cache_key 对应的锁；事件循环变化时重建，避免跨 loop 复用。"""
        current_loop = asyncio.get_running_loop()

        if cache_key not in self._locks:
            self._locks[cache_key] = asyncio.Lock()
            self._lock_loops[cache_key] = current_loop
        else:
            stored_loop = self._lock_loops.get(cache_key)
            if stored_loop is not current_loop:
                old_lock = self._locks[cache_key]
                if old_lock.locked() or getattr(old_lock, "_waiters", None):
                    logger.error(
                        "[TenantAgentPool] Lock for %s has waiters during loop change! "
                        "This may cause data inconsistency.",
                        cache_key,
                    )
                self._locks[cache_key] = asyncio.Lock()
                self._lock_loops[cache_key] = current_loop

        return self._locks[cache_key]

    @staticmethod
    def _build_workspace_path(service_id: str | None, agent_id: str | None) -> Path | None:
        """企业租户用户工作目录根路径."""
        return get_multi_tenant_user_workspace_dir(service_id, agent_id)

    @staticmethod
    def _extract_ids(request: AgentRequest) -> tuple[str, str]:
        agent_id = getattr(request, "agent_id", None)
        service_id = getattr(request, "service_id", None)
        if request.channel_id == "acp":
            return "acp", "global_acp"
        return agent_id or "default_agent_id", service_id or "default_service_id"

    async def _ensure_agent_manager(
        self,
        agent_id: str,
        service_id: str | None = None,
    ) -> AgentManager:
        if not self._enterprise:
            if self._agent_manager is None:
                raise RuntimeError("TenantAgentPool community mode missing AgentManager")
            return self._agent_manager

        cache_key = f"{agent_id}_{service_id}"
        lock = self._get_lock(cache_key)
        async with lock:
            if self._agent_wrappers is None:
                raise RuntimeError("TenantAgentPool enterprise mode missing agent wrappers cache")
            cached = await self._agent_wrappers.get(cache_key)
            if cached is not None:
                return cached

            logger.info(
                "[TenantAgentPool] create AgentManager: agent_id=%s service_id=%s",
                agent_id,
                service_id,
            )
            workspace = self._build_workspace_path(service_id, agent_id)
            if workspace is None:
                raise ValueError(
                    f"invalid tenant workspace: agent_id={agent_id!r}, service_id={service_id!r}"
                )
            manager = AgentManager(
                agent_id=agent_id,
                service_id=service_id or "",
                user_workspace_dir=workspace,
            )
            await self._agent_wrappers.put(cache_key, manager)
            active = await self._agent_wrappers.keys()
            for stale in [k for k in self._locks if k not in active]:
                del self._locks[stale]
                self._lock_loops.pop(stale, None)
            return manager

    async def process_message(self, request: AgentRequest) -> AgentResponse:
        """处理非流式请求."""
        if not self._enterprise:
            if self._agent_manager is None:
                raise RuntimeError("TenantAgentPool community mode missing AgentManager")
            return await self._agent_manager.process_message(request)
        agent_id, service_id = self._extract_ids(request)
        manager = await self._ensure_agent_manager(agent_id, service_id)
        return await manager.process_message(request)

    async def process_message_stream(self, request: AgentRequest):
        """处理流式请求."""
        if not self._enterprise:
            if self._agent_manager is None:
                raise RuntimeError("TenantAgentPool community mode missing AgentManager")
            async for chunk in self._agent_manager.process_message_stream(request):
                yield chunk
            return
        agent_id, service_id = self._extract_ids(request)
        manager = await self._ensure_agent_manager(agent_id, service_id)
        async for chunk in manager.process_message_stream(request):
            yield chunk

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
        if self._enterprise and self._agent_wrappers is not None:
            for key in await self._agent_wrappers.keys():
                manager = await self._agent_wrappers.get(key)
                if manager is not None:
                    try:
                        await manager.cleanup()
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "[TenantAgentPool] AgentManager cleanup failed for %s: %s",
                            key,
                            exc,
                        )
            await self._agent_wrappers.clear()
            self._locks.clear()
            self._lock_loops.clear()
        elif self._agent_manager is not None:
            await self._agent_manager.cleanup()
        logger.info("[TenantAgentPool] Cleanup complete")

    def is_working(self) -> bool:
        """返回是否有任何租户 Agent 正在工作."""
        if self._agent_manager is not None:
            return self._agent_manager.is_working()
        wrappers = self._agent_wrappers
        if wrappers is None:
            return False
        try:
            for manager in wrappers.values():
                if manager is not None and manager.is_working():
                    return True
        except Exception:  # noqa: BLE001
            return False
        return False

    async def get_agent_manager(self, agent_id: str, service_id: str) -> AgentManager:
        """获取（企业版：按需创建）指定租户的 AgentManager."""
        return await self._ensure_agent_manager(agent_id, service_id)

    async def cancel_all_inflight_work(self, reason: str = "[gateway ws disconnect] ") -> None:
        """Gateway 与 AgentServer 的 WebSocket 断开时：取消在途任务。

        - 社区版：透传到唯一 AgentManager
        - 企业版：遍历 LRU 中所有租户 AgentManager；单租户失败不影响其他租户
        """
        if not self._enterprise:
            if self._agent_manager is None:
                raise RuntimeError("TenantAgentPool community mode missing AgentManager")
            await self._agent_manager.cancel_all_inflight_work(reason)
            return

        if self._agent_wrappers is None:
            raise RuntimeError("TenantAgentPool enterprise mode missing agent wrappers cache")
        keys = await self._agent_wrappers.keys()
        for key in keys:
            agent_manager = await self._agent_wrappers.get(key)
            if agent_manager is not None:
                try:
                    await agent_manager.cancel_all_inflight_work(reason)
                except Exception:
                    logger.exception(
                        "[TenantAgentPool] cancel_all_inflight_work failed for %s", key
                    )
