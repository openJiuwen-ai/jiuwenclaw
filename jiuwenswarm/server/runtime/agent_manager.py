# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""AgentManager - 管理 Agent 实例."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Any, TYPE_CHECKING

from jiuwenswarm.common.e2a.acp.protocol import build_acp_initialize_result
from jiuwenswarm.agents.harness.team import get_team_manager
from jiuwenswarm.common.config import get_config

if TYPE_CHECKING:
    from jiuwenswarm.server.runtime.agent_adapter.interface import JiuWenSwarm


logger = logging.getLogger(__name__)


ACP_DEFAULT_CAPABILITIES: dict[str, Any] = build_acp_initialize_result()


def _normalize_channel_id(channel_id: str | None) -> str:
    return str(channel_id or "default").strip() or "default"


def _normalize_mode(mode: str | None) -> str:
    return str(mode or "agent").strip() or "agent"


def _normalize_sub_mode(sub_mode: str | None) -> str:
    return str(sub_mode or "").strip()


def _normalize_project_dir(project_dir: str | None) -> str:
    raw = str(project_dir or "").strip()
    if not raw:
        return ""
    try:
        return os.path.normcase(os.path.abspath(os.path.expanduser(raw)))
    except Exception:
        return raw


def _make_agent_cache_key(mode: str | None, sub_mode: str | None, project_dir: str | None) -> str:
    mode_key = _normalize_mode(mode)
    sub_mode_key = _normalize_sub_mode(sub_mode)
    project_key = _normalize_project_dir(project_dir)
    return f"{mode_key}:{sub_mode_key}:{project_key}"


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
        self.agents: dict[str, dict[str, "JiuWenSwarm"]] = {}
        # 记录每个 (channel_id, mode) 的创建参数, 便于 recreate_agent 立刻重建
        self._agent_create_params: dict[str, dict[str, dict[str, Any]]] = {}
        self._client_capabilities_by_channel: dict[str, dict[str, Any]] = {}
        self._latest_env_overrides: dict[str, Any] = {}
        # reload 串行锁: 防止并发 reload 叠加导致内存爆炸
        self._reload_lock: asyncio.Lock = asyncio.Lock()
        self._last_reload_fingerprint: str | None = None

    @staticmethod
    def _reload_fingerprint(
        config: Any,
        env: Any,
        *,
        agent_topology: Any,
        target_channel_id: str | None,
        target_session_id: str | None,
    ) -> str:
        payload = {
            "config": config,
            "env": env if isinstance(env, dict) else {},
            "agent_topology": agent_topology,
            "target_channel_id": str(target_channel_id or "").strip() or None,
            "target_session_id": str(target_session_id or "").strip() or None,
        }
        return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=repr)

    def _reload_agent_topology(self, target_channel_id: str | None = None) -> dict[str, list[tuple[str, int]]]:
        channel_items = (
            [(target_channel_id, self.agents.get(target_channel_id, {}))]
            if target_channel_id
            else self.agents.items()
        )
        topology: dict[str, list[tuple[str, int]]] = {}
        for channel_id, agents in channel_items:
            if not isinstance(agents, dict):
                topology[str(channel_id)] = []
                continue
            topology[str(channel_id)] = sorted((str(agent_key), id(agent)) for agent_key, agent in agents.items())
        return topology

    async def _create_agent(
        self,
        agent_key: str,
        mode: str = "agent",
        config: dict[str, Any] | None = None,
        sub_mode: str = None,
        cache_key: str | None = None,
    ) -> "JiuWenSwarm":
        """创建 Agent 实例.

        Args:
            agent_key: Agent 键（如 "acp" 或 "default"）
            config: 可选配置
            sub_mode: 子模式
        Returns:
            JiuWenSwarm 实例
        """
        from jiuwenswarm.server.runtime.agent_adapter.interface import JiuWenSwarm

        for env_key, env_value in self._latest_env_overrides.items():
            key = str(env_key)
            if env_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(env_value)
        channel_key = _normalize_channel_id(agent_key)
        mode_key = _normalize_mode(mode)
        sub_mode_key = _normalize_sub_mode(sub_mode)
        project_dir = _normalize_project_dir((config or {}).get("project_dir"))
        if project_dir:
            config = dict(config or {})
            config["project_dir"] = project_dir
        agent_cache_key = cache_key or _make_agent_cache_key(mode_key, sub_mode_key, project_dir)
        logger.info(
            "[AgentManager] Creating %s agent (mode=%s, sub_mode=%s, project_dir=%s)",
            channel_key,
            mode_key,
            sub_mode_key or None,
            project_dir or None,
        )
        agent = JiuWenSwarm()
        await agent.create_instance(config, mode=mode_key, sub_mode=sub_mode_key or None)
        setattr(agent, "_jiuwenswarm_agent_cache_key", agent_cache_key)
        setattr(agent, "_jiuwenswarm_agent_mode", mode_key)
        setattr(agent, "_jiuwenswarm_agent_sub_mode", sub_mode_key)
        setattr(agent, "_jiuwenswarm_agent_project_dir", project_dir)
        self.agents.setdefault(channel_key, {})[agent_cache_key] = agent
        # 记录创建参数, recreate_agent() 时可原样复用
        self._agent_create_params.setdefault(channel_key, {})[agent_cache_key] = {
            "mode": mode_key,
            "sub_mode": sub_mode_key or None,
            "config": dict(config or {}),
            "cache_key": agent_cache_key,
        }
        logger.info("[AgentManager] %s agent created cache_key=%s", channel_key, agent_cache_key)
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
        channel_key = _normalize_channel_id(channel_id)
        if channel_key == "acp":
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

    async def cleanup_session_runtime(self, *, channel_id: str = "", session_id: str) -> bool:
        """Release in-memory runtime for one session across existing channel agents."""
        sid = str(session_id or "").strip()
        if not sid:
            return False
        channel_key = _normalize_channel_id(channel_id)
        channel_agents = self.agents.get(channel_key, {})
        if not isinstance(channel_agents, dict):
            return False

        cleaned = False
        for agent in list(channel_agents.values()):
            cleanup_fn = getattr(agent, "cleanup_session_runtime", None)
            if not callable(cleanup_fn):
                continue
            try:
                cleaned = bool(await cleanup_fn(sid)) or cleaned
            except Exception:
                logger.exception(
                    "[AgentManager] cleanup_session_runtime failed: channel_id=%s session_id=%s",
                    channel_key,
                    sid,
                )
        return cleaned

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
        channel_key = _normalize_channel_id(channel_id)
        if channel_key == "acp":
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
    ) -> "JiuWenSwarm | None":
        """获取 Agent 实例（自动创建）.

        如果 agent 不存在，会自动创建（仅用于非 ACP 场景）。

        Args:
            channel_id: 通道 ID
            mode: 每个模式对应的实例
            project_dir: user project dir (e.g. trusted_dirs[0])
            sub_mode: 子模式

        Returns:
            JiuWenSwarm | None: Agent 实例
        """
        channel_key = _normalize_channel_id(channel_id)
        mode_key = _normalize_mode(mode)
        sub_mode_key = _normalize_sub_mode(sub_mode)
        project_key = _normalize_project_dir(project_dir)
        cache_key = _make_agent_cache_key(mode_key, sub_mode_key, project_key)
        channel_agents = self.agents.get(channel_key, {})
        if cache_key in channel_agents:
            return channel_agents[cache_key]

        config = {}
        if project_key:
            config["project_dir"] = project_key
        if channel_key == "acp":
            config = {
                **config,
                **_build_acp_agent_config()
            }
        return await self._create_agent(
            channel_key,
            mode_key,
            config,
            sub_mode_key or None,
            cache_key=cache_key,
        )

    def get_agent_nowait(
        self,
        channel_id: str = "",
        mode: str | None = None,
        project_dir: str | None = None,
        sub_mode: str | None = None,
    ) -> "JiuWenSwarm | None":
        """获取 Agent 实例（同步，不自动创建）.

        Args:
            channel_id: 通道 ID

        Returns:
            JiuWenSwarm | None: Agent 实例，如果不存在则返回 None
        """
        channel_key = _normalize_channel_id(channel_id)
        channel_agents = self.agents.get(channel_key, {})
        if not isinstance(channel_agents, dict):
            return None

        if mode is not None or project_dir is not None or sub_mode is not None:
            cache_key = _make_agent_cache_key(mode, sub_mode, project_dir)
            agent = channel_agents.get(cache_key)
            if agent is not None:
                return agent

        requested_mode = _normalize_mode(mode) if mode is not None else ""
        requested_sub_mode = _normalize_sub_mode(sub_mode) if sub_mode is not None else ""
        requested_project_dir = _normalize_project_dir(project_dir) if project_dir is not None else ""
        for agent in channel_agents.values():
            if requested_mode and getattr(agent, "_jiuwenswarm_agent_mode", "") != requested_mode:
                continue
            if requested_sub_mode and getattr(agent, "_jiuwenswarm_agent_sub_mode", "") != requested_sub_mode:
                continue
            if requested_project_dir and getattr(agent, "_jiuwenswarm_agent_project_dir", "") != requested_project_dir:
                continue
            return agent

        if mode is None and project_dir is None and sub_mode is None:
            for agent in channel_agents.values():
                if getattr(agent, "_jiuwenswarm_agent_mode", "") == "agent":
                    return agent
            return next(iter(channel_agents.values()), None)
        return None

    async def broadcast_package_change_to_single_agents(
        self,
        package_id: str,
        config_path: str,
        operation: str,
        channel_id: str | None = None,
        skip_instance: Any | None = None,
    ) -> None:
        """Broadcast package change to agent.fast and agent.plan instances only.

        This ensures deactivation affects all relevant agent instances, not just the current one.
        Does NOT affect team mode agents.

        Args:
            package_id: The package ID being activated/deactivated.
            config_path: Absolute path to harness_config.yaml.
            operation: "activate" or "deactivate".
            channel_id: Optional channel ID to limit broadcast scope.
            skip_instance: Optional agent instance to skip (already processed by caller).
        """
        target_modes = {"agent", "agent.fast", "agent.plan"}

        for channel_key, channel_agents in self.agents.items():
            # Limit to specific channel if provided
            if channel_id and channel_key != _normalize_channel_id(channel_id):
                continue

            for cache_key, agent in channel_agents.items():
                # Parse mode from cache_key: "mode:sub_mode:project"
                mode = cache_key.split(":")[0] if ":" in cache_key else ""
                if mode not in target_modes:
                    continue  # Skip team and other modes

                instance = agent.get_instance()
                if instance is None:
                    continue

                fanout = getattr(
                    agent,
                    "apply_package_change_to_session_adapters",
                    None,
                )
                if callable(fanout):
                    try:
                        await fanout(operation, config_path)
                    except Exception as exc:
                        logger.warning(
                            "[AgentManager] session-adapter fanout failed for "
                            "package %s on agent %s: %s",
                            package_id,
                            cache_key,
                            exc,
                        )

                # Skip the instance that was already processed by the caller
                if skip_instance is not None and instance is skip_instance:
                    logger.debug(
                        "[AgentManager] Skipping already processed agent %s for package %s",
                        cache_key,
                        package_id,
                    )
                    continue

                try:
                    if operation == "deactivate":
                        await instance.unload_harness_config(config_path)
                        logger.info(
                            "[AgentManager] Unloaded package %s from agent %s (channel=%s)",
                            package_id,
                            cache_key,
                            channel_key,
                        )
                    elif operation == "activate":
                        await instance.load_harness_config(config_path)
                        logger.info(
                            "[AgentManager] Loaded package %s to agent %s (channel=%s)",
                            package_id,
                            cache_key,
                            channel_key,
                        )
                except Exception as exc:
                    logger.warning(
                        "[AgentManager] Failed to %s package %s on agent %s: %s",
                        operation,
                        package_id,
                        cache_key,
                        exc,
                    )

    async def reload_agents_config(
        self,
        config,
        env,
        *,
        target_channel_id: str | None = None,
        target_session_id: str | None = None,
    ) -> None:
        """reload agent config.

        使用 ``self._reload_lock`` 串行化, 避免高频触发(如批量 MCP 增删)时多个
        reload 并发叠加, 同时重建大量 agent 实例导致内存暴涨被 OOM kill.
        """
        async with self._reload_lock:
            self._latest_env_overrides = dict(env) if isinstance(env, dict) else {}
            for env_key, env_value in self._latest_env_overrides.items():
                key = str(env_key)
                if env_value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = str(env_value)

            target_channel = str(target_channel_id or "").strip() or None
            target_session = str(target_session_id or "").strip() or None
            effective_config = config
            if effective_config is None:
                try:
                    effective_config = get_config()
                except Exception:
                    effective_config = None
            fingerprint = self._reload_fingerprint(
                effective_config,
                self._latest_env_overrides,
                agent_topology=self._reload_agent_topology(target_channel),
                target_channel_id=target_channel,
                target_session_id=target_session,
            )
            if fingerprint == self._last_reload_fingerprint:
                logger.info(
                    "[AgentManager] reload agent config skipped: unchanged scope/config/env "
                    "(channel=%s session=%s)",
                    target_channel or "*",
                    target_session or "*",
                )
                return
            channel_items = (
                [(target_channel, self.agents.get(target_channel, {}))]
                if target_channel
                else list(self.agents.items())
            )
            reload_completed = True

            for channel_id, agents in channel_items:
                if not isinstance(agents, dict):
                    reload_completed = False
                    logger.warning(
                        "[AgentManager] unexpected agents entry for channel %s: %r",
                        channel_id,
                        type(agents),
                    )
                    continue
                for _, agent in list(agents.items()):
                    reload_kwargs = {
                        "config_base": effective_config,
                        "env_overrides": self._latest_env_overrides,
                    }
                    if target_session:
                        reload_kwargs["target_session_id"] = target_session
                    await agent.reload_agent_config(**reload_kwargs)
                try:
                    team_config = effective_config if isinstance(effective_config, dict) else get_config()
                    await get_team_manager(channel_id).update_evolution_config(team_config)
                except Exception as exc:
                    reload_completed = False
                    logger.warning(
                        "[AgentManager] team evolution config hot-update failed: channel=%s error=%s",
                        channel_id,
                        exc,
                    )
                logger.info(f"channel {channel_id} reload agent config success.")
            if reload_completed:
                self._last_reload_fingerprint = fingerprint

    async def recreate_agent(self, channel_id: str, *, immediate: bool = True) -> None:
        """重建指定 channel 的所有 agent 实例.

        用于 ``/sandbox enable/disable`` 等需要重新构建 ``SysOperationCard`` 的场景.
        步骤:
        1. 备份现有 (mode -> create_params) 映射;
        2. cleanup 并删除现有 agent 实例;
        3. 若 ``immediate=True``, 依据备份的参数立即重新调用 ``_create_agent()``,
           使新的 SysOperation 生效不必等到下次 ``get_agent()``;
           ``immediate=False`` 则按原行为, 下次 ``get_agent()`` 时再重建.

        Args:
            channel_id: 通道 ID.
            immediate: 是否立即重建 (默认 True).
        """
        channel_key = channel_id or "default"
        agents = self.agents.get(channel_key)
        if not agents:
            logger.info(
                "[AgentManager] recreate_agent: no active agent on channel %s",
                channel_key,
            )

        # 1. 备份 (mode -> create_params)
        existing_modes = list(agents.keys())
        backup_params: dict[str, dict[str, Any]] = {}
        channel_params = self._agent_create_params.get(channel_key) or {}
        for mode_key in existing_modes:
            params = channel_params.get(mode_key)
            if params is None:
                # 未记录创建参数 (理论上 _create_agent 一定记录), 兜底使用 mode_key
                params = {"mode": mode_key, "sub_mode": None, "config": None}
            backup_params[mode_key] = dict(params)

        # 2. cleanup + 删除
        for mode_key, agent in list(agents.items()):
            if hasattr(agent, "cleanup"):
                try:
                    await agent.cleanup()
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[AgentManager] recreate cleanup failed (mode=%s): %s",
                        mode_key,
                        exc,
                    )
        del self.agents[channel_key]
        self._agent_create_params.pop(channel_key, None)
        logger.info(
            "[AgentManager] recreate_agent: channel %s agents dropped (modes=%s)",
            channel_key,
            existing_modes,
        )

        if not immediate:
            logger.info(
                "[AgentManager] recreate_agent: channel %s will rebuild on next get_agent()",
                channel_key,
            )

        # 3. 立即按原参数重建
        for mode_key, params in backup_params.items():
            try:
                await self._create_agent(
                    channel_key,
                    mode=params.get("mode") or mode_key,
                    config=params.get("config"),
                    sub_mode=params.get("sub_mode"),
                    cache_key=params.get("cache_key") or mode_key,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "[AgentManager] recreate_agent: rebuild failed (mode=%s): %s",
                    mode_key,
                    exc,
                )
        logger.info(
            "[AgentManager] recreate_agent: channel %s rebuilt (modes=%s)",
            channel_key,
            existing_modes,
        )

    async def process_message(self, request: Any) -> Any:
        """处理非流式请求.

        Args:
            request: AgentRequest 对象

        Returns:
            AgentResponse 对象
        """
        try:
            channel_id = getattr(request, "channel_id", "")
            params = getattr(request, "params", {}) if isinstance(getattr(request, "params", {}), dict) else {}
            mode_full = params.get("mode", "agent.plan")
            mode = str(mode_full).split(".")[0] if mode_full else "agent"
            workspace_dir = params.get("workspace_dir")

            agent = await self.get_agent(
                channel_id=channel_id,
                mode=mode,
                project_dir=workspace_dir,
            )
            if agent is None:
                raise RuntimeError(f"[AgentManager] No agent available for channel {channel_id}")

            return await agent.process_message(request)
        except Exception as e:
            logger.error(f"[AgentManager] Error in process_message: {e}", exc_info=True)
            raise

    async def process_message_stream(self, request: Any):
        """处理流式请求.

        Args:
            request: AgentRequest 对象

        Yields:
            AgentResponseChunk 对象
        """
        try:
            channel_id = getattr(request, "channel_id", "")
            params = getattr(request, "params", {}) if isinstance(getattr(request, "params", {}), dict) else {}
            mode_full = params.get("mode", "agent.plan")
            mode = str(mode_full).split(".")[0] if mode_full else "agent"
            workspace_dir = params.get("workspace_dir")

            agent = await self.get_agent(
                channel_id=channel_id,
                mode=mode,
                project_dir=workspace_dir,
            )
            if agent is None:
                raise RuntimeError(f"[AgentManager] No agent available for channel {channel_id}")

            # 流式处理
            async for chunk in agent.process_message_stream(request):
                yield chunk
        except Exception as e:
            logger.error(f"[AgentManager] Error in process_message_stream: {e}", exc_info=True)
            raise

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
        self._agent_create_params.clear()
        self._client_capabilities_by_channel.clear()
        logger.info("[AgentManager] All agents cleaned up")
