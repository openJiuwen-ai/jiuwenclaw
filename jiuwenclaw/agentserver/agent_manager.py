# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""AgentManager - 管理单个租户内的 Agent 实例."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, TYPE_CHECKING

from jiuwenclaw.e2a.acp.protocol import build_acp_initialize_result

if TYPE_CHECKING:
    from jiuwenclaw.agentserver.interface import JiuWenClaw

logger = logging.getLogger(__name__)


ACP_DEFAULT_CAPABILITIES: dict[str, Any] = build_acp_initialize_result()

# 进程级锁：保护 os.environ 修改，避免多租户/多协程交叉污染
_ENV_LOCK = threading.Lock()


def _build_acp_agent_config(extra_config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the dedicated ACP agent profile config.

    ACP sessions should use ACP-native filesystem/terminal tools instead of the
    default openjiuwen filesystem/bash toolchain.

    通道与工具配置由 ACP 协议固定，``extra_config`` 中的同名字段无效。
    """
    config: dict[str, Any] = {
        "agent_name": "acp_agent",
        "enable_filesystem_rail": True,
    }
    if isinstance(extra_config, dict):
        config.update(extra_config)
    # 固定字段不允许被 extra_config 覆盖
    config["channel_id"] = "acp"
    config["tool_profile"] = "acp"
    return config


def _apply_env_overrides(env_overrides: dict[str, Any] | None) -> None:
    """统一应用环境变量覆盖。

    None 值表示删除；其它值统一转为 str。整个操作在进程级锁保护下执行，
    避免多个租户/协程同时修改 ``os.environ`` 导致交叉污染。
    """
    if not env_overrides:
        return
    with _ENV_LOCK:
        for env_key, env_value in env_overrides.items():
            key = str(env_key)
            if env_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(env_value)


def _parse_request_meta(request: Any) -> tuple[str, str | None, str, str, str | None]:
    """从 ``AgentRequest`` 提取调度所需元信息。

    Returns:
        (channel_id, session_id, mode_full, mode, workspace_dir)
    """
    channel_id = getattr(request, "channel_id", "") or ""
    session_id = getattr(request, "session_id", None)
    raw_params = getattr(request, "params", {})
    params = raw_params if isinstance(raw_params, dict) else {}
    mode_full = params.get("mode", "agent.plan")
    mode = str(mode_full).split(".")[0] if mode_full else "agent"
    workspace_dir = params.get("workspace_dir")
    return channel_id, session_id, str(mode_full), mode, workspace_dir


class AgentManager:
    """管理单个租户内的 Agent 实例.

    支持多种通道:
    - "acp": ACP 协议通道
    - "default": 默认通道
    """

    def __init__(
            self,
            agent_id: str,
            service_id: str,
            user_workspace_dir: Path | None = None,
            config_base: dict[str, Any] | None = None,
            env_overrides: dict[str, Any] | None = None,
    ) -> None:
        """初始化 AgentManager.

        Args:
            agent_id: agent名称/路径
            service_id: 服务ID（chat_id + bot_app_id 组合）
            user_workspace_dir: 用户工作目录路径
            config_base: 初始配置（用于懒加载 agent 时重放）
            env_overrides: 初始环境变量覆盖（用于懒加载 agent 时重放）
        """
        # 结构: dict[channel_id][mode][session_id] -> JiuWenClaw
        # 每个 session_id 对应独立的 Agent 实例，支持多 session 并发执行
        self.agents: dict[str, dict[str, dict[str, "JiuWenClaw"]]] = {}
        self._client_capabilities_by_channel: dict[str, dict[str, Any]] = {}

        # 保存初始配置（用于后续创建的 agent 重放）
        self._latest_config_base: dict[str, Any] | None = config_base
        self._latest_env_overrides: dict[str, Any] = (
            dict(env_overrides) if isinstance(env_overrides, dict) else {}
        )

        # 应用初始 env_overrides
        _apply_env_overrides(self._latest_env_overrides)

        # 保护 self.agents 与 self._latest_config_base/_latest_env_overrides 的并发访问
        self._agents_lock: asyncio.Lock | None = None

        self.agent_id = agent_id
        self.service_id = service_id
        self.user_workspace_dir = user_workspace_dir
        logger.info(
            "[AgentManager] 初始化: agent_id=%s, service_id=%s, workspace=%s, has_config=%s",
            agent_id,
            service_id,
            user_workspace_dir,
            config_base is not None,
        )

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _get_lock(self) -> asyncio.Lock:
        """惰性创建 lock，避免 __init__ 阶段无事件循环时报错。"""
        if self._agents_lock is None:
            self._agents_lock = asyncio.Lock()
        return self._agents_lock

    def _lookup_agent(
        self, channel_id: str, mode: str, session_id: str,
    ) -> "JiuWenClaw | None":
        """同步查找已创建的 agent，不做副作用。"""
        return (
            self.agents.get(channel_id, {})
            .get(mode, {})
            .get(session_id)
        )

    # pylint: disable=protected-access
    async def _create_agent(
        self,
        agent_key: str,
        mode: str = "agent",
        session_id: str = "default",
        config: dict[str, Any] | None = None,
    ) -> "JiuWenClaw":
        """创建 Agent 实例.

        Args:
            agent_key: Agent 键（如 "acp" 或 "default"）
            mode: 工作模式
            session_id: 会话 ID，用于实例命名和存储
            config: 可选配置

        Returns:
            JiuWenClaw 实例
        """
        from jiuwenclaw.agentserver.interface import JiuWenClaw

        _apply_env_overrides(self._latest_env_overrides)
        logger.info(
            "[AgentManager] Creating %s agent (mode=%s, session=%s)",
            agent_key, mode, session_id,
            extra={'user_visible': 'progress'},
        )

        agent = JiuWenClaw(
            user_workspace_dir=str(self.user_workspace_dir) if self.user_workspace_dir else None,
            agent_id=self.agent_id,
            service_id=self.service_id,
        )
        agent._agent_name = (
            f"agent_{self.agent_id}_{self.service_id}_{agent_key}_{session_id}"
        )
        await agent.create_instance(config, mode=mode)

        # 创建后如果有保存的配置，重放 reload_agent_config；失败时不注册
        if self._latest_config_base is not None or self._latest_env_overrides:
            try:
                await agent.reload_agent_config(
                    config_base=self._latest_config_base,
                    env_overrides=self._latest_env_overrides,
                )
                logger.info(
                    "[AgentManager] Replayed reload_agent_config for %s agent (session=%s)",
                    agent_key, session_id,
                )
            except Exception as exc:
                logger.error(
                    "[AgentManager] Replay reload_agent_config failed, rolling back: %s",
                    exc,
                )
                # 回滚：清理刚创建的 agent，避免半初始化实例对外暴露
                if hasattr(agent, "cleanup"):
                    try:
                        await agent.cleanup()
                    except Exception as cleanup_exc:
                        logger.warning(
                            "[AgentManager] Rollback cleanup failed: %s", cleanup_exc,
                        )
                raise

        # 注册到 self.agents（已经在调用方的 lock 内）
        self.agents.setdefault(agent_key, {}).setdefault(mode, {})[session_id] = agent

        logger.info(
            "[AgentManager] %s agent created for tenant %s (session=%s)",
            agent_key, self.agent_id, session_id,
            extra={'user_visible': 'progress'},
        )
        return agent

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    async def initialize(
        self, channel_id: str = "", extra_config: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """初始化 AgentManager.

        对于 ACP 通道，创建 agent 并返回 capabilities。
        """
        if channel_id != "acp":
            return None

        logger.info(
            "[AgentManager] ACP initialize for tenant %s", self.agent_id,
            extra={'user_visible': 'critical'},
        )
        if extra_config:
            client_capabilities = extra_config.get("client_capabilities")
            if isinstance(client_capabilities, dict):
                self._client_capabilities_by_channel["acp"] = dict(client_capabilities)

        async with self._get_lock():
            if "acp" in self.agents:
                logger.info(
                    "[AgentManager] Resetting ACP agent for tenant %s", self.agent_id,
                    extra={'user_visible': 'progress'},
                )
                for mode_agents in self.agents.get("acp", {}).values():
                    for agent in mode_agents.values():
                        if hasattr(agent, "cleanup"):
                            try:
                                await agent.cleanup()
                            except Exception as exc:
                                logger.warning(
                                    "[AgentManager] ACP agent cleanup failed: %s", exc,
                                    extra={'user_visible': 'progress'},
                                )
                del self.agents["acp"]

            config = _build_acp_agent_config(extra_config)
            await self._create_agent("acp", "code", "default", config)

        return ACP_DEFAULT_CAPABILITIES.copy()

    async def cancel_all_inflight_work(
        self, reason: str = "[gateway ws disconnect] ",
    ) -> None:
        """Gateway 与 AgentServer 的 WebSocket 断开时：取消所有 Agent 在途任务。"""
        # 拷贝一份引用，避免迭代过程中外部修改 self.agents
        snapshot = [
            agent
            for channel_agents in list(self.agents.values())
            for mode_agents in list(channel_agents.values())
            for agent in list(mode_agents.values())
        ]
        for agent in snapshot:
            try:
                await agent.cancel_inflight_work(reason)
            except Exception:
                logger.exception("[AgentManager] cancel_inflight_work failed")

    def get_client_capabilities(self, channel_id: str = "") -> dict[str, Any]:
        """获取指定通道的客户端能力."""
        channel_key = str(channel_id or "").strip()
        caps = self._client_capabilities_by_channel.get(channel_key)
        return dict(caps) if isinstance(caps, dict) else {}

    async def create_session(
        self, channel_id: str = "", session_id: str | None = None,
    ) -> str:
        """创建会话.

        Args:
            channel_id: 通道 ID
            session_id: 可选的会话 ID

        Returns:
            会话 ID（非空字符串）
        """
        explicit_session_id = str(session_id or "").strip()
        if explicit_session_id:
            logger.info(
                "[AgentManager] session ensured: channel_id=%s session_id=%s",
                channel_id, explicit_session_id,
                extra={'user_visible': 'progress'},
            )
            return explicit_session_id
        if channel_id == "acp":
            new_id = f"acp_{uuid.uuid4().hex[:8]}"
            logger.info(
                "[AgentManager] ACP session created: session_id=%s", new_id,
                extra={'user_visible': 'critical'},
            )
            return new_id
        return "default"

    async def get_agent(
            self,
            channel_id: str = "",
            mode: str = "agent",
            workspace_dir: str | None = None,
            session_id: str | None = None,
    ) -> "JiuWenClaw | None":
        """获取 Agent 实例（自动创建）.

        每个 session_id 对应独立的 Agent 实例，支持多 session 并发执行。
        加锁后再次检查存在性，避免并发请求重复创建。
        """
        effective_session_id = session_id or "default"

        # 快速路径：无锁读，命中则直接返回
        existing = self._lookup_agent(channel_id, mode, effective_session_id)
        if existing is not None:
            logger.info(
                "[AgentManager] 复用现有Agent: channel=%s mode=%s session=%s",
                channel_id, mode, effective_session_id,
                extra={'user_visible': 'critical'},
            )
            return existing

        async with self._get_lock():
            # 双检：进入临界区后再次确认，避免 check-then-act 竞态
            existing = self._lookup_agent(channel_id, mode, effective_session_id)
            if existing is not None:
                return existing

            config: dict[str, Any] = {"workspace_dir": workspace_dir} if workspace_dir else {}
            if channel_id == "acp":
                config = {**config, **_build_acp_agent_config()}

            logger.info(
                "[AgentManager] 创建新的Agent: channel=%s mode=%s session=%s",
                channel_id, mode, effective_session_id,
                extra={'user_visible': 'critical'},
            )
            await self._create_agent(channel_id, mode, effective_session_id, config)
            return self._lookup_agent(channel_id, mode, effective_session_id)

    def get_agent_nowait(
            self,
            channel_id: str = "",
            mode: str = "agent",
            session_id: str | None = None,
    ) -> "JiuWenClaw | None":
        """获取 Agent 实例（同步，不自动创建）."""
        channel_key = channel_id or "default"
        effective_session_id = session_id or "default"
        return self._lookup_agent(channel_key, mode, effective_session_id)

    async def reload_agents_config(self, config, env) -> None:
        """重新加载所有 agent 配置；单个 agent 失败不影响其他 agent。"""
        # 保存配置（用于后续创建的 agent 重放）
        self._latest_env_overrides = dict(env) if isinstance(env, dict) else {}
        self._latest_config_base = config

        _apply_env_overrides(self._latest_env_overrides)

        for channel_id, channel_agents in list(self.agents.items()):
            if not isinstance(channel_agents, dict):
                logger.warning(
                    "[AgentManager] unexpected agents entry for channel %s: %r",
                    channel_id, type(channel_agents),
                )
                continue
            channel_ok = True
            for mode, mode_agents in channel_agents.items():
                for session_id, agent in mode_agents.items():
                    try:
                        await agent.reload_agent_config(
                            config_base=config,
                            env_overrides=env,
                        )
                    except Exception as exc:
                        channel_ok = False
                        logger.error(
                            "[AgentManager] reload_agent_config failed channel=%s "
                            "mode=%s session=%s: %s",
                            channel_id, mode, session_id, exc,
                        )
            if channel_ok:
                logger.info("[AgentManager] channel %s reload agent config success.", channel_id)

    async def _maybe_apply_code_mode_switch(self, agent: Any, request: Any, mode_full: str) -> None:
        """code 模式下在真实 session 上执行 switch_mode，确保 state 持久化。

        提取出来供 ``process_message`` 与 ``process_message_stream`` 共享。
        """
        from openjiuwen.core.single_agent import create_agent_session

        parts = mode_full.split(".")
        sub_mode = parts[1] if len(parts) > 1 else "plan"
        session = create_agent_session(
            session_id=getattr(request, "session_id", None),
            card=agent.get_instance().card,
        )
        session_label = getattr(session, "session_id", "unknown")
        logger.info(
            "[AgentManager] Code模式switch开始: session=%s", session_label,
            extra={'user_visible': 'critical'},
        )
        await session.pre_run(inputs=None)  # 从 checkpointer 加载历史 state
        agent.get_instance().switch_mode(session=session, mode=sub_mode)
        state = agent.get_instance().load_state(session)
        session.update_state({"deep_agent_state": state.to_session_dict()})
        logger.info(
            "[AgentManager] Code模式switch完成: session=%s", session_label,
            extra={'user_visible': 'critical'},
        )
        await session.post_run()  # 写入 checkpointer

    async def _resolve_agent_for_request(self, request: Any) -> tuple[Any, str]:
        """根据 request 解析或创建 agent；返回 (agent, mode_full)。"""
        channel_id, session_id, mode_full, mode, workspace_dir = _parse_request_meta(request)

        agent = await self.get_agent(
            channel_id=channel_id,
            mode=mode,
            workspace_dir=workspace_dir,
            session_id=session_id,
        )
        if agent is None:
            raise RuntimeError(f"[AgentManager] No agent available for channel {channel_id}")

        if mode == "code":
            await self._maybe_apply_code_mode_switch(agent, request, mode_full)
        return agent, mode_full

    async def process_message(self, request: Any) -> Any:
        """处理非流式请求."""
        logger.debug(
            "[AgentManager] process_message 开始 | request_id=%s | channel=%s",
            getattr(request, "request_id", ""),
            getattr(request, "channel_id", ""),
        )
        agent, _ = await self._resolve_agent_for_request(request)
        return await agent.process_message(request)

    async def process_message_stream(self, request: Any) -> AsyncIterator[Any]:
        """处理流式请求."""
        logger.debug(
            "[AgentManager] process_message_stream 开始 | request_id=%s | channel=%s",
            getattr(request, "request_id", ""),
            getattr(request, "channel_id", ""),
        )
        agent, _ = await self._resolve_agent_for_request(request)
        async for chunk in agent.process_message_stream(request):
            yield chunk

    async def reload_agent_config(
            self,
            channel_id: str = "",
            config_base: Any = None,
            env_overrides: dict | None = None,
    ) -> None:
        """重新加载指定通道的 Agent 配置."""
        agent = await self.get_agent(channel_id)
        if agent is None:
            raise RuntimeError(f"[AgentManager] No agent available for channel {channel_id}")
        await agent.reload_agent_config(
            config_base=config_base,
            env_overrides=env_overrides,
        )

    async def cleanup_session(
        self,
        channel_id: str,
        mode: str,
        session_id: str,
    ) -> None:
        """清理指定 session 的 Agent 实例（锁内执行，避免与 get_agent 竞态）."""
        async with self._get_lock():
            session_agents = self.agents.get(channel_id, {}).get(mode, {})
            agent = session_agents.pop(session_id, None)

        if agent is None:
            return

        try:
            if hasattr(agent, "cleanup"):
                await agent.cleanup()
            logger.info(
                "[AgentManager] Session cleaned up: channel=%s mode=%s session=%s",
                channel_id, mode, session_id,
            )
        except Exception as exc:
            logger.warning("[AgentManager] Session cleanup failed: %s", exc)

    async def cleanup(self) -> None:
        """清理所有 agent 实例。cleanup 失败的 agent 保留在 self.agents 以便后续重试。"""
        async with self._get_lock():
            for channel_key in list(self.agents.keys()):
                channel_agents = self.agents[channel_key]
                failed_modes: dict[str, dict[str, Any]] = {}
                for mode, mode_agents in channel_agents.items():
                    failed_sessions: dict[str, Any] = {}
                    for session_id, agent in mode_agents.items():
                        if not hasattr(agent, "cleanup"):
                            continue
                        try:
                            await agent.cleanup()
                        except Exception as exc:
                            logger.warning(
                                "[AgentManager] Agent cleanup failed channel=%s mode=%s session=%s: %s",
                                channel_key, mode, session_id, exc,
                            )
                            failed_sessions[session_id] = agent
                    if failed_sessions:
                        failed_modes[mode] = failed_sessions
                if failed_modes:
                    # 保留 cleanup 失败的 agent，便于后续诊断/重试
                    self.agents[channel_key] = failed_modes
                else:
                    del self.agents[channel_key]
        self._client_capabilities_by_channel.clear()
        logger.info("[AgentManager] All agents cleaned up for tenant %s", self.agent_id)

    def is_working(self) -> bool:
        """返回租户是否正在工作；任意 Agent 工作即返回 True。"""
        if not self.agents:
            return False

        for channel_agents in self.agents.values():
            if not isinstance(channel_agents, dict):
                continue
            for mode_agents in channel_agents.values():
                for agent in mode_agents.values():
                    if not hasattr(agent, "is_working"):
                        continue
                    try:
                        if agent.is_working():
                            return True
                    except Exception as exc:
                        logger.warning("Get working status failed, %s", exc)
                        continue
        return False
