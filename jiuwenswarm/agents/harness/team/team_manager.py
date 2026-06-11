# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team lifecycle manager."""

from __future__ import annotations

import asyncio
import copy
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.paths import team_home
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.core.runner import Runner
from openjiuwen.harness import DeepAgent
from openjiuwen.harness.rails import (
    SkillEvolutionRail,
    TeamSkillCreateRail,
    TeamSkillEvolutionRail,
)
from jiuwenswarm.agents.harness.team.bootstrap import configure_agent_teams_home

configure_agent_teams_home()

from jiuwenswarm.agents.harness.team.config_loader import (
    load_team_spec_dict,
)
from jiuwenswarm.agents.harness.team.distributed_runtime import (
    ensure_postgresql_for_leader,
    extract_pg_endpoint,
    fallback_distributed_to_local,
    is_distributed_mode,
    missing_distributed_dependencies,
    is_pg_available,
    is_postgresql_storage,
    normalize_distributed_transport_fields,
    parse_port,
    run_command,
    runtime_member_name,
    runtime_role,
    try_start_pg_cluster,
)
from jiuwenswarm.agents.harness.team.handlers.team_monitor_handler import TeamMonitorHandler
from jiuwenswarm.agents.harness.team.remote_member_bootstrap import release_a2x_reservations_for_session
from jiuwenswarm.agents.harness.team.team_skill_links import sync_skill_dir_links
from jiuwenswarm.common.config import get_config, get_default_models
from jiuwenswarm.agents.harness.team.team_runtime_inheritance import (
    MemberInfo,
    RuntimeInfo,
    TeamWorkspaceInfo,
    get_evolution_auto_scan_enabled,
    get_skill_create_enabled,
    build_member_rails,
)
from jiuwenswarm.common.utils import get_agent_skills_dir
from jiuwenswarm.server.runtime.session.session_metadata import get_session_metadata

logger = logging.getLogger(__name__)

# Wall-clock cap for a single external command (pg_isready, systemctl, etc.).
_SUBPROCESS_TIMEOUT_SEC = 120.0
# After pg_ctlcluster/systemd reports start, the server may still be initializing.
_PG_POST_START_READY_MAX_SEC = 30.0
_PG_POST_START_READY_INIT_SLEEP = 0.4
_PG_POST_START_READY_MAX_SLEEP = 2.0
_PG_POST_START_READY_BACKOFF = 1.45
_PG_POST_START_LOG_EVERY_SEC = 5.0


@dataclass
class TeamRailMountContext:
    """Context needed to rebuild team rails after a hot config toggle."""

    agent: Any
    member_info: MemberInfo
    runtime: RuntimeInfo
    team_workspace: TeamWorkspaceInfo


async def _stop_team_messager(team_agent: Any, *, session_id: str) -> None:
    """Stop a team's mailbox transport so per-team ZMQ sockets release their ports."""
    infra = getattr(team_agent, "infra", None)
    messager = getattr(infra, "messager", None) if infra is not None else None
    stop = getattr(messager, "stop", None)
    if not callable(stop):
        return
    try:
        await stop()
        logger.info("[TeamManager] team messager stopped: session_id=%s", session_id)
    except Exception as exc:
        logger.warning("[TeamManager] team messager stop failed: session_id=%s error=%s", session_id, exc)


def _runner_team_runtime_manager(runner: Any) -> Any:
    """Return Runner's team runtime manager without calling its protected method."""
    attr_name = "_team_runtime_manager"
    manager = vars(runner).get(attr_name)
    if manager is None:
        from openjiuwen.agent_teams.runtime import TeamRuntimeManager

        manager = TeamRuntimeManager()
        setattr(runner, attr_name, manager)
    return manager


class TeamManager:
    """Manage team instances across sessions."""

    def __init__(self):
        self._team_agents: dict[str, TeamAgent] = {}
        self._runner_team_agents: dict[str, TeamAgent] = {}
        self._team_monitors: dict[str, TeamMonitorHandler] = {}
        self._stream_tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self._active_session_id: str | None = None
        self._active_team_name: str | None = None
        self._pending_session_id: str | None = None
        self._pending_team_name: str | None = None
        # session_id → TeamSkillEvolutionRail instance (set by customizer, used for drain/approval)
        self._team_skill_rails: dict[str, Any] = {}
        # session_id → member SkillEvolutionRail instances
        self._team_member_skill_evolution_rails: dict[str, list[Any]] = {}
        # session_id → TeamSkillCreateRail instance
        self._team_skill_create_rails: dict[str, Any] = {}
        # session_id → context used to rebuild team rails on config enable
        self._team_rail_contexts: dict[str, TeamRailMountContext] = {}
        # session_id → live rails and owning DeepAgent, for hot-unregister
        self._team_live_rails: dict[str, list[tuple[Any, Any]]] = {}
        # session_id → evolution watcher task
        self._team_evolution_watchers: dict[str, asyncio.Task] = {}
        # session_id -> team workspace skills directory used as the shared link view.
        self._team_shared_skill_link_targets: dict[str, Path] = {}
        # session_id → workflow handler instance
        self._workflow_handlers: dict[str, Any] = {}

    def has_stream_task(self, session_id: str) -> bool:
        return session_id in self._stream_tasks

    def pop_stream_task(self, session_id: str) -> asyncio.Task | None:
        return self._stream_tasks.pop(session_id, None)

    @property
    def active_session_id(self) -> str | None:
        return self._active_session_id

    @property
    def active_team_name(self) -> str | None:
        return self._active_team_name

    @property
    def pending_session_id(self) -> str | None:
        return self._pending_session_id

    @property
    def pending_team_name(self) -> str | None:
        return self._pending_team_name

    def get_monitor(self, session_id: str) -> TeamMonitorHandler | None:
        return self._team_monitors.get(session_id)

    def get_team_evolution_watcher(self, session_id: str) -> asyncio.Task | None:
        return self._team_evolution_watchers.get(session_id)

    def register_team_evolution_watcher(self, session_id: str, task: asyncio.Task) -> None:
        self._team_evolution_watchers[session_id] = task

    def pop_team_evolution_watcher(self, session_id: str) -> asyncio.Task | None:
        return self._team_evolution_watchers.pop(session_id, None)

    @staticmethod
    def _is_distributed_mode(config_base: dict[str, Any]) -> bool:
        return is_distributed_mode(config_base)

    @staticmethod
    def _runtime_role(config_base: dict[str, Any]) -> str:
        return runtime_role(config_base)

    @staticmethod
    def _runtime_member_name(config_base: dict[str, Any], team_cfg: dict[str, Any]) -> str | None:
        return runtime_member_name(config_base, team_cfg)

    @staticmethod
    def _normalize_distributed_transport_fields(
        config_base: dict[str, Any],
        team_cfg: dict[str, Any],
    ) -> dict[str, Any]:
        return normalize_distributed_transport_fields(config_base, team_cfg)

    @staticmethod
    def normalize_distributed_transport_fields(
        config_base: dict[str, Any],
        team_cfg: dict[str, Any],
    ) -> dict[str, Any]:
        """Public wrapper for distributed transport normalization."""
        return TeamManager._normalize_distributed_transport_fields(config_base, team_cfg)

    @staticmethod
    def _parse_port(value: Any, default: int, field_name: str) -> int:
        return parse_port(value, default, field_name)

    @staticmethod
    def parse_port(value: Any, default: int, field_name: str) -> int:
        """Public wrapper for validated port parsing."""
        return TeamManager._parse_port(value, default, field_name)

    @staticmethod
    def _normalize_team_identity_fields(team_cfg: dict[str, Any]) -> dict[str, Any]:
        normalized_cfg = copy.deepcopy(team_cfg)
        leader_cfg = normalized_cfg.get("leader", {})
        if isinstance(leader_cfg, dict):
            display_name = str(leader_cfg.get("display_name", "")).strip()
            name = str(leader_cfg.get("name", "")).strip()
            if display_name and not name:
                leader_cfg["name"] = display_name
            elif name and not display_name:
                leader_cfg["display_name"] = name

        members = normalized_cfg.get("predefined_members", [])
        if isinstance(members, list):
            for member in members:
                if not isinstance(member, dict):
                    continue
                display_name = str(member.get("display_name", "")).strip()
                name = str(member.get("name", "")).strip()
                if display_name and not name:
                    member["name"] = display_name
                elif name and not display_name:
                    member["display_name"] = name
        return normalized_cfg

    @staticmethod
    def _build_session_scoped_team_name(team_name: str, session_id: str) -> str:
        base_name = str(team_name or "").strip() or "team"
        session_suffix = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(session_id or "").strip())
        session_suffix = session_suffix.strip("._-")
        if not session_suffix:
            return base_name
        if base_name.endswith(f"_{session_suffix}"):
            return base_name
        return f"{base_name}_{session_suffix}"

    @staticmethod
    def _apply_session_scoped_team_name(
        spec: TeamAgentSpec,
        *,
        session_id: str,
    ) -> None:
        spec.team_name = TeamManager._build_session_scoped_team_name(
            spec.team_name,
            session_id,
        )

    @staticmethod
    def _load_team_spec(session_id: str) -> TeamAgentSpec:
        config_base = get_config()
        # Keep dependency checks scoped to distributed mode to make the
        # control flow explicit at the call site (local mode bypasses checks).
        if TeamManager._is_distributed_mode(config_base):
            missing = missing_distributed_dependencies(config_base)
            if missing:
                missing_list = ", ".join(missing)
                logger.warning(
                    "[TeamManager][MISSING_DISTRIBUTE_DEPS] missing=%s",
                    missing_list,
                )
                logger.error(
                    "[TeamManager][FALLBACK_TO_LOCAL] "
                    "distributed runtime is not available; downgraded to local mode "
                    "for current process"
                )
                logger.warning(
                    "[TeamManager][ACTION] install via: "
                    "pip install -e \".[distribute]\" or uv sync --extra distribute"
                )
                config_base = fallback_distributed_to_local(config_base)

        spec_dict = load_team_spec_dict(config_base=config_base)
        spec_dict = TeamManager._normalize_team_identity_fields(spec_dict)
        if TeamManager._is_distributed_mode(config_base):
            spec_dict = TeamManager._normalize_distributed_transport_fields(config_base, spec_dict)

        # When models.defaults has more than one entry, populate model_pool
        # and set model_pool_strategy to by_model_name so team members
        # can be assigned different model endpoints from the pool.
        default_models = get_default_models(config_base)
        if len(default_models) > 1:
            from openjiuwen.agent_teams.schema.team import ModelPoolEntry

            pool_entries: list[dict] = []
            for entry in default_models:
                mcc = entry.get("model_client_config") or {}
                mco = entry.get("model_config_obj") or {}
                if not mcc.get("model_name"):
                    continue
                pool_entry = ModelPoolEntry(
                    model_name=mcc["model_name"],
                    api_key=mcc.get("api_key", ""),
                    api_base_url=mcc.get("api_base", ""),
                    api_provider=mcc.get("client_provider", ""),
                    metadata={
                        "client": {
                            k: v for k, v in mcc.items()
                            if k not in ("model_name", "api_key", "api_base", "client_provider") and v is not None
                        },
                        "request": dict(mco),
                    },
                )
                pool_entries.append(pool_entry.model_dump())

            if pool_entries:
                spec_dict["model_pool"] = pool_entries
                spec_dict["model_pool_strategy"] = "by_model_name"

        return TeamAgentSpec.model_validate(spec_dict)


    async def get_swarm_enriched_team_spec(
        self,
        session_id: str,
        *,
        mode: str,
        project_dir: str | None = None,
        request_id: str | None = None,
        channel_id: str | None = None,
        request_metadata: dict[str, Any] | None = None,
    ) -> TeamAgentSpec:
        """Build a team spec via provider-based assembly (no parent DeepAgent).

        Sources every member capability from the shared config source through
        ``enrich_team_spec_for_swarm`` instead of inheriting from a pre-built
        single agent, so creating a team never requires constructing one first.

        Args:
            session_id: Active session id.
            mode: Request mode (e.g. "team").
            project_dir: Resolved project directory, if any.
            request_id: Originating request id, if any.
            channel_id: Raw channel id from the request, if any.
            request_metadata: Request metadata mapping.

        Returns:
            The enriched ``TeamAgentSpec`` ready to build (``build_context`` set;
            assembly is fully declarative, no imperative post-processing).
        """
        from jiuwenswarm.agents.swarm import enrich_team_spec_for_swarm

        config_base = get_config()
        await self._ensure_postgresql_for_leader(config_base)
        spec = self._load_team_spec(session_id)
        self._apply_session_scoped_team_name(spec, session_id=session_id)
        self.apply_team_plan_mode(spec, request_metadata=request_metadata)
        enrich_team_spec_for_swarm(
            spec,
            session_id=session_id,
            mode=mode,
            project_dir=project_dir,
            request_id=request_id,
            channel_id=channel_id,
            request_metadata=request_metadata,
        )
        return spec

    @staticmethod
    def apply_team_plan_mode(
        spec: TeamAgentSpec,
        *,
        request_metadata: dict[str, Any] | None,
    ) -> None:
        mode = str((request_metadata or {}).get("mode") or "").strip().lower()
        if mode == "team.plan":
            try:
                spec.enable_team_plan = True
            except (AttributeError, ValueError):
                object.__setattr__(spec, "enable_team_plan", True)

    async def prepare_runtime_activation(self, session_id: str, team_name: str) -> None:
        await self.prepare_session_switch(
            session_id,
            reason="switch runtime: ",
        )

        async with self._lock:
            self._pending_session_id = session_id
            self._pending_team_name = team_name

    async def prepare_session_switch(self, target_session_id: str, reason: str = "") -> None:
        """Stop other active or pending team runtimes before switching sessions."""
        stale_sessions: list[str] = []
        async with self._lock:
            if self._active_session_id and self._active_session_id != target_session_id:
                stale_sessions.append(self._active_session_id)
            if self._pending_session_id and self._pending_session_id != target_session_id:
                stale_sessions.append(self._pending_session_id)
            logger.info(
                "[TeamManager] %sprepare_session_switch target=%s active=%s pending=%s stale=%s",
                reason,
                target_session_id,
                self._active_session_id,
                self._pending_session_id,
                list(dict.fromkeys(stale_sessions)),
            )

        for stale_session_id in dict.fromkeys(stale_sessions):
            await self.stop_session_runtime(
                stale_session_id,
                reason=reason,
            )

    def commit_runtime_ready(self, session_id: str, team_name: str) -> None:
        self._active_session_id = session_id
        self._active_team_name = team_name
        if self._pending_session_id == session_id:
            self._pending_session_id = None
            self._pending_team_name = None
        logger.info(
            "[TeamManager] commit_runtime_ready session_id=%s team_name=%s active=%s pending=%s",
            session_id,
            team_name,
            self._active_session_id,
            self._pending_session_id,
        )

    def clear_pending_runtime(self, session_id: str) -> None:
        if self._pending_session_id == session_id:
            self._pending_session_id = None
            self._pending_team_name = None

    def clear_active_runtime(self, session_id: str) -> None:
        if self._active_session_id == session_id:
            self._active_session_id = None
            self._active_team_name = None

    def _resolve_session_team_name(self, session_id: str) -> str | None:
        if self._active_session_id == session_id and self._active_team_name:
            return self._active_team_name
        if self._pending_session_id == session_id and self._pending_team_name:
            return self._pending_team_name

        metadata = get_session_metadata(session_id)
        team_name = str(metadata.get("team_name") or "").strip()
        if team_name:
            return team_name

        logger.warning(
            "[TeamManager] failed to resolve team_name from active/pending/metadata: session_id=%s",
            session_id,
        )
        return None

    @staticmethod
    def _resolve_delete_session_team_name(session_id: str) -> str | None:
        metadata = get_session_metadata(session_id)
        team_name = str(metadata.get("team_name") or "").strip()
        if team_name:
            return team_name

        logger.warning(
            "[TeamManager] failed to resolve delete team_name from metadata: session_id=%s",
            session_id,
        )
        return None


    @staticmethod
    def _is_postgresql_storage(team_cfg: dict[str, Any]) -> bool:
        return is_postgresql_storage(team_cfg)

    @staticmethod
    def _extract_pg_endpoint(team_cfg: dict[str, Any]) -> tuple[str, int]:
        return extract_pg_endpoint(team_cfg)

    @staticmethod
    async def _run_command(*args: str) -> tuple[int, str]:
        return await run_command(*args, subprocess_timeout_sec=_SUBPROCESS_TIMEOUT_SEC)

    async def _is_pg_available(self, host: str, port: int) -> bool:
        return await is_pg_available(host, port, subprocess_timeout_sec=_SUBPROCESS_TIMEOUT_SEC)

    async def _try_start_pg_cluster(self) -> bool:
        return await try_start_pg_cluster(subprocess_timeout_sec=_SUBPROCESS_TIMEOUT_SEC)

    async def _ensure_postgresql_for_leader(self, config_base: dict[str, Any]) -> None:
        await ensure_postgresql_for_leader(
            config_base,
            subprocess_timeout_sec=_SUBPROCESS_TIMEOUT_SEC,
            post_start_ready_max_sec=_PG_POST_START_READY_MAX_SEC,
            post_start_ready_init_sleep=_PG_POST_START_READY_INIT_SLEEP,
            post_start_ready_max_sleep=_PG_POST_START_READY_MAX_SLEEP,
            post_start_ready_backoff=_PG_POST_START_READY_BACKOFF,
            post_start_log_every_sec=_PG_POST_START_LOG_EVERY_SEC,
        )

    @staticmethod
    def _initialize_team_shared_skill_links(spec: TeamAgentSpec) -> None:
        """Initialize team shared skill links from the global skill root."""
        global_skills_dir = get_agent_skills_dir()
        if not global_skills_dir.exists():
            logger.warning("[TeamManager] global_skills_dir does not exist: %s", global_skills_dir)
            return

        # Resolve team workspace path
        ws_config = spec.workspace
        ws_path = ws_config.root_path if ws_config and ws_config.root_path else None
        if not ws_path:
            ws_path = str(team_home(spec.team_name) / "team-workspace")

        team_shared_skills_dir = Path(ws_path) / "skills"

        team_shared_skills_dir.mkdir(parents=True, exist_ok=True)
        sync_skill_dir_links(global_skills_dir, team_shared_skills_dir)

        logger.info("[TeamManager] Initialized team shared skill links: %s", team_shared_skills_dir)

    @staticmethod
    def _resolve_team_shared_skills_dir(spec: TeamAgentSpec) -> Path:
        ws_config = spec.workspace
        ws_path = ws_config.root_path if ws_config and ws_config.root_path else None
        if not ws_path:
            ws_path = str(team_home(spec.team_name) / "team-workspace")
        return Path(ws_path) / "skills"

    @staticmethod
    def ensure_team_shared_skills_initialized(spec: TeamAgentSpec) -> None:
        """Ensure team shared skills are available in the team workspace."""
        TeamManager._initialize_team_shared_skill_links(spec)

    def ensure_team_shared_skills_ready_for_session(self, session_id: str, spec: TeamAgentSpec) -> None:
        """Ensure team shared skills are initialized and registered for refresh."""
        self.ensure_team_shared_skills_initialized(spec)
        self.register_team_shared_skill_link_target(
            session_id,
            self._resolve_team_shared_skills_dir(spec),
        )

    def register_team_shared_skill_link_target(self, session_id: str, target: Path) -> None:
        """Register the team shared skills directory for link refresh."""
        self._team_shared_skill_link_targets[session_id] = target

    def refresh_team_shared_skill_links(self, session_id: str) -> bool:
        """Refresh team shared skill links from global skills."""
        target = self._team_shared_skill_link_targets.get(session_id)
        if target is None:
            logger.debug("[TeamManager] no team shared skill link target for session_id=%s", session_id)
            return False
        global_skills_dir = get_agent_skills_dir()
        if not global_skills_dir.exists():
            logger.warning("[TeamManager] global_skills_dir does not exist: %s", global_skills_dir)
            return False
        sync_skill_dir_links(global_skills_dir, target)
        logger.info("[TeamManager] Refreshed team shared skill links: session_id=%s target=%s", session_id, target)
        return True

    def refresh_all_team_shared_skill_links(self) -> int:
        """Refresh every registered team shared skill link view."""
        refreshed = 0
        for session_id in list(self._team_shared_skill_link_targets):
            if self.refresh_team_shared_skill_links(session_id):
                refreshed += 1
        return refreshed

    async def create_team(
        self,
        session_id: str,
        deep_agent: DeepAgent,
        request_id: str | None = None,
        channel_id: str | None = None,
        request_metadata: dict[str, Any] | None = None,
    ) -> TeamAgent:
        config_base = get_config()
        await self._ensure_postgresql_for_leader(config_base)
        logger.info("[TeamManager] building TeamAgentSpec: session_id=%s", session_id)
        spec = self._load_team_spec(session_id)
        self._apply_session_scoped_team_name(
            spec,
            session_id=session_id,
        )

        resolved_mode = str((request_metadata or {}).get("mode") or "").strip()
        # Provider-based assembly: source every member capability from the shared
        # config source, no pre-built parent DeepAgent / customizer. Mirrors
        # get_swarm_enriched_team_spec so a team rebuilt here (e.g. the distributed
        # teammate's auxiliary leader) carries provider declarations plus the
        # serializable build_context_seed.
        from jiuwenswarm.agents.swarm import enrich_team_spec_for_swarm

        self.apply_team_plan_mode(spec, request_metadata=request_metadata)
        enrich_team_spec_for_swarm(
            spec,
            session_id=session_id,
            mode=resolved_mode,
            project_dir=(request_metadata or {}).get("project_dir"),
            request_id=request_id,
            channel_id=channel_id,
            request_metadata=request_metadata,
        )

        logger.info("[TeamManager] TeamAgentSpec ready: team_name=%s", spec.team_name)

        token = set_session_id(session_id)
        try:
            logger.info("[TeamManager] creating TeamAgent from spec")
            team_agent = spec.build()
            self._team_agents[session_id] = team_agent
            # After build, initialize team shared skill links.
            self.ensure_team_shared_skills_ready_for_session(session_id, spec)

            if self._is_distributed_mode(config_base):
                try:
                    from jiuwenswarm.agents.harness.team.remote_member_bootstrap import (
                        attach_distributed_local_spawn_guard,
                        attach_remote_bootstrap_ack_listener,
                        attach_remote_teammate_bootstrap_listener,
                        attach_shutdown_member_remote_cleanup_wrapper,
                        attach_spawn_member_remote_bootstrap_wrapper,
                    )

                    attach_distributed_local_spawn_guard(
                        team_agent,
                        session_id=session_id,
                        channel_id=channel_id,
                    )
                    attach_spawn_member_remote_bootstrap_wrapper(
                        team_agent,
                        session_id=session_id,
                        channel_id=channel_id,
                    )
                    attach_shutdown_member_remote_cleanup_wrapper(
                        team_agent,
                        session_id=session_id,
                        channel_id=channel_id,
                    )
                    attach_remote_bootstrap_ack_listener(
                        team_agent,
                        session_id=session_id,
                        channel_id=channel_id,
                    )
                    attach_remote_teammate_bootstrap_listener(
                        team_agent,
                        session_id=session_id,
                        channel_id=channel_id,
                    )
                except Exception as exc:
                    logger.warning(
                        "[TeamManager] remote_member_bootstrap wrapper attach failed: %s",
                        exc,
                    )
            logger.info(
                "[TeamManager] Team created: session_id=%s, team_name=%s",
                session_id,
                spec.team_name,
            )
            return team_agent
        finally:
            reset_session_id(token)

    async def get_or_create_team(
        self,
        session_id: str,
        deep_agent: DeepAgent,
        request_id: str | None = None,
        channel_id: str | None = None,
        request_metadata: dict[str, Any] | None = None,
    ) -> TeamAgent:
        async with self._lock:
            team_agent = self._team_agents.get(session_id)
            if team_agent is not None:
                return team_agent

            await self._destroy_other_sessions(session_id)
            return await self.create_team(
                session_id,
                deep_agent,
                request_id,
                channel_id,
                request_metadata,
            )

    async def interact(self, session_id: str, user_input: Any) -> tuple[bool, str | None]:
        try:
            if session_id != self._active_session_id or not self._active_team_name:
                reason = "not_active" if not self._active_team_name else "session_mismatch"
                logger.warning(
                    "[TeamManager] interact ignored for non-active team session: "
                    "session_id=%s active_session_id=%s active_team_name=%s reason=%s",
                    session_id,
                    self._active_session_id,
                    self._active_team_name,
                    reason,
                )
                return False, reason

            team_name = self._active_team_name
            result = await Runner.interact_agent_team(
                user_input,
                team_name=team_name,
                session_id=session_id,
            )
            if not result:
                reason = getattr(result, "reason", None) or "runner_failed"
                logger.warning(
                    "[TeamManager] interact failed against runner runtime: session_id=%s team=%s reason=%s",
                    session_id,
                    team_name,
                    reason,
                )
                return False, reason
            return True, None
        except Exception as exc:
            logger.error("[TeamManager] interact failed: session_id=%s, error=%s", session_id, exc)
            return False, "exception"

    # TeamSkillEvolutionRail accessors.

    def get_team_skill_rail(self, session_id: str) -> Any | None:
        return self._team_skill_rails.get(session_id)

    def get_team_skill_create_rail(self, session_id: str) -> Any | None:
        return self._team_skill_create_rails.get(session_id)

    def find_team_skill_rail_for_request(self, request_id: str) -> Any | None:
        """Find the TeamSkillEvolutionRail that owns a pending approval with this request_id."""
        for rail in self._team_skill_rails.values():
            if request_id in getattr(rail, "_pending_approval_snapshots", {}):
                return rail
            if request_id in getattr(rail, "_pending_governance", {}):
                return rail
        return None

    async def drain_team_skill_events(self, session_id: str) -> list[dict]:
        """Drain buffered approval events from this session's TeamSkillEvolutionRail."""
        rail = self._team_skill_rails.get(session_id)
        if rail is None:
            return []
        return await rail.drain_pending_approval_events()

    def register_team_skill_rail(self, session_id: str, rail: Any) -> None:
        """Register a TeamSkillEvolutionRail instance for the given session."""
        self._team_skill_rails[session_id] = rail

    def register_team_member_skill_evolution_rail(self, session_id: str, rail: Any) -> None:
        """Register a member SkillEvolutionRail instance for hot config updates."""
        rails = self._team_member_skill_evolution_rails.setdefault(session_id, [])
        if rail not in rails:
            rails.append(rail)

    def register_team_skill_create_rail(self, session_id: str, rail: Any) -> None:
        """Register a TeamSkillCreateRail instance for hot config updates."""
        self._team_skill_create_rails[session_id] = rail

    def register_team_rail_context(self, session_id: str, context: TeamRailMountContext) -> None:
        """Register session context needed to rebuild missing team rails."""
        if getattr(context.member_info, "role", None) == "leader":
            self._team_rail_contexts[session_id] = context

    def get_team_rail_context(self, session_id: str) -> TeamRailMountContext | None:
        """Return the stored leader rail mount context for a session."""
        return self._team_rail_contexts.get(session_id)

    def register_team_live_rail(self, session_id: str, agent: Any, rail: Any) -> None:
        """Remember a live rail owner so hot reload can unregister mounted rails."""
        rails = self._team_live_rails.setdefault(session_id, [])
        entry = (agent, rail)
        if entry not in rails:
            rails.append(entry)

    def _clear_team_rail_registries(self, session_id: str) -> None:
        self._team_skill_rails.pop(session_id, None)
        self._team_member_skill_evolution_rails.pop(session_id, None)
        self._team_skill_create_rails.pop(session_id, None)
        self._team_rail_contexts.pop(session_id, None)
        self._team_live_rails.pop(session_id, None)
        self._team_shared_skill_link_targets.pop(session_id, None)

    async def _cancel_team_evolution_watcher(self, session_id: str) -> None:
        watcher_task = self._team_evolution_watchers.pop(session_id, None)
        if watcher_task and not watcher_task.done():
            watcher_task.cancel()
            try:
                await watcher_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning(
                    "[TeamManager] evolution watcher stop failed: session_id=%s error=%s",
                    session_id,
                    exc,
                )

    async def _unregister_live_rail(self, session_id: str, rail: Any) -> None:
        live_rails = self._team_live_rails.get(session_id, [])
        remaining: list[tuple[Any, Any]] = []
        for agent, live_rail in live_rails:
            if live_rail is not rail:
                remaining.append((agent, live_rail))
                continue
            unregister = getattr(agent, "unregister_rail", None)
            if callable(unregister):
                try:
                    result = unregister(live_rail)
                    if hasattr(result, "__await__"):
                        await result
                except Exception as exc:
                    logger.warning(
                        "[TeamManager] live rail unregister failed: session_id=%s rail=%s error=%s",
                        session_id,
                        type(live_rail).__name__,
                        exc,
                    )
        if remaining:
            self._team_live_rails[session_id] = remaining
        else:
            self._team_live_rails.pop(session_id, None)

    def _build_and_mount_member_rails_for_context(
        self,
        session_id: str,
        context: TeamRailMountContext,
        *,
        mount_team_skill_rail: bool,
        mount_team_skill_create_rail: bool,
        mount_skill_evolution_rail: bool,
    ) -> tuple[Any | None, Any | None]:
        """Rebuild team rails for a session using the stored mount context."""
        latest_config = get_config()
        context.team_workspace.config = latest_config
        member_rails = build_member_rails(
            member_info=context.member_info,
            runtime=context.runtime,
            team_workspace=context.team_workspace,
        )
        team_skill_rail: Any | None = None
        team_skill_create_rail: Any | None = None
        for rail in member_rails:
            if isinstance(rail, TeamSkillEvolutionRail) and mount_team_skill_rail:
                context.agent.add_rail(rail)
                self.register_team_live_rail(session_id, context.agent, rail)
                team_skill_rail = rail
            elif isinstance(rail, SkillEvolutionRail) and mount_skill_evolution_rail:
                context.agent.add_rail(rail)
                self.register_team_member_skill_evolution_rail(session_id, rail)
            elif isinstance(rail, TeamSkillCreateRail) and mount_team_skill_create_rail:
                context.agent.add_rail(rail)
                self.register_team_live_rail(session_id, context.agent, rail)
                team_skill_create_rail = rail

        if team_skill_rail is not None:
            self.register_team_skill_rail(session_id, team_skill_rail)
        if team_skill_create_rail is not None:
            self.register_team_skill_create_rail(session_id, team_skill_create_rail)
        return team_skill_rail, team_skill_create_rail

    async def update_evolution_config(self, config: dict[str, Any] | None) -> None:
        """Hot-update team evolution rails for existing team runtimes."""
        auto_scan_enabled = get_evolution_auto_scan_enabled(config)
        skill_create_enabled = get_skill_create_enabled(config)

        for rails in self._team_member_skill_evolution_rails.values():
            for rail in rails:
                try:
                    rail.auto_scan = auto_scan_enabled
                except Exception as exc:
                    logger.warning(
                        "[TeamManager] SkillEvolutionRail auto_scan update failed: %s",
                        exc,
                    )

        for rail in self._team_skill_rails.values():
            try:
                rail.auto_scan = auto_scan_enabled
            except Exception as exc:
                logger.warning(
                    "[TeamManager] TeamSkillEvolutionRail auto_scan update failed: %s",
                    exc,
                )

        if not skill_create_enabled:
            for session_id, rail in list(self._team_skill_create_rails.items()):
                await self._unregister_live_rail(session_id, rail)
                self._team_skill_create_rails.pop(session_id, None)
            return

        for session_id, context in list(self._team_rail_contexts.items()):
            if session_id in self._team_skill_create_rails:
                continue
            self._build_and_mount_member_rails_for_context(
                session_id,
                context,
                mount_team_skill_rail=False,
                mount_team_skill_create_rail=True,
                mount_skill_evolution_rail=False,
            )

    async def destroy_team(self, session_id: str) -> bool:
        async with self._lock:
            return await self._destroy_team(session_id)

    async def _destroy_other_sessions(self, current_session_id: str) -> None:
        stale_session_ids = [sid for sid in list(self._team_agents.keys()) if sid != current_session_id]
        for stale_session_id in stale_session_ids:
            await self._destroy_team(stale_session_id)

    async def _destroy_team(self, session_id: str) -> bool:
        await self._cleanup_runtime_locals(session_id)

        team_agent = self._team_agents.pop(session_id, None)
        cleaned = False
        try:
            if team_agent is None:
                logger.info("[TeamManager] no in-memory team for session_id=%s", session_id)
                return False

            token = set_session_id(session_id)
            try:
                try:
                    cleaned = await team_agent.destroy_team(force=True)
                finally:
                    await release_a2x_reservations_for_session(session_id, team_agent=team_agent)
                    await _stop_team_messager(team_agent, session_id=session_id)
            finally:
                reset_session_id(token)

            logger.info(
                "[TeamManager] Team cleaned via core API: session_id=%s cleaned=%s",
                session_id,
                cleaned,
            )
        except Exception as exc:
            logger.error(
                "[TeamManager] destroy team failed: session_id=%s error=%s",
                session_id,
                exc,
            )

        return cleaned

    async def cleanup_all(self) -> None:
        async with self._lock:
            session_ids = list(self._team_agents.keys())
            for session_id in session_ids:
                await self._destroy_team(session_id)
            logger.info("[TeamManager] all teams cleaned")

    def get_team_agent(self, session_id: str) -> TeamAgent | None:
        return self._team_agents.get(session_id)

    def get_monitor_handler(self, session_id: str) -> TeamMonitorHandler | None:
        return self._team_monitors.get(session_id)

    def register_monitor(self, session_id: str, handler: TeamMonitorHandler) -> None:
        self._team_monitors[session_id] = handler

    def register_workflow_handler(self, session_id: str, handler: Any) -> None:
        self._workflow_handlers[session_id] = handler

    def get_workflow_handler(self, session_id: str) -> Any | None:
        return self._workflow_handlers.get(session_id)

    def pop_workflow_handler(self, session_id: str) -> Any | None:
        return self._workflow_handlers.pop(session_id, None)

    def register_stream_task(self, session_id: str, task: asyncio.Task) -> None:
        self._stream_tasks[session_id] = task

    def _has_local_team_runtime(self, session_id: str) -> bool:
        """Return whether the session should use the legacy in-memory TeamAgent path."""
        return self._is_distributed_mode(get_config()) and session_id in self._team_agents

    async def attach_distributed_hooks_for_runner_runtime(
        self,
        team_name: str,
        session_id: str,
        channel_id: str | None = None,
    ) -> bool:
        """Attach distributed bootstrap hooks to Runner-owned TeamAgent.

        When team streaming uses Runner.run_agent_team_streaming(), the actual
        TeamAgent is created and cached by openjiuwen TeamRuntimeManager pool,
        not by TeamManager.create_team(). This method retrieves the Runner-owned
        TeamAgent from GLOBAL_RUNNER's pool and attaches distributed hooks.

        Args:
            team_name: Team name to look up in Runner pool.
            session_id: Session identifier for hook context.
            channel_id: Channel identifier for hook context.

        Returns:
            True if hooks attached successfully, False otherwise.
        """
        config_base = get_config()
        if not self._is_distributed_mode(config_base):
            logger.debug(
                "[TeamManager] non-distributed mode; skip Runner runtime hooks "
                "team_name=%s session_id=%s",
                team_name,
                session_id,
            )
            return False

        from openjiuwen.core.runner.runner import GLOBAL_RUNNER

        runtime_mgr = _runner_team_runtime_manager(GLOBAL_RUNNER)
        active_team = await runtime_mgr.pool.get(team_name)
        if active_team is None:
            logger.warning(
                "[TeamManager] Runner pool has no active team for distributed hooks "
                "team_name=%s session_id=%s",
                team_name,
                session_id,
            )
            return False

        team_agent = active_team.agent
        if team_agent is None:
            logger.warning(
                "[TeamManager] ActiveTeam has no agent instance for distributed hooks "
                "team_name=%s session_id=%s",
                team_name,
                session_id,
            )
            return False

        self._runner_team_agents[session_id] = team_agent

        try:
            from jiuwenswarm.agents.harness.team.remote_member_bootstrap import (
                attach_distributed_local_spawn_guard,
                attach_remote_bootstrap_ack_listener,
                attach_remote_teammate_bootstrap_listener,
                attach_shutdown_member_remote_cleanup_wrapper,
                attach_spawn_member_remote_bootstrap_wrapper,
            )

            attach_distributed_local_spawn_guard(
                team_agent,
                session_id=session_id,
                channel_id=channel_id,
            )
            attach_spawn_member_remote_bootstrap_wrapper(
                team_agent,
                session_id=session_id,
                channel_id=channel_id,
            )
            attach_shutdown_member_remote_cleanup_wrapper(
                team_agent,
                session_id=session_id,
                channel_id=channel_id,
            )
            attach_remote_bootstrap_ack_listener(
                team_agent,
                session_id=session_id,
                channel_id=channel_id,
            )
            attach_remote_teammate_bootstrap_listener(
                team_agent,
                session_id=session_id,
                channel_id=channel_id,
            )
            logger.info(
                "[TeamManager] distributed hooks attached to Runner-owned TeamAgent "
                "team_name=%s session_id=%s channel_id=%s",
                team_name,
                session_id,
                channel_id,
            )
            return True
        except Exception as exc:
            logger.warning(
                "[TeamManager] distributed hooks attach failed for Runner-owned TeamAgent "
                "team_name=%s session_id=%s error=%s",
                team_name,
                session_id,
                exc,
            )
            return False

    async def _stop_local_team_runtime(self, session_id: str, team_agent: TeamAgent) -> bool:
        stopped = False
        stop_coordination = getattr(team_agent, "stop_coordination", None) or getattr(
            team_agent,
            "_stop_coordination",
            None,
        )
        if callable(stop_coordination):
            try:
                await stop_coordination()
                stopped = True
            except Exception as exc:
                logger.warning(
                    "[TeamManager] stop local team coordination failed: session_id=%s error=%s",
                    session_id,
                    exc,
                )

        try:
            await release_a2x_reservations_for_session(session_id, team_agent=team_agent)
        except Exception as exc:
            logger.warning(
                "[TeamManager] release A2X reservations failed: session_id=%s error=%s",
                session_id,
                exc,
            )
        try:
            await _stop_team_messager(team_agent, session_id=session_id)
        except Exception as exc:
            logger.warning(
                "[TeamManager] stop local team messager failed: session_id=%s error=%s",
                session_id,
                exc,
            )
        return stopped

    async def _stop_runner_team_agent_transport(self, session_id: str) -> None:
        if not self._is_distributed_mode(get_config()):
            self._runner_team_agents.pop(session_id, None)
            return

        team_agent = self._runner_team_agents.pop(session_id, None)
        if team_agent is None:
            return

        stop_coordination = getattr(team_agent, "stop_coordination", None)
        if callable(stop_coordination):
            try:
                await stop_coordination()
            except Exception as exc:
                logger.warning(
                    "[TeamManager] stop Runner-owned team coordination failed: session_id=%s error=%s",
                    session_id,
                    exc,
                )

        try:
            await _stop_team_messager(team_agent, session_id=session_id)
        except Exception as exc:
            logger.warning(
                "[TeamManager] stop Runner-owned team messager failed: session_id=%s error=%s",
                session_id,
                exc,
            )

    async def _cleanup_runtime_locals(self, session_id: str) -> None:
        watcher_task = self._team_evolution_watchers.pop(session_id, None)
        if watcher_task and not watcher_task.done():
            watcher_task.cancel()
            try:
                await watcher_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning(
                    "[TeamManager] evolution watcher stop failed: session_id=%s error=%s",
                    session_id,
                    exc,
                )

        stream_task = self._stream_tasks.pop(session_id, None)
        if stream_task and not stream_task.done():
            stream_task.cancel()
            try:
                await stream_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning(
                    "[TeamManager] stream stop failed: session_id=%s error=%s",
                    session_id,
                    exc,
                )

        monitor_handler = self._team_monitors.pop(session_id, None)
        if monitor_handler is not None:
            try:
                await monitor_handler.stop()
            except Exception as exc:
                logger.warning(
                    "[TeamManager] monitor stop failed: session_id=%s error=%s",
                    session_id,
                    exc,
                )

        workflow_handler = self.pop_workflow_handler(session_id)
        if workflow_handler is not None:
            try:
                await workflow_handler.stop()
            except Exception as exc:
                logger.warning(
                    "[TeamManager] workflow handler stop failed: session_id=%s error=%s",
                    session_id,
                    exc,
                )

        self._clear_team_rail_registries(session_id)

    async def terminate_session_runtime(self, session_id: str, reason: str = "") -> bool:
        """Stop-like teardown for the current team session runtime.

        This stops the foreground stream/monitor owned by claw and then asks the
        Runner-owned team runtime to enter the stop state. Used for explicit
        team stop so the same session can resume later.
        """
        async with self._lock:
            has_stream_task = session_id in self._stream_tasks
            has_local_team_runtime = self._has_local_team_runtime(session_id)
            has_team_runtime = (
                has_local_team_runtime
                or session_id in self._team_monitors
                or self._active_session_id == session_id
                or self._pending_session_id == session_id
            )
            if not has_stream_task and not has_team_runtime:
                return False
            logger.info(
                "[TeamManager] %s terminate team session runtime: session_id=%s",
                reason,
                session_id,
            )

            # Resolve team_name early before cleanup, from active/pending/metadata
            team_name = self._resolve_session_team_name(session_id)

            # Stop Runner-owned runtime first before cleaning locals
            # to avoid gate/teardown races
            if team_name:
                try:
                    await Runner.stop_agent_team(team_name=team_name, session_id=session_id)
                except Exception as exc:
                    logger.warning(
                        "[TeamManager] runner stop failed: session_id=%s error=%s",
                        session_id,
                        exc,
                    )

            if has_local_team_runtime:
                cleaned = await self._destroy_team(session_id)
            else:
                cleaned = False

            await self._cleanup_runtime_locals(session_id)

            self.clear_active_runtime(session_id)
            self.clear_pending_runtime(session_id)
        logger.info(
            "[TeamManager] %steam session terminated: session_id=%s cleaned=%s",
            reason,
            session_id,
            cleaned,
        )
        return True

    async def cancel_session_runtime(self, session_id: str, reason: str = "") -> bool:
        """Cancel the current team session runtime, removing it from Runner pool.

        Unlike pause/terminate, this fully stops the Runner-owned team runtime
        so it is removed from the pool. This prevents subsequent sessions from
        hitting "present in pool but missing from DB" reject_inconsistent errors.

        Used for team cancel intent where the session should not be resumed.
        """
        async with self._lock:
            has_stream_task = session_id in self._stream_tasks
            has_local_team_runtime = self._has_local_team_runtime(session_id)
            has_team_runtime = (
                has_local_team_runtime
                or session_id in self._team_monitors
                or self._active_session_id == session_id
                or self._pending_session_id == session_id
            )
            if not has_stream_task and not has_team_runtime:
                return False

            logger.info(
                "[TeamManager] %s cancel team session runtime: session_id=%s",
                reason,
                session_id,
            )

            # Resolve team_name early before cleanup, from active/pending/metadata
            team_name = self._resolve_session_team_name(session_id)

            # Stop Runner-owned runtime first before cancelling stream task
            # to avoid gate/teardown races and ensure pool removal
            runner_stopped = False
            if team_name:
                try:
                    runner_stopped = await Runner.stop_agent_team(
                        team_name=team_name,
                        session_id=session_id,
                    )
                    logger.info(
                        "[TeamManager] Runner pool entry removed: session_id=%s team_name=%s stopped=%s",
                        session_id,
                        team_name,
                        runner_stopped,
                    )
                except Exception as exc:
                    logger.warning(
                        "[TeamManager] runner stop failed: session_id=%s team_name=%s error=%s",
                        session_id,
                        team_name,
                        exc,
                    )

            cleaned = False

            # Cleanup locals (watcher, stream, monitor, skill rails)
            await self._cleanup_runtime_locals(session_id)

            self.clear_active_runtime(session_id)
            self.clear_pending_runtime(session_id)

        logger.info(
            "[TeamManager] %steam session cancelled: session_id=%s cleaned=%s runner_stopped=%s",
            reason,
            session_id,
            cleaned,
            runner_stopped,
        )
        return True

    async def stop_session_runtime(self, session_id: str, reason: str = "") -> bool:
        """Stop the current team runtime for this session without deleting persisted data."""
        async with self._lock:
            has_stream_task = session_id in self._stream_tasks
            has_local_team_runtime = self._has_local_team_runtime(session_id)
            has_team_runtime = (
                has_local_team_runtime
                or session_id in self._runner_team_agents
                or session_id in self._team_monitors
                or self._active_session_id == session_id
                or self._pending_session_id == session_id
            )
            if not has_stream_task and not has_team_runtime:
                return False

            logger.info(
                "[TeamManager] %s stop team session runtime: session_id=%s",
                reason,
                session_id,
            )
            team_agent = self._team_agents.pop(session_id, None) if has_local_team_runtime else None
            await self._cleanup_runtime_locals(session_id)

            stopped = False
            if has_local_team_runtime and team_agent is not None:
                stopped = await self._stop_local_team_runtime(session_id, team_agent)

            team_name = self._resolve_session_team_name(session_id)

            if team_name:
                try:
                    runner_stopped = await Runner.stop_agent_team(team_name=team_name, session_id=session_id)
                    stopped = runner_stopped or stopped
                except Exception as exc:
                    logger.warning(
                        "[TeamManager] runner stop failed: session_id=%s team_name=%s error=%s",
                        session_id,
                        team_name,
                        exc,
                    )
            if not has_local_team_runtime:
                try:
                    team_agent = self._runner_team_agents.get(session_id)
                    await release_a2x_reservations_for_session(session_id, team_agent=team_agent)
                except Exception as exc:
                    logger.warning(
                        "[TeamManager] release A2X reservations failed: session_id=%s error=%s",
                        session_id,
                        exc,
                    )
                await self._stop_runner_team_agent_transport(session_id)

            self.clear_active_runtime(session_id)
            self.clear_pending_runtime(session_id)

        logger.info(
            "[TeamManager] %steam session stopped: session_id=%s stopped=%s",
            reason,
            session_id,
            stopped,
        )
        return True

    async def pause_session_runtime(self, session_id: str, reason: str = "") -> bool:
        """Pause the current team runtime for this session.

        Team runtimes are persistent. The current implementation pauses by
        tearing down the foreground stream task and parking the Runner-owned
        runtime in paused state so a later `chat.send` can resume it.
        """
        async with self._lock:
            has_stream_task = session_id in self._stream_tasks
            has_local_team_runtime = self._has_local_team_runtime(session_id)
            has_team_runtime = (
                has_local_team_runtime
                or session_id in self._team_monitors
                or self._active_session_id == session_id
                or self._pending_session_id == session_id
            )
            if not has_stream_task and not has_team_runtime:
                return False

            logger.info(
                "[TeamManager] %s pause team session runtime: session_id=%s",
                reason,
                session_id,
            )

            team_name = self._resolve_session_team_name(session_id)
            runner_paused = False
            if team_name:
                try:
                    runner_paused = await Runner.pause_agent_team(
                        team_name=team_name,
                        session_id=session_id,
                    )
                except Exception as exc:
                    logger.warning(
                        "[TeamManager] runner pause failed: session_id=%s team_name=%s error=%s",
                        session_id,
                        team_name,
                        exc,
                    )

            await self._cleanup_runtime_locals(session_id)
            self.clear_active_runtime(session_id)
            self.clear_pending_runtime(session_id)

        logger.info(
            "[TeamManager] %steam session paused: session_id=%s runner_paused=%s",
            reason,
            session_id,
            runner_paused,
        )
        return True

    async def delete_session_runtime(self, session_id: str, reason: str = "") -> bool:
        """Delete a team-mode session and its session-scoped team data.

        Jiuwenswarm scopes team names by session id, so deleting a
        team-mode session should delete the corresponding Agent Team
        before the caller removes the local session directory. If the
        team name cannot be resolved from session metadata, fall back to
        releasing only the session checkpoint.
        """
        team_name = self._resolve_delete_session_team_name(session_id)

        await self.stop_session_runtime(session_id, reason=reason)

        try:
            if team_name:
                await Runner.delete_agent_team(
                    team_name=team_name,
                    session_ids=[session_id],
                    force=True,
                )
            else:
                logger.warning(
                    "[TeamManager] delete session runtime fell back to session release: "
                    "session_id=%s reason=missing_team_name",
                    session_id,
                )
                await Runner.release(session_id)
            logger.info(
                "[TeamManager] %steam session deleted: session_id=%s team_name=%s",
                reason,
                session_id,
                team_name,
            )
            return True
        except Exception as exc:
            logger.warning(
                "[TeamManager] failed to delete team session runtime: session_id=%s team_name=%s error=%s",
                session_id,
                team_name,
                exc,
            )
            return False

    async def cancel_all_stream_tasks(self, reason: str = "") -> None:
        """Cancel Team stream tasks after AgentServer disconnects."""
        async with self._lock:
            pending = list(self._stream_tasks.items())
        for session_id, task in pending:
            if task.done():
                continue
            logger.info(
                "[TeamManager] %s cancel stream task session_id=%s",
                reason,
                session_id,
            )
            task.cancel()
        for session_id, task in pending:
            if task.done():
                continue
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning(
                    "[TeamManager] stream task await after cancel failed session_id=%s: %s",
                    session_id,
                    exc,
                )
        async with self._lock:
            self._stream_tasks.clear()


