# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""AgentManager - 管理单个租户内的 Agent 实例."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, TYPE_CHECKING

from jiuwenclaw.e2a.acp.protocol import build_acp_initialize_result
from jiuwenclaw.agentserver.reload_result import (
    ReloadAggregateResult,
    ReloadResult,
    log_agent_config_hot_reload_replay,
    reload_touches_memory,
)
from jiuwenclaw.agentserver.memory import (
    acquire_memory_cache_session,
    release_memory_cache_session,
)
from jiuwenclaw.local_env_config import (
    ENV_CONFIG_DICT,
    apply_env_overrides_to_active,
    apply_env_removals,
    bind_task_env_overlay,
    build_effective_env_overlay,
    promote_staged_env,
    replace_active_env,
    reset_task_env_overlay,
    stage_env_overrides,
)
from jiuwenclaw.agentserver.tools.multimodal_config import (
    infer_multimodal_env_removals,
    merge_reload_env_snapshot,
    sync_multimodal_env_omission_state,
)
from jiuwenclaw.utils import get_agent_workspace_dir, get_multi_tenant_user_workspace_dir
from jiuwenclaw.config import _sandbox_yaml_to_env_overlay

if TYPE_CHECKING:
    from jiuwenclaw.agentserver.interface import JiuWenClaw

logger = logging.getLogger(__name__)


ACP_DEFAULT_CAPABILITIES: dict[str, Any] = build_acp_initialize_result()

# Disk control-plane RPCs: list/rollback archives via EvolutionStore only.
# Must not call create_instance() (which constructs llm_OpenAI).
_DISK_ONLY_EVOLUTION_METHODS: frozenset[str] = frozenset(
    {
        "skills.evolution.archives",
        "skills.evolution.rollback",
    }
)


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

    def __init__(
            self,
            agent_id: str,
            service_id: str,
            user_workspace_dir: Path | None = None,
            config_base: dict[str, Any] | None = None,
            env_overrides: dict[str, Any] | None = None,
            last_reload_trace_id: str | None = None,
            *,
            env_agent_id: str | None = None,
            env_service_id: str | None = None,
    ) -> None:
        """初始化 AgentManager.

        Args:
            agent_id: agent名称/路径（可能被 AGENT_RUNTIME 改写成 cache_key）
            service_id: 服务ID（chat_id + bot_app_id 组合）
            user_workspace_dir: 用户工作目录路径
            config_base: 初始配置（用于懒加载 agent 时重放）
            env_overrides: 初始环境变量覆盖（用于懒加载 agent 时重放）
            env_agent_id / env_service_id: 请求侧 id，用于 tip/ns 袋键（不得用改写后 agent_id）
        """
        # 结构: dict[channel_id][mode][session_id] -> JiuWenClaw
        # 每个 session_id 对应独立的 Agent 实例，支持多 session 并发执行
        self.agents: dict[str, dict[str, dict[str, "JiuWenClaw"]]] = {}
        self._client_capabilities_by_channel: dict[str, dict[str, Any]] = {}

        # 保存初始配置（用于后续创建的 agent 重放）
        self._latest_config_base: dict[str, Any] | None = config_base
        self._latest_env_overrides: dict[str, Any] = {}

        # Request-side ids for env tip / ns (never follow AGENT_RUNTIME rewrite).
        self.env_agent_id = env_agent_id if env_agent_id is not None else agent_id
        self.env_service_id = (
            env_service_id if env_service_id is not None else service_id
        )

        # 应用初始 env_overrides（冷启动，尚无在途 session）
        if env_overrides is not None and isinstance(env_overrides, dict):
            omission_removals = infer_multimodal_env_removals(
                None,
                env_overrides,
                active_env=None,
                service_id=self.env_service_id,
                agent_id=self.env_agent_id,
            )
            if omission_removals:
                apply_env_removals(
                    omission_removals,
                    service_id=self.env_service_id,
                    agent_id=self.env_agent_id,
                )
            sync_multimodal_env_omission_state(
                omission_removals,
                env_overrides,
                service_id=self.env_service_id,
                agent_id=self.env_agent_id,
            )
            self._latest_env_overrides = merge_reload_env_snapshot(None, env_overrides)
            apply_env_overrides_to_active(
                env_overrides,
                service_id=self.env_service_id,
                agent_id=self.env_agent_id,
            )

        # 把 config_base['sandbox'] 的 url/type/enabled 翻译成 active env,
        # 让懒加载路径首次 _create_sys_operation 也能读到 yaml 配置的值
        # (reload_agent_config 内的 task overlay 仅对在途 session 生效)。
        if isinstance(config_base, dict):
            sandbox_overlay = _sandbox_yaml_to_env_overlay(config_base.get("sandbox"))
            if sandbox_overlay:
                apply_env_overrides_to_active(
                    sandbox_overlay,
                    service_id=self.env_service_id,
                    agent_id=self.env_agent_id,
                )

        self.agent_id = agent_id
        self.service_id = service_id
        self.user_workspace_dir = user_workspace_dir
        self._last_reload_trace_id = last_reload_trace_id
        logger.info(
            "[AgentManager] 初始化: agent_id=%s, service_id=%s, env_ns=(%s,%s), workspace=%s, has_config=%s",
            agent_id,
            service_id,
            self.env_service_id,
            self.env_agent_id,
            user_workspace_dir,
            config_base is not None,
        )

    @property
    def env_ns_service_id(self) -> str:
        return self.env_service_id

    @property
    def env_ns_agent_id(self) -> str:
        return self.env_agent_id

    def _build_session_key(self, channel_id: str, mode: str, session_id: str) -> str:
        """Build registry session key including tenant ids (request-side env_*).

        ``_SESSION_BINDINGS`` is process-global; without ``(service_id, agent_id)``
        two tenants sharing channel/mode/session_id (e.g. ``default``) would
        steal each other's memory cache refcounts.
        """
        return (
            f"{self.env_service_id}:{self.env_agent_id}:"
            f"{channel_id}:{mode}:{session_id}"
        )

    def _memory_workspace_dir(self) -> str:
        if self.user_workspace_dir:
            return str(Path(self.user_workspace_dir) / "agent" / "jiuwenclaw_workspace")
        workspace = get_multi_tenant_user_workspace_dir(
            self.env_service_id, self.env_agent_id
        )
        if workspace is not None:
            return str(workspace / "agent" / "jiuwenclaw_workspace")
        return str(get_agent_workspace_dir())

    async def _sync_agent_memory_cache(self, session_key: str, agent: "JiuWenClaw") -> None:
        fingerprint = await agent.get_memory_cache_fingerprint()
        if not fingerprint:
            return
        await acquire_memory_cache_session(
            session_key,
            self.env_agent_id,
            self._memory_workspace_dir(),
            fingerprint,
        )

    async def _create_agent(
        self,
        agent_key: str,
        mode: str = "agent",
        session_id: str = "default",
        config: dict[str, Any] | None = None
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

        logger.info("[AgentManager] Creating %s agent (mode=%s, session=%s)", agent_key, mode, session_id,
                   extra={'user_visible': 'progress'})

        agent = JiuWenClaw(
            user_workspace_dir=str(self.user_workspace_dir) if self.user_workspace_dir else None,
            agent_id=self.agent_id,
            service_id=self.service_id,
            env_agent_id=self.env_agent_id,
            env_service_id=self.env_service_id,
        )
        merged_config = dict(config) if isinstance(config, dict) else {}
        merged_config.setdefault(
            "agent_name",
            f"agent_{self.agent_id}_{self.service_id}_{agent_key}_{session_id}",
        )
        # Always bind (incl. {}): seal; tip = formula B ∪ latest overrides.
        overlay = build_effective_env_overlay(
            self._latest_env_overrides or None,
            service_id=self.env_service_id,
            agent_id=self.env_agent_id,
        )
        overlay_token = bind_task_env_overlay(overlay)
        try:
            await agent.create_instance(merged_config, mode=mode, session_id=session_id)
            self.agents.setdefault(agent_key, {}).setdefault(mode, {})[session_id] = agent

            # 创建后如果有保存的配置，重放 reload_agent_config
            if self._latest_config_base is not None or self._latest_env_overrides:
                try:
                    await agent.reload_agent_config(
                        config_base=self._latest_config_base,
                        env_overrides=self._latest_env_overrides,
                    )
                    log_agent_config_hot_reload_replay(
                        logger,
                        reload_trace_id=self._last_reload_trace_id,
                        session=session_id,
                        agent_key=agent_key,
                        mode=mode,
                        config=self._latest_config_base,
                        env=self._latest_env_overrides,
                    )
                except Exception as e:
                    logger.warning("[AgentManager] Replay reload_agent_config failed: %s", e)
        finally:
            reset_task_env_overlay(overlay_token)

        session_key = self._build_session_key(agent_key, mode, session_id)
        try:
            await self._sync_agent_memory_cache(session_key, agent)
        except Exception as e:
            logger.warning("[AgentManager] sync memory cache binding failed: %s", e)

        logger.info("[AgentManager] %s agent created for tenant %s (session=%s)", agent_key, self.agent_id, session_id,
                   extra={'user_visible': 'progress'})
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
            logger.info("[AgentManager] ACP initialize for tenant %s", self.agent_id,
                       extra={'user_visible': 'critical'})
            if extra_config:
                client_capabilities = extra_config.get("client_capabilities")
                if isinstance(client_capabilities, dict):
                    self._client_capabilities_by_channel["acp"] = dict(client_capabilities)

            if "acp" in self.agents:
                logger.info("[AgentManager] Resetting ACP agent for tenant %s", self.agent_id,
                           extra={'user_visible': 'progress'})
                for mode_agents in self.agents.get("acp", {}).values():
                    for agent in mode_agents.values():
                        if hasattr(agent, "cleanup"):
                            try:
                                await agent.cleanup()
                            except Exception as e:
                                logger.warning("[AgentManager] ACP agent cleanup failed: %s", e,
                                               extra={'user_visible': 'progress'})
                del self.agents["acp"]

            config = _build_acp_agent_config(extra_config)
            await self._create_agent("acp", "code", "default", config)

            return ACP_DEFAULT_CAPABILITIES.copy()
        return None

    async def cancel_all_inflight_work(self, reason: str = "[gateway ws disconnect] ") -> None:
        """Gateway 与 AgentServer 的 WebSocket 断开时：取消所有已创建 Agent 实例上的在途任务。"""
        for channel_agents in list(self.agents.values()):
            for mode_agents in list(channel_agents.values()):
                for agent in list(mode_agents.values()):
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
                extra={'user_visible': 'progress'},
            )
            return explicit_session_id
        if channel_id == "acp":
            session_id = f"acp_{uuid.uuid4().hex[:8]}"
            logger.info("[AgentManager] ACP session created: session_id=%s", session_id,
                       extra={'user_visible': 'critical'})
            return session_id
        return "default"

    async def get_agent(
            self,
            channel_id: str = "",
            mode: str = "agent",
            workspace_dir: str = None,
            session_id: str | None = None,
            request: Any | None = None,
    ) -> "JiuWenClaw | None":
        """获取 Agent 实例（自动创建）.

        每个 session_id 对应独立的 Agent 实例，支持多 session 并发执行。

        Args:
            channel_id: 通道 ID
            mode: 每个模式对应的实例
            workspace_dir: project dir
            session_id: 会话 ID，用于实例隔离

        Returns:
            JiuWenClaw | None: Agent 实例
        """
        effective_session_id = session_id or "default"

        if channel_id in self.agents and mode in self.agents[channel_id]:
            if effective_session_id in self.agents[channel_id][mode]:
                logger.info(
                    f"[AgentManager] 复用现有Agent: channel={channel_id} mode={mode} session={effective_session_id}",
                    extra={'user_visible': 'critical'}
                )
                return self.agents[channel_id][mode][effective_session_id]

        config = {"workspace_dir": workspace_dir} if workspace_dir else {}
        if channel_id == "acp":
            config = {
                **config,
                **_build_acp_agent_config()
            }
        if request is not None:
            config = {**config, "request": request}
        logger.info(
            f"[AgentManager] 创建新的Agent: channel={channel_id} mode={mode} session={effective_session_id}",
            extra={'user_visible': 'critical'}
        )
        await self._create_agent(channel_id, mode, effective_session_id, config)
        return self.agents.get(channel_id, {}).get(mode, {}).get(effective_session_id)

    def get_agent_nowait(
            self,
            channel_id: str = "",
            mode: str = "agent",
            session_id: str | None = None,
    ) -> "JiuWenClaw | None":
        """获取 Agent 实例（同步，不自动创建）.

        Args:
            channel_id: 通道 ID
            mode: 工作模式
            session_id: 会话 ID

        Returns:
            JiuWenClaw | None: Agent 实例，如果不存在则返回 None
        """
        channel_key = channel_id or "default"
        effective_session_id = session_id or "default"
        channel_agents = self.agents.get(channel_key, {})
        if isinstance(channel_agents, dict):
            mode_agents = channel_agents.get(mode, {})
            if effective_session_id in mode_agents:
                return mode_agents[effective_session_id]
        return None

    def iter_jiuwenclaw_instances(self) -> list["JiuWenClaw"]:
        """Return all initialized JiuWenClaw instances in this manager."""
        claws: list["JiuWenClaw"] = []
        for channel_agents in self.agents.values():
            if not isinstance(channel_agents, dict):
                continue
            for mode_agents in channel_agents.values():
                if not isinstance(mode_agents, dict):
                    continue
                for claw in mode_agents.values():
                    if claw is not None:
                        claws.append(claw)
        return claws

    async def reload_agents_config(
        self, config, env, *, reload_trace_id: str | None = None
    ) -> ReloadAggregateResult:
        """Reload config for all cached agents in this tenant.

        Env overrides must already be staged by the caller (``TenantAgentPool``).
        """
        if reload_trace_id:
            self._last_reload_trace_id = reload_trace_id
        previous_config = self._latest_config_base
        self._latest_env_overrides = merge_reload_env_snapshot(
            self._latest_env_overrides,
            env,
        )
        self._latest_config_base = config

        # 把 config['sandbox'] 的 url/type/enabled 翻译成 env overlay 并 stage,
        # 让 promote_staged_env 把它们写入 active env。这样后续懒加载的 agent
        # (含首个 _create_sys_operation 调用) 能读到 yaml 配置的 sandbox 值。
        # (TenantAgentPool.stage_env_overrides(env) 只处理 env payload, 不含 yaml。)
        if isinstance(config, dict):
            sandbox_overlay = _sandbox_yaml_to_env_overlay(config.get("sandbox"))
            if sandbox_overlay:
                stage_env_overrides(
                    sandbox_overlay,
                    service_id=self.env_service_id,
                    agent_id=self.env_agent_id,
                )

        aggregate = ReloadAggregateResult()
        for channel_id, channel_agents in self.agents.items():
            if not isinstance(channel_agents, dict):
                logger.warning(
                    "[AgentManager] unexpected agents entry for channel %s: %r",
                    channel_id,
                    type(channel_agents),
                )
                continue
            for mode, mode_agents in channel_agents.items():
                for session_id, agent in mode_agents.items():
                    session_key = self._build_session_key(channel_id, mode, session_id)
                    try:
                        result = await agent.reload_agent_config(
                            config_base=config,
                            env_overrides=env,
                        )
                        aggregate.merge(result, session_key=session_key)
                        if result.applied or result.deferred:
                            try:
                                await self._sync_agent_memory_cache(session_key, agent)
                            except Exception as exc:
                                logger.warning(
                                    "[AgentManager] sync memory cache after reload failed for %s: %s",
                                    session_key,
                                    exc,
                                )
                    except Exception as exc:
                        logger.exception(
                            "[AgentManager] reload_agent_config failed for %s", session_key
                        )
                        aggregate.failed.append(
                            {"session": session_key, "error": str(exc)}
                        )
            logger.info("channel %s reload agent config finished.", channel_id)

        idle = not self.is_working()
        if idle:
            promote_staged_env(
                service_id=self.env_service_id,
                agent_id=self.env_agent_id,
            )
        try:
            from jiuwenclaw.agentserver.tools.browser_tools import (
                notify_browser_runtime_after_reload,
            )

            notify_browser_runtime_after_reload(
                idle=idle,
                service_id=self.env_service_id,
                agent_id=self.env_agent_id,
            )
        except Exception:
            logger.exception(
                "[AgentManager] browser runtime reload notify failed"
            )

        return aggregate

    async def apply_sync_config(
        self,
        config: dict[str, Any],
        env: dict[str, Any],
    ) -> ReloadAggregateResult:
        """Apply sync_agents_configs write-through config/env to live adapters."""
        previous_config = self._latest_config_base
        self._latest_config_base = config
        self._latest_env_overrides = dict(env)
        replace_active_env(
            env,
            service_id=self.env_service_id,
            agent_id=self.env_agent_id,
            clear_staged=True,
        )
        invalidate_memory = reload_touches_memory(
            env,
            config,
            previous_config=previous_config,
        )

        aggregate = ReloadAggregateResult()
        # Walk channel/mode/session so memory bindings use tenant-qualified keys.
        for channel_id, channel_agents in self.agents.items():
            if not isinstance(channel_agents, dict):
                continue
            for mode, mode_agents in channel_agents.items():
                if not isinstance(mode_agents, dict):
                    continue
                for session_id, claw in mode_agents.items():
                    adapter = getattr(claw, "_adapter", None)
                    if adapter is None:
                        continue
                    session_key = self._build_session_key(channel_id, mode, session_id)
                    try:
                        if claw.is_working():
                            # Busy: rewrite pending to sync-side materialization (plan: never
                            # clear-only; never keep old Gateway snapshot).
                            pending = (config, env, invalidate_memory)
                            if hasattr(adapter, "_pending_reload"):
                                setattr(adapter, "_pending_reload", pending)
                                aggregate.deferred += 1
                            else:
                                result = await claw.reload_agent_config(config, env)
                                aggregate.merge(result, session_key=session_key)
                        else:
                            reload_fn = getattr(adapter, "reload_agent_config", None)
                            if callable(reload_fn):
                                result = await reload_fn(
                                    config,
                                    env,
                                    _force_apply=True,
                                    _invalidate_memory_cache=invalidate_memory,
                                )
                                aggregate.merge(result, session_key=session_key)
                                if result.applied or result.deferred:
                                    try:
                                        await self._sync_agent_memory_cache(
                                            session_key, claw
                                        )
                                    except Exception as exc:
                                        logger.warning(
                                            "[AgentManager] sync memory cache after sync apply failed: %s",
                                            exc,
                                        )
                    except Exception as exc:
                        logger.exception(
                            "[AgentManager] apply_sync_config failed for adapter"
                        )
                        aggregate.failed.append(
                            {"session": session_key, "error": str(exc)}
                        )

        try:
            from jiuwenclaw.agentserver.tools.browser_tools import (
                notify_browser_runtime_after_reload,
            )

            notify_browser_runtime_after_reload(
                idle=not self.is_working(),
                service_id=self.env_service_id,
                agent_id=self.env_agent_id,
            )
        except Exception:
            logger.exception(
                "[AgentManager] browser runtime sync-config notify failed"
            )
        return aggregate

    async def _process_disk_only_evolution(self, request: Any) -> Any:
        """Handle archives/rollback without create_instance / LLM client.

        Prefer a warm agent if one already exists; otherwise use an ephemeral
        JiuWenClaw that only constructs DeepAdapter + EvolutionStore.
        """
        channel_id = getattr(request, "channel_id", "") or "default"
        session_id = getattr(request, "session_id", None)
        params = getattr(request, "params", {}) if isinstance(getattr(request, "params", {}), dict) else {}
        mode_full = params.get("mode", "agent.plan")
        mode = str(mode_full).split(".")[0] if mode_full else "agent"

        existing = self.get_agent_nowait(channel_id, mode, session_id)
        if existing is not None:
            return await existing.process_message(request)

        from jiuwenclaw.agentserver.interface import JiuWenClaw

        agent = JiuWenClaw(
            user_workspace_dir=str(self.user_workspace_dir) if self.user_workspace_dir else None,
            agent_id=self.agent_id,
            service_id=self.service_id,
        )
        overlay_token = None
        if self._latest_env_overrides:
            overlay = build_effective_env_overlay(self._latest_env_overrides)
            if overlay:
                overlay_token = bind_task_env_overlay(overlay)
        try:
            logger.info(
                "[AgentManager] disk-only evolution RPC via ephemeral agent "
                "(skip create_instance): method=%s channel=%s",
                getattr(getattr(request, "req_method", None), "value", getattr(request, "req_method", None)),
                channel_id,
                extra={"user_visible": "progress"},
            )
            return await agent.process_message(request)
        finally:
            if overlay_token is not None:
                reset_task_env_overlay(overlay_token)

    async def process_message(self, request: Any) -> Any:
        """处理非流式请求.

        Args:
            request: AgentRequest 对象

        Returns:
            AgentResponse 对象
        """
        logger.info(
            "[AgentManager] DIAGNOSTIC: process_message 非流式消息处理开始执行 "
            "| request_id=%s | channel=%s | has_metadata=%s",
            getattr(request, "request_id", ""),
            getattr(request, "channel_id", ""),
            bool(getattr(request, "metadata", None)),
            extra={'user_visible': 'critical'}
        )

        req_method = getattr(request, "req_method", None)
        req_method_value = getattr(req_method, "value", req_method)
        if isinstance(req_method_value, str) and req_method_value in _DISK_ONLY_EVOLUTION_METHODS:
            return await self._process_disk_only_evolution(request)

        channel_id = getattr(request, "channel_id", "")
        session_id = getattr(request, "session_id", None)
        params = getattr(request, "params", {}) if isinstance(getattr(request, "params", {}), dict) else {}
        mode_full = params.get("mode", "agent.plan")
        mode = str(mode_full).split(".")[0] if mode_full else "agent"
        workspace_dir = params.get("workspace_dir")

        agent = await self.get_agent(
            channel_id=channel_id,
            mode=mode,
            workspace_dir=workspace_dir,
            session_id=session_id,
            request=request,
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
            logger.info(
                f"[AgentManager] Code模式switch开始: session="
                f"{session.session_id if hasattr(session, 'session_id') else 'unknown'}",
                extra={'user_visible': 'critical'}
            )
            await session.pre_run(inputs=None)  # 从 checkpointer 加载历史 state
            agent.get_instance().switch_mode(session=session, mode=sub_mode)
            state = agent.get_instance().load_state(session)
            session.update_state({"deep_agent_state": state.to_session_dict()})
            logger.info(
                f"[AgentManager] Code模式switch完成: session="
                f"{session.session_id if hasattr(session, 'session_id') else 'unknown'}",
                extra={'user_visible': 'critical'}
            )
            await session.post_run()  # 写入 checkpointer

        return await agent.process_message(request)

    async def process_message_stream(self, request: Any):
        """处理流式请求.

        Args:
            request: AgentRequest 对象

        Yields:
            AgentResponseChunk 对象
        """
        # 诊断日志：确认 AgentManager.process_message_stream 是否被调用
        logger.info(
            "[AgentManager] DIAGNOSTIC: process_message_stream 流式消息处理开始执行 "
            "| request_id=%s | channel=%s | has_metadata=%s",
            getattr(request, "request_id", ""),
            getattr(request, "channel_id", ""),
            bool(getattr(request, "metadata", None)),
            extra={'user_visible': 'critical'}
        )
        channel_id = getattr(request, "channel_id", "")
        session_id = getattr(request, "session_id", None)
        params = getattr(request, "params", {}) if isinstance(getattr(request, "params", {}), dict) else {}
        mode_full = params.get("mode", "agent.plan")
        mode = str(mode_full).split(".")[0] if mode_full else "agent"
        workspace_dir = params.get("workspace_dir")

        agent = await self.get_agent(
            channel_id=channel_id,
            mode=mode,
            workspace_dir=workspace_dir,
            session_id=session_id,
            request=request,
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
            logger.info(
                f"[AgentManager] Code模式switch开始: session="
                f"{session.session_id if hasattr(session, 'session_id') else 'unknown'}",
                extra={'user_visible': 'critical'}
            )
            await session.pre_run(inputs=None)  # 从 checkpointer 加载历史 state
            agent.get_instance().switch_mode(session=session, mode=sub_mode)
            state = agent.get_instance().load_state(session)
            session.update_state({"deep_agent_state": state.to_session_dict()})
            logger.info(
                f"[AgentManager] Code模式switch完成: session="
                f"{session.session_id if hasattr(session, 'session_id') else 'unknown'}",
                extra={'user_visible': 'critical'}
            )
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

    async def cleanup_session(
        self,
        channel_id: str,
        mode: str,
        session_id: str
    ) -> None:
        """清理指定 session 的 Agent 实例.

        Args:
            channel_id: 通道 ID
            mode: 工作模式
            session_id: 会话 ID
        """
        try:
            session_agents = self.agents.get(channel_id, {}).get(mode, {})
            agent = session_agents.pop(session_id, None)

            session_key = self._build_session_key(channel_id, mode, session_id)
            try:
                await release_memory_cache_session(session_key)
            except Exception as exc:
                logger.warning("[AgentManager] release memory cache failed for %s: %s", session_key, exc)

            if agent:
                if hasattr(agent, "cleanup"):
                    await agent.cleanup()
                logger.info(
                    "[AgentManager] Session cleaned up: channel=%s mode=%s session=%s",
                    channel_id, mode, session_id
                )
        except Exception as e:
            logger.warning("[AgentManager] Session cleanup failed: %s", e)

    async def cleanup(self) -> None:
        """清理所有 agent 实例."""
        for channel_id, channel_agents in list(self.agents.items()):
            if not isinstance(channel_agents, dict):
                continue
            for mode, mode_agents in channel_agents.items():
                if not isinstance(mode_agents, dict):
                    continue
                for session_id in list(mode_agents.keys()):
                    session_key = self._build_session_key(channel_id, mode, session_id)
                    try:
                        await release_memory_cache_session(session_key)
                    except Exception as exc:
                        logger.warning(
                            "[AgentManager] release memory cache failed for %s: %s",
                            session_key,
                            exc,
                        )
                for agent in mode_agents.values():
                    if hasattr(agent, "cleanup"):
                        try:
                            await agent.cleanup()
                        except Exception as e:
                            logger.warning("[AgentManager] Agent cleanup failed: %s", e)
            del self.agents[channel_id]
        self._client_capabilities_by_channel.clear()
        logger.info("[AgentManager] All agents cleaned up for tenant %s", self.agent_id)

    def is_working(self) -> bool:
        """返回租户是否正在工作.

        任意一个 Agent 在工作则返回 True，立即返回。
        """
        if not self.agents:
            return False

        for channel_agents in self.agents.values():
            if not isinstance(channel_agents, dict):
                continue
            for mode_agents in channel_agents.values():
                for agent in mode_agents.values():
                    if hasattr(agent, "is_working"):
                        try:
                            if agent.is_working():
                                return True
                        except Exception as e:
                            logger.warning("Get working status failed, %s", e)
                            continue
        return False
