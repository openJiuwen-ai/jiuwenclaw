# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""AgentManager - 管理单个租户内的 Agent 实例."""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import Any, TYPE_CHECKING

from jiuwenclaw.e2a.acp.protocol import build_acp_initialize_result

if TYPE_CHECKING:
    from jiuwenclaw.agentserver.interface import JiuWenClaw

logger = logging.getLogger(__name__)


ACP_DEFAULT_CAPABILITIES: dict[str, Any] = build_acp_initialize_result()


def _build_acp_agent_config(extra_config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the dedicated ACP agent profile config.

    ACP sessions should use ACP-native filesystem/terminal tools instead of the
    default openjiuwen filesystem/bash toolchain.
    """
    config: dict[str, Any] = {
        "agent_name": "acp_agent",
        "channel_id": "acp",
        "tool_profile": "acp",
        "enable_filesystem_rail": True,
    }
    if isinstance(extra_config, dict):
        config.update(extra_config)
    config["channel_id"] = "acp"
    config["tool_profile"] = "acp"
    return config


class AgentManager:
    """管理单个租户内的 Agent 实例.

    支持多种通道:
    - "acp": ACP 协议通道
    - "default": 默认通道
    """

    def __init__(self, agent_id: str, service_id: str, user_workspace_dir: Path | None = None) -> None:
        """初始化 AgentManager.

        Args:
            agent_id: agent名称/路径
            service_id: 服务ID（chat_id + bot_app_id 组合）
            user_workspace_dir: 用户工作目录路径
        """
        self.agents: dict[str, dict[str, "JiuWenClaw"]] = {}
        self._client_capabilities_by_channel: dict[str, dict[str, Any]] = {}
        self._latest_env_overrides: dict[str, Any] = {}
        self.agent_id = agent_id
        self.service_id = service_id
        self.user_workspace_dir = user_workspace_dir
        logger.info(
            "[AgentManager] 初始化: agent_id=%s, service_id=%s, workspace=%s",
            agent_id,
            service_id,
            user_workspace_dir,
        )

    # pylint: disable=protected-access
    async def _create_agent(
        self, agent_key: str, mode: str = "agent", config: dict[str, Any] | None = None
    ) -> "JiuWenClaw":
        """创建 Agent 实例.

        Args:
            agent_key: Agent 键（如 "acp" 或 "default"）
            config: 可选配置

        Returns:
            JiuWenClaw 实例
        """
        from jiuwenclaw.agentserver.interface import JiuWenClaw

        for env_key, env_value in self._latest_env_overrides.items():
            key = str(env_key)
            if env_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(env_value)
        logger.info("[AgentManager] Creating %s agent (mode=%s)", agent_key, mode)

        agent = JiuWenClaw(user_workspace_dir=str(self.user_workspace_dir) if self.user_workspace_dir else None)
        agent._agent_name = f"agent_{self.agent_id}_{self.service_id}_{agent_key}"
        await agent.create_instance(config, mode=mode)
        self.agents.setdefault(agent_key, {})[mode] = agent
        logger.info("[AgentManager] %s agent created for tenant %s", agent_key, self.agent_id)
        return agent

    async def initialize(
        self, channel_id: str = "", extra_config: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """初始化 AgentManager.

        对于 ACP 通道，创建 agent 并返回 capabilities。

        Args:
            channel_id: 通道 ID
            extra_config: 额外配置（如 protocol_version, client_capabilities）

        Returns:
            对于 ACP 通道，返回 capabilities；对于其他通道，返回 None
        """
        if channel_id == "acp":
            logger.info("[AgentManager] ACP initialize for tenant %s", self.agent_id)
            if extra_config:
                client_capabilities = extra_config.get("client_capabilities")
                if isinstance(client_capabilities, dict):
                    self._client_capabilities_by_channel["acp"] = dict(client_capabilities)

            if "acp" in self.agents:
                logger.info("[AgentManager] Resetting ACP agent for tenant %s", self.agent_id)
                for agent in self.agents.get("acp", {}).values():
                    if hasattr(agent, "cleanup"):
                        try:
                            await agent.cleanup()
                        except Exception as e:
                            logger.warning("[AgentManager] ACP agent cleanup failed: %s", e)
                del self.agents["acp"]

            config = _build_acp_agent_config(extra_config)
            await self._create_agent("acp", "code", config)

            return ACP_DEFAULT_CAPABILITIES.copy()
        return None

    async def cancel_all_inflight_work(self, reason: str = "[gateway ws disconnect] ") -> None:
        """Gateway 与 AgentServer 的 WebSocket 断开时：取消所有已创建 Agent 实例上的在途任务。"""
        for modes in list(self.agents.values()):
            for agent in list(modes.values()):
                try:
                    await agent.cancel_inflight_work(reason)
                except Exception:
                    logger.exception("[AgentManager] cancel_inflight_work failed")

    def get_client_capabilities(self, channel_id: str = "") -> dict[str, Any]:
        """获取指定通道的客户端能力.

        Args:
            channel_id: 通道 ID

        Returns:
            客户端能力字典
        """
        channel_key = str(channel_id or "").strip()
        caps = self._client_capabilities_by_channel.get(channel_key)
        return dict(caps) if isinstance(caps, dict) else {}

    async def create_session(self, channel_id: str = "", session_id: str | None = None) -> str:
        """创建会话.

        Args:
            channel_id: 通道 ID
            session_id: 可选的会话 ID

        Returns:
            会话 ID
        """
        explicit_session_id = str(session_id or "").strip()
        if explicit_session_id:
            logger.info(
                "[AgentManager] session ensured: channel_id=%s session_id=%s",
                channel_id,
                explicit_session_id,
            )
            return explicit_session_id
        if channel_id == "acp":
            session_id = f"acp_{uuid.uuid4().hex[:8]}"
            logger.info("[AgentManager] ACP session created: session_id=%s", session_id)
            return session_id
        return "default"

    async def get_agent(
            self,
            channel_id: str = "",
            mode: str = "agent",
            workspace_dir: str = None
    ) -> "JiuWenClaw | None":
        """获取 Agent 实例（自动创建）.

        如果 agent 不存在，会自动创建。

        Args:
            channel_id: 通道 ID
            mode: 每个模式对应的实例
            workspace_dir: project dir

        Returns:
            JiuWenClaw | None: Agent 实例
        """
        if channel_id in self.agents and mode in self.agents[channel_id]:
            return self.agents[channel_id][mode]
        else:
            config = {"workspace_dir": workspace_dir} if workspace_dir else {}
            if channel_id == "acp":
                config = {
                    **config,
                    **_build_acp_agent_config()
                }
            await self._create_agent(channel_id, mode, config)
        return self.agents.get(channel_id, {}).get(mode)

    def get_agent_nowait(self, channel_id: str = "") -> "JiuWenClaw | None":
        """获取 Agent 实例（同步，不自动创建）.

        Args:
            channel_id: 通道 ID

        Returns:
            JiuWenClaw | None: Agent 实例，如果不存在则返回 None
        """
        channel_key = channel_id or "default"
        channel_agents = self.agents.get(channel_key, {})
        if isinstance(channel_agents, dict):
            return channel_agents.get("agent") or next(iter(channel_agents.values()), None)
        return None

    async def reload_agents_config(self, config, env) -> None:
        """reload agent config"""
        self._latest_env_overrides = dict(env) if isinstance(env, dict) else {}
        for env_key, env_value in self._latest_env_overrides.items():
            key = str(env_key)
            if env_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(env_value)

        for channel_id, agents in self.agents.items():
            if not isinstance(agents, dict):
                logger.warning(
                    "[AgentManager] unexpected agents entry for channel %s: %r",
                    channel_id,
                    type(agents),
                )
                continue
            for _, agent in agents.items():
                await agent.reload_agent_config(
                    config_base=config,
                    env_overrides=env,
                )
            logger.info(f"channel {channel_id} reload agent config success.")

    async def process_message(self, request: Any) -> Any:
        """处理非流式请求.

        Args:
            request: AgentRequest 对象

        Returns:
            AgentResponse 对象
        """
        channel_id = getattr(request, "channel_id", "")
        params = getattr(request, "params", {}) if isinstance(getattr(request, "params", {}), dict) else {}
        mode_full = params.get("mode", "agent.plan")
        mode = str(mode_full).split(".")[0] if mode_full else "agent"
        workspace_dir = params.get("workspace_dir")

        agent = await self.get_agent(
            channel_id=channel_id,
            mode=mode,
            workspace_dir=workspace_dir,
        )
        if agent is None:
            raise RuntimeError(f"[AgentManager] No agent available for channel {channel_id}")

        # code 模式：在真实 session 上执行 switch_mode，确保 state 持久化
        if mode == "code":
            from openjiuwen.core.single_agent import create_agent_session

            parts = str(mode_full).split(".")
            sub_mode = parts[1] if len(parts) > 1 else "plan"
            session = create_agent_session(
                session_id=getattr(request, "session_id", None),
                card=agent.get_instance().card,
            )
            await session.pre_run(inputs=None)  # 从 checkpointer 加载历史 state
            agent.get_instance().switch_mode(session=session, mode=sub_mode)
            state = agent.get_instance().load_state(session)
            session.update_state({"deep_agent_state": state.to_session_dict()})
            await session.post_run()  # 写入 checkpointer

        return await agent.process_message(request)

    async def process_message_stream(self, request: Any):
        """处理流式请求.

        Args:
            request: AgentRequest 对象

        Yields:
            AgentResponseChunk 对象
        """
        channel_id = getattr(request, "channel_id", "")
        params = getattr(request, "params", {}) if isinstance(getattr(request, "params", {}), dict) else {}
        mode_full = params.get("mode", "agent.plan")
        mode = str(mode_full).split(".")[0] if mode_full else "agent"
        workspace_dir = params.get("workspace_dir")

        agent = await self.get_agent(
            channel_id=channel_id,
            mode=mode,
            workspace_dir=workspace_dir,
        )
        if agent is None:
            raise RuntimeError(f"[AgentManager] No agent available for channel {channel_id}")

        # code 模式：在真实 session 上执行 switch_mode，确保 state 持久化
        if mode == "code":
            from openjiuwen.core.single_agent import create_agent_session

            parts = str(mode_full).split(".")
            sub_mode = parts[1] if len(parts) > 1 else "plan"
            session = create_agent_session(
                session_id=getattr(request, "session_id", None),
                card=agent.get_instance().card,
            )
            await session.pre_run(inputs=None)  # 从 checkpointer 加载历史 state
            agent.get_instance().switch_mode(session=session, mode=sub_mode)
            state = agent.get_instance().load_state(session)
            session.update_state({"deep_agent_state": state.to_session_dict()})
            await session.post_run()  # 写入 checkpointer

        async for chunk in agent.process_message_stream(request):
            yield chunk

    async def reload_agent_config(
            self,
            channel_id: str = "",
            config_base: Any = None,
            env_overrides: dict | None = None
    ) -> None:
        """重新加载指定通道的 Agent 配置.

        Args:
            channel_id: 通道 ID
            config_base: 基础配置
            env_overrides: 环境变量覆盖
        """
        agent = await self.get_agent(channel_id)
        if agent is None:
            raise RuntimeError(f"[AgentManager] No agent available for channel {channel_id}")
        await agent.reload_agent_config(
            config_base=config_base,
            env_overrides=env_overrides
        )

    async def cleanup(self) -> None:
        """清理所有 agent 实例."""
        for key, agents in list(self.agents.items()):
            for agent in agents.values():
                if hasattr(agent, "cleanup"):
                    try:
                        await agent.cleanup()
                    except Exception as e:
                        logger.warning("[AgentManager] Agent cleanup failed: %s", e)
            del self.agents[key]
        self._client_capabilities_by_channel.clear()
        logger.info("[AgentManager] All agents cleaned up for tenant %s", self.agent_id)

    def is_working(self) -> dict:
        """返回 Agent 是否正在工作的状态.
        Returns:
            dict: 工作状态信息，包含 working, initialized, active_tasks,
                stream_tasks, pending_messages, active_sessions 字段.
                如果 Agent 未初始化，返回 working=False.
        """
        agent = self.agents.get("default_session")
        if agent is None:
            return {
                "working": False,
                "initialized": False,
                "model_configured": False,
                "active_tasks": 0,
                "stream_tasks": 0,
                "pending_messages": 0,
                "active_sessions": [],
            }
        return agent.is_working()