# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""AgentManager - 管理 Agent 实例."""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, TYPE_CHECKING

from jiuwenclaw.common.e2a.acp.protocol import build_acp_initialize_result
from jiuwenclaw.agents.harness.team import get_team_manager
from jiuwenclaw.common.config import get_config

if TYPE_CHECKING:
    from jiuwenclaw.server.runtime.agent_adapter.interface import JiuWenClaw


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
    """管理多个 Agent 实例.

    支持多种通道:
    - "acp": ACP 协议通道
    - "default": 默认通道
    """

    def __init__(self) -> None:
        self.agents: dict[str, dict[str, "JiuWenClaw"]] = {}
        self._client_capabilities_by_channel: dict[str, dict[str, Any]] = {}
        self._latest_env_overrides: dict[str, Any] = {}

    async def _create_agent(
        self, agent_key: str, mode: str = "agent", config: dict[str, Any] | None = None, sub_mode: str = None
    ) -> "JiuWenClaw":
        """创建 Agent 实例.

        Args:
            agent_key: Agent 键（如 "acp" 或 "default"）
            config: 可选配置
            sub_mode: 子模式
        Returns:
            JiuWenClaw 实例
        """
        from jiuwenclaw.server.runtime.agent_adapter.interface import JiuWenClaw

        for env_key, env_value in self._latest_env_overrides.items():
            key = str(env_key)
            if env_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(env_value)
        logger.info("[AgentManager] Creating %s agent (mode=%s, sub_mode=%s)", agent_key, mode, sub_mode)
        agent = JiuWenClaw()
        await agent.create_instance(config, mode=mode, sub_mode=sub_mode)
        self.agents.setdefault(agent_key, {})[mode] = agent
        logger.info("[AgentManager] %s agent created", agent_key)
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
            logger.info("[AgentManager] ACP initialize")
            if extra_config:
                client_capabilities = extra_config.get("client_capabilities")
                if isinstance(client_capabilities, dict):
                    self._client_capabilities_by_channel["acp"] = dict(client_capabilities)

            if "acp" in self.agents:
                logger.info("[AgentManager] Resetting ACP agent")
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
        channel_key = str(channel_id or "").strip()
        caps = self._client_capabilities_by_channel.get(channel_key)
        return dict(caps) if isinstance(caps, dict) else {}

    async def create_session(self, channel_id: str = "", session_id: str | None = None) -> str:
        """创建会话.

        Args:
            channel_id: 通道 ID

        Returns:
            会话 ID
        """
        explicit_session_id = str(session_id or "").strip()
        if explicit_session_id:
            logger.info("[AgentManager] session ensured: channel_id=%s session_id=%s", channel_id, explicit_session_id)
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
            project_dir: str = None,
            sub_mode: str = None
    ) -> "JiuWenClaw | None":
        """获取 Agent 实例（自动创建）.

        如果 agent 不存在，会自动创建（仅用于非 ACP 场景）。

        Args:
            channel_id: 通道 ID
            mode: 每个模式对应的实例
            project_dir: user project dir (e.g. trusted_dirs[0])
            sub_mode: 子模式

        Returns:
            JiuWenClaw | None: Agent 实例
        """
        if channel_id in self.agents and mode in self.agents[channel_id]:
            return self.agents[channel_id][mode]
        else:
            config = {}
            if project_dir:
                config["project_dir"] = project_dir
            if channel_id == "acp":
                config = {
                    **config,
                    **_build_acp_agent_config()
                }
            await self._create_agent(channel_id, mode, config, sub_mode)
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
            try:
                team_config = config if isinstance(config, dict) else get_config()
                await get_team_manager(channel_id).update_evolution_config(team_config)
            except Exception as exc:
                logger.warning(
                    "[AgentManager] team evolution config hot-update failed: channel=%s error=%s",
                    channel_id,
                    exc,
                )
            logger.info(f"channel {channel_id} reload agent config success.")

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
        logger.info("[AgentManager] All agents cleaned up")