_team_managers: dict[str, TeamManager] = {}


def get_team_manager(channel_id: str | None = None) -> TeamManager:
    resolved_channel_id = str(channel_id or "default").strip() or "default"
    manager = _team_managers.get(resolved_channel_id)
    if manager is None:
        manager = TeamManager()
        _team_managers[resolved_channel_id] = manager
    return manager


def find_team_skill_rail_across_managers(request_id: str) -> Any | None:
    """Find the TeamSkillEvolutionRail that owns a pending request across all channel managers."""
    for manager in _team_managers.values():
        rail = manager.find_team_skill_rail_for_request(request_id)
        if rail is not None:
            return rail
    return None


def refresh_team_shared_skill_links_across_managers(session_id: str | None = None) -> bool:
    """Refresh team shared skill links across channel managers."""
    refreshed = 0
    for manager in _team_managers.values():
        if session_id is None:
            refreshed += manager.refresh_all_team_shared_skill_links()
        elif manager.refresh_team_shared_skill_links(session_id):
            refreshed += 1
    return refreshed > 0


async def cancel_all_team_stream_tasks_across_managers(reason: str = "") -> None:
    """Cancel team stream tasks for all channel managers."""
    for manager in list(_team_managers.values()):
        await manager.cancel_all_stream_tasks(reason=reason)


async def stop_team_session_runtime_across_managers(session_id: str, reason: str = "") -> bool:
    """Stop a team session runtime across all channel managers."""
    stopped = False
    for manager in list(_team_managers.values()):
        manager_stopped = await manager.stop_session_runtime(session_id, reason=reason)
        stopped = manager_stopped or stopped
    return stopped


def get_all_team_managers() -> list[TeamManager]:
    """Return a snapshot of all channel-scoped team managers."""
    return list(_team_managers.values())


def reset_team_manager(channel_id: str | None = None) -> None:
    if channel_id is None:
        _team_managers.clear()
        return

    resolved_channel_id = str(channel_id).strip() or "default"
    _team_managers.pop(resolved_channel_id, None)
