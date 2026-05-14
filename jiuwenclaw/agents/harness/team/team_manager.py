# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Team lifecycle manager."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.paths import team_home
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.core.runner import Runner
from openjiuwen.harness import DeepAgent
from openjiuwen.harness.rails import SkillEvolutionRail, TeamSkillCreateRail, TeamSkillRail
from jiuwenclaw.agents.harness.common.plugins.rail_manager import get_rail_manager
from jiuwenclaw.agents.harness.team.rails.team_member_skill_toolkit_rail import (
    MemberSkillToolkitRail,
)
from jiuwenclaw.server.runtime.skill.skill_manager import SkillManager

from jiuwenclaw.agents.harness.team.bootstrap import configure_agent_teams_home

configure_agent_teams_home()

from jiuwenclaw.agents.harness.team.config_loader import (
    load_team_spec_dict,
)
from jiuwenclaw.agents.harness.team.distributed_runtime import (
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
from jiuwenclaw.agents.harness.team.monitor_handler import TeamMonitorHandler
from jiuwenclaw.agents.harness.team.remote_member_bootstrap import release_a2x_reservations_for_team
from jiuwenclaw.common.config import get_config, get_default_models
from jiuwenclaw.agents.harness.team.team_runtime_inheritance import (
    MemberInfo,
    RAIL_WHITELIST,
    RuntimeInfo,
    TeamWorkspaceInfo,
    get_evolution_auto_scan_enabled,
    get_skill_create_enabled,
    build_member_rails,
    filter_inheritable_ability_cards,
    get_default_model_name,
)
from jiuwenclaw.common.utils import get_agent_skills_dir
from jiuwenclaw.server.runtime.session.session_metadata import get_session_metadata

logger = logging.getLogger(__name__)

# Wall-clock cap for a single external command (pg_isready, systemctl, etc.).
_SUBPROCESS_TIMEOUT_SEC = 120.0
# After pg_ctlcluster/systemd reports start, the server may still be initializing.
_PG_POST_START_READY_MAX_SEC = 30.0
_PG_POST_START_READY_INIT_SLEEP = 0.4
_PG_POST_START_READY_MAX_SLEEP = 2.0
_PG_POST_START_READY_BACKOFF = 1.45
_PG_POST_START_LOG_EVERY_SEC = 5.0


def _sync_skills_dir(source: Path, target: Path) -> None:
    """Copy every valid skill directory from *source* into *target*.

    A valid skill is a sub-directory containing a ``SKILL.md`` file.
    Existing skills in *target* are overwritten so the latest version
    always wins.
    """
    if not source.is_dir():
        return
    target.mkdir(parents=True, exist_ok=True)
    synced = 0
    for skill_dir in source.iterdir():
        if not skill_dir.is_dir() or not (skill_dir / "SKILL.md").is_file():
            continue
        dest = target / skill_dir.name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(skill_dir, dest)
        synced += 1
    if synced:
        logger.info("[TeamManager] synced %d skills: %s -> %s", synced, source, target)


@dataclass
class TeamRailMountContext:
    """Context needed to rebuild team rails after a hot config toggle."""

    agent: Any
    member_info: MemberInfo
    runtime: RuntimeInfo
    team_workspace: TeamWorkspaceInfo


async def _stop_team_messager(team_agent: Any, *, session_id: str) -> None:
    """Stop a team's mailbox transport so per-team ZMQ sockets release their ports."""
    messager = getattr(team_agent, "_messager", None) or getattr(team_agent, "mailbox_transport", None)
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
        self._team_monitors: dict[str, TeamMonitorHandler] = {}
        self._stream_tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self._active_session_id: str | None = None
        self._active_team_name: str | None = None
        self._pending_session_id: str | None = None
        self._pending_team_name: str | None = None
        # session_id → TeamSkillRail instance (set by customizer, used for drain/approval)
        self._team_skill_rails: dict[str, Any] = {}
        # session_id → member SkillEvolutionRail instances
        self._team_member_skill_evolution_rails: dict[str, list[Any]] = {}
        # session_id → TeamSkillCreateRail instance
        self._team_skill_create_rails: dict[str, Any] = {}
        # session_id → context used to rebuild team rails on config enable
        self._team_rail_contexts: dict[str, TeamRailMountContext] = {}
        # session_id → live rails and owning DeepAgent, for hot-unregister
        self._team_live_rails: dict[str, list[tuple[Any, Any]]] = {}
        # session_id → (workspace_skills_dir, global_team_skills_dir)
        self._team_skill_sync_targets: dict[str, tuple[Path, Path]] = {}
        # session_id → evolution watcher task
        self._team_evolution_watchers: dict[str, asyncio.Task] = {}

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

    async def get_enriched_team_spec(
        self,
        session_id: str,
        deep_agent: DeepAgent,
        request_id: str | None = None,
        channel_id: str | None = None,
        request_metadata: dict[str, Any] | None = None,
    ) -> TeamAgentSpec:
        config_base = get_config()
        await self._ensure_postgresql_for_leader(config_base)
        spec = self._load_team_spec(session_id)
        spec.agent_customizer = self.build_agent_customizer(
            spec,
            deep_agent,
            session_id,
            request_id=request_id,
            channel_id=channel_id,
            request_metadata=request_metadata,
        )
        return spec

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
    def register_member_runtime_tools(
        agent: DeepAgent,
        *,
        session_id: str,
        request_id: str | None,
        channel_id: str | None,
        request_metadata: dict[str, Any] | None,
    ) -> None:
        from jiuwenclaw.agents.harness.common.tools.cron.cron_runtime import CronRuntimeBridge
        from jiuwenclaw.agents.harness.common.tools.send_file_to_user import SendFileToolkit

        agent_id = getattr(getattr(agent, "card", None), "id", None)
        cron_runtime = CronRuntimeBridge()
        cron_context = SimpleNamespace(
            tool_scope=f"team_member_{agent_id or 'unknown'}",
            channel_id=channel_id or "web",
            session_id=session_id,
            metadata=request_metadata,
            mode="team",
        )

        try:
            cron_tools = cron_runtime.build_tools(
                context=cron_context,
                agent_id=agent_id,
                language=getattr(agent.deep_config, "language", "cn")
            )
            for cron_tool in cron_tools:
                if not Runner.resource_mgr.get_tool(cron_tool.card.id):
                    Runner.resource_mgr.add_tool(cron_tool)
                agent.ability_manager.add(cron_tool.card)
            logger.info("[TeamManager] Registered %d cron tools for member agent=%s", len(cron_tools), agent_id)
        except Exception as exc:
            logger.warning("[TeamManager] cron tool registration failed for member agent=%s: %s", agent_id, exc)

        if not request_id or not channel_id:
            logger.info("[TeamManager] SendFileToolkit skipped: missing request_id or channel_id")
            return

        try:
            config = get_config()
            send_file_enabled = (
                config.get("channels", {})
                .get(str(channel_id), {})
                .get("send_file_allowed", False)
            )
            if not send_file_enabled:
                logger.info(
                    "[TeamManager] SendFileToolkit skipped: send_file_allowed=False for channel=%s",
                    channel_id,
                )
                return

            for existing in list(agent.ability_manager.list() or []):
                if getattr(existing, "name", "").startswith("send_file_to_user"):
                    agent.ability_manager.remove(existing.name)

            send_file_toolkit = SendFileToolkit(
                request_id=request_id,
                session_id=session_id,
                channel_id=channel_id,
                metadata=request_metadata,
            )
            for sf_tool in send_file_toolkit.get_tools():
                Runner.resource_mgr.add_tool(sf_tool)
                agent.ability_manager.add(sf_tool.card)
            logger.info("[TeamManager] SendFileToolkit registered for channel=%s", channel_id)
        except Exception as exc:
            logger.warning("[TeamManager] SendFileToolkit registration failed: %s", exc)

    @staticmethod
    def build_agent_customizer(
        spec: TeamAgentSpec,
        deep_agent: DeepAgent,
        session_id: str,
        *,
        request_id: str | None = None,
        channel_id: str | None = None,
        request_metadata: dict[str, Any] | None = None,
    ) -> Callable[..., None]:
        global_skills_dir = get_agent_skills_dir()
        global_skills_state_path = global_skills_dir / "skills_state.json"
        resolved_channel = channel_id or "default"
        resolved_model_name = get_default_model_name()

        # Resolve team shared workspace skills directory for TeamSkillRail.
        ws_config = spec.workspace
        team_ws_root = (
            ws_config.root_path if ws_config and ws_config.root_path
            else str(team_home(spec.team_name) / "team-workspace")
        )
        team_ws_skills_dir = Path(team_ws_root) / "skills"
        team_ws_trajectories_dir = Path(team_ws_root) / "trajectories"

        def resolve_member_spec(
            member_name: str | None,
            role: str | None,
        ) -> Any:
            if member_name and member_name in spec.agents:
                return spec.agents[member_name]
            if role and role in spec.agents:
                return spec.agents[role]
            return spec.agents.get("leader")

        def resolve_member_skills(
            member_name: str | None,
            role: str | None,
        ) -> tuple[bool, list[str]]:
            member_spec = resolve_member_spec(member_name, role)
            if member_spec is None or not hasattr(member_spec, "skills"):
                return False, []

            skills = getattr(member_spec, "skills", None)
            if skills is None:
                return False, []

            return True, [str(skill).strip() for skill in skills if str(skill).strip()]

        def copy_member_configured_skills(
            member_skills_dir: Path,
            selected_skills: list[str],
        ) -> None:
            """Copy member-configured skills to member's own skills directory."""
            if not global_skills_dir.exists():
                logger.warning("[TeamManager] global_skills_dir does not exist: %s", global_skills_dir)
                return

            selected_skill_set = set(selected_skills)
            member_skills_dir.mkdir(parents=True, exist_ok=True)
            copied_count = 0
            for skill_dir in global_skills_dir.iterdir():
                if not skill_dir.is_dir():
                    continue
                if not (skill_dir / "SKILL.md").is_file():
                    continue
                if skill_dir.name not in selected_skill_set:
                    continue
                dest = member_skills_dir / skill_dir.name
                if dest.exists():
                    continue
                shutil.copytree(skill_dir, dest)
                copied_count += 1
                logger.info("[TeamManager] Copied skill '%s' to member workspace", skill_dir.name)

            existing_skill_names = {
                path.name for path in member_skills_dir.iterdir() if path.is_dir()
            }
            missing = sorted(selected_skill_set - existing_skill_names)
            if missing:
                logger.warning("[TeamManager] configured skills not found in global dir: %s", missing)

            logger.info("[TeamManager] Total configured skills copied to member: %d", copied_count)

        def build_member_skill_state(member_skills_dir: Path) -> dict[str, Any]:
            state: dict[str, Any] = {
                "marketplaces": [],
                "installed_plugins": [],
                "local_skills": [],
            }
            if global_skills_state_path.is_file():
                try:
                    loaded_state = json.loads(global_skills_state_path.read_text(encoding="utf-8"))
                    if isinstance(loaded_state, dict):
                        state.update(loaded_state)
                except Exception as exc:
                    logger.warning("[TeamManager] failed to load global skills_state.json: %s", exc)

            state["marketplaces"] = SkillManager.normalize_marketplaces(
                state.get("marketplaces")
            )

            actual_skill_names = sorted(
                path.name
                for path in member_skills_dir.iterdir()
                if path.is_dir() and (path / "SKILL.md").is_file()
            )
            actual_skill_set = set(actual_skill_names)

            installed_plugins = []
            for plugin in state.get("installed_plugins", []):
                if not isinstance(plugin, dict):
                    continue
                plugin_name = str(plugin.get("name", "")).strip()
                if not plugin_name or plugin_name not in actual_skill_set:
                    continue
                installed_plugins.append(plugin)

            local_skills = []
            for local_skill in state.get("local_skills", []):
                if not isinstance(local_skill, dict):
                    continue
                skill_name = str(local_skill.get("name", "")).strip()
                if not skill_name or skill_name not in actual_skill_set:
                    continue
                local_skills.append(local_skill)

            existing_plugin_names = {
                str(plugin.get("name", "")).strip()
                for plugin in installed_plugins
                if isinstance(plugin, dict)
            }
            existing_local_names = {
                str(local_skill.get("name", "")).strip()
                for local_skill in local_skills
                if isinstance(local_skill, dict)
            }
            for skill_name in actual_skill_names:
                if skill_name not in existing_plugin_names:
                    installed_plugins.append(
                        {
                            "name": skill_name,
                            "marketplace": "",
                            "version": "",
                            "commit": "",
                            "source": "project",
                            "installed_at": "",
                        }
                    )
                if skill_name not in existing_local_names:
                    local_skills.append(
                        {
                            "name": skill_name,
                            "origin": str(member_skills_dir / skill_name),
                            "source": "project",
                        }
                    )

            state["installed_plugins"] = installed_plugins
            state["local_skills"] = local_skills
            return state

        def write_member_skill_state(member_skills_dir: Path) -> None:
            state_file = member_skills_dir / "skills_state.json"
            state = build_member_skill_state(member_skills_dir)
            state_file.write_text(
                json.dumps(state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info("[TeamManager] Wrote member skills_state.json: %s", state_file)

        def customizer(
            agent: DeepAgent,
            member_name: str | None = None,
            role: str | None = None,
        ) -> None:
            logger.info(
                "[TeamManager] customizer called: channel=%s member_name=%s role=%s",
                resolved_channel,
                member_name,
                role,
            )
            agent_ws = agent.deep_config.workspace if agent.deep_config else None
            if agent_ws:
                logger.debug("[TeamManager] member workspace.root_path=%s", agent_ws.root_path)
            else:
                logger.warning("[TeamManager] agent deep_config.workspace is None")
            parent_adapter_mode = str(
                getattr(deep_agent, "_jiuwenclaw_adapter_mode", "") or ""
            ).lower()

            inheritable_cards = filter_inheritable_ability_cards(deep_agent)
            existing_ability_ids = {card.id for card in agent.ability_manager.list() or []}
            added_count = 0
            for card in inheritable_cards:
                if card.id not in existing_ability_ids:
                    agent.ability_manager.add(card)
                    existing_ability_ids.add(card.id)
                    added_count += 1
                else:
                    logger.debug("[TeamManager] Ability '%s' already exists, skipped", card.name)
            logger.info(
                "[TeamManager] Added %d inheritable abilities (total: %d)",
                added_count,
                len(existing_ability_ids),
            )

            member_workspace = agent.deep_config.workspace if agent.deep_config else None
            member_skills_dir_resolved: Path | None = None
            member_skill_manager: Any | None = None
            if member_workspace and member_workspace.root_path:
                member_skills_dir = Path(member_workspace.root_path) / "skills"
                member_skills_dir_resolved = member_skills_dir
                skills_configured, selected_skills = resolve_member_skills(member_name, role)

                # Copy member-configured skills to member's own skills directory
                # Note: global skills are already copied to team shared directory in create_team
                try:
                    # Ensure member skills directory exists
                    member_skills_dir.mkdir(parents=True, exist_ok=True)
                    if skills_configured and selected_skills:
                        copy_member_configured_skills(member_skills_dir, selected_skills)
                    # Member directory always needs skills_state.json
                    write_member_skill_state(member_skills_dir)
                except Exception as exc:
                    logger.warning("[TeamManager] skill copy failed: %s", exc)

                try:
                    member_skill_manager = SkillManager(workspace_dir=str(member_workspace.root_path))
                except Exception as exc:
                    logger.warning("[TeamManager] member SkillManager setup failed: %s", exc)

                # Create independent SkillManager and SkillToolkit for member
                try:
                    agent.add_rail(
                        MemberSkillToolkitRail(
                            workspace_dir=str(member_workspace.root_path),
                        )
                    )
                    logger.info(
                        "[TeamManager] MemberSkillToolkitRail queued for member workspace: %s",
                        member_workspace.root_path,
                    )
                except Exception as exc:
                    logger.warning("[TeamManager] MemberSkillToolkitRail setup failed: %s", exc)

            if parent_adapter_mode == "code":
                try:
                    from jiuwenclaw.server.runtime.agent_adapter.interface_code import (
                        configure_code_team_member_agent,
                    )

                    parent_workspace = (
                        deep_agent.deep_config.workspace if deep_agent.deep_config else None
                    )
                    configure_code_team_member_agent(
                        agent,
                        parent_agent=deep_agent,
                        skill_manager=member_skill_manager,
                        member_name=member_name,
                        role=role,
                        session_id=session_id,
                        channel_id=resolved_channel,
                        project_dir=(
                            str(parent_workspace.root_path)
                            if parent_workspace and parent_workspace.root_path
                            else None
                        ),
                    )
                except Exception as exc:
                    logger.warning(
                        "[TeamManager] code team member adapter setup failed: member=%s role=%s error=%s",
                        member_name,
                        role,
                        exc,
                    )

            # Build all member rails (common + skill rails via role).
            team_workspace = TeamWorkspaceInfo(
                root_dir=str(Path(team_ws_root)),
                skills_dir=str(team_ws_skills_dir),
                trajectories_dir=str(team_ws_trajectories_dir),
                team_id=spec.team_name,
                config=get_config(),
            )

            try:
                member_rails = build_member_rails(
                    member_info=MemberInfo(
                        agent_name=getattr(agent.card, "name", "team_member"),
                        model_name=resolved_model_name,
                        role=role
                    ),
                    runtime=RuntimeInfo(channel=resolved_channel),
                    team_workspace=team_workspace,
                )
                team_skill_rail: Any | None = None
                team_skill_create_rail: Any | None = None
                for rail in member_rails:
                    if type(rail).__name__ in RAIL_WHITELIST:
                        agent.add_rail(rail)
                        if isinstance(rail, (TeamSkillRail, TeamSkillCreateRail)):
                            get_team_manager(resolved_channel).register_team_live_rail(
                                session_id,
                                agent,
                                rail,
                            )
                    else:
                        logger.debug("[TeamManager] Skipping non-whitelisted rail: %s", type(rail).__name__)
                    if isinstance(rail, TeamSkillRail):
                        team_skill_rail = rail
                    elif isinstance(rail, SkillEvolutionRail):
                        get_team_manager(resolved_channel).register_team_member_skill_evolution_rail(
                            session_id,
                            rail,
                        )
                    elif isinstance(rail, TeamSkillCreateRail):
                        team_skill_create_rail = rail
                logger.info("[TeamManager] Added %d rails for team member", len(member_rails))
                # Register TeamSkillRail with TeamManager for approval/sync.
                if team_skill_rail is not None:
                    tm = get_team_manager(resolved_channel)
                    tm.register_team_skill_rail(session_id, team_skill_rail)
                    tm.register_team_skill_sync_target(
                        session_id,
                        team_ws_skills_dir,
                        get_agent_skills_dir(),
                    )
                    logger.info(
                        "[TeamManager] TeamSkillRail mounted on leader "
                        "(skills_dir=%s, sync_target=%s)",
                        team_ws_skills_dir, get_agent_skills_dir(),
                    )
                if team_skill_create_rail is not None:
                    get_team_manager(resolved_channel).register_team_skill_create_rail(
                        session_id,
                        team_skill_create_rail,
                    )
                get_team_manager(resolved_channel).register_team_rail_context(
                    session_id,
                    TeamRailMountContext(
                        agent=agent,
                        member_info=MemberInfo(
                            agent_name=getattr(agent.card, "name", "team_member"),
                            model_name=resolved_model_name,
                            role=role,
                        ),
                        runtime=RuntimeInfo(channel=resolved_channel),
                        team_workspace=team_workspace,
                    ),
                )
            except Exception as exc:
                logger.warning("[TeamManager] build_member_rails failed: %s", exc)

            rail_manager = get_rail_manager()
            for rail_name in rail_manager.get_registered_rail_names():
                try:
                    rail_instance = rail_manager.load_rail_instance_without_enabled_check(rail_name)
                    if rail_instance is not None:
                        agent.add_rail(rail_instance)
                        logger.info("[TeamManager] Added extension rail: %s", rail_name)
                except Exception as exc:
                    logger.warning("[TeamManager] add rail %s failed: %s", rail_name, exc)

            TeamManager.register_member_runtime_tools(
                agent,
                session_id=session_id,
                request_id=request_id,
                channel_id=channel_id,
                request_metadata=request_metadata,
            )

        return customizer

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
    def _copy_global_skills_to_team_shared_dir(spec: TeamAgentSpec) -> None:
        """Copy global skills to team shared directory (executed once after team build)."""
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

        # Check if already copied (via marker file)
        copied_marker = team_shared_skills_dir / ".team_skills_copied"
        if copied_marker.exists():
            logger.info("[TeamManager] Team shared skills already copied, skipping")
            return

        # Copy entire skills directory (including skills_state.json)
        team_shared_skills_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(global_skills_dir, team_shared_skills_dir, dirs_exist_ok=True)

        # Write marker file to indicate copy completed
        copied_marker.write_text("", encoding="utf-8")
        logger.info("[TeamManager] Copied global skills dir to team shared: %s", team_shared_skills_dir)

    @staticmethod
    def ensure_team_shared_skills_initialized(spec: TeamAgentSpec) -> None:
        """Ensure team shared skills are available in the team workspace."""
        TeamManager._copy_global_skills_to_team_shared_dir(spec)

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

        spec.agent_customizer = self.build_agent_customizer(
            spec,
            deep_agent,
            session_id,
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
            # After build, copy global skills to team shared directory (only once)
            self.ensure_team_shared_skills_initialized(spec)

            if self._is_distributed_mode(config_base):
                try:
                    from jiuwenclaw.agents.harness.team.remote_member_bootstrap import (
                        attach_distributed_local_spawn_guard,
                        attach_remote_bootstrap_ack_listener,
                        attach_remote_teammate_bootstrap_listener,
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

    async def interact(self, session_id: str, user_input: str) -> bool:
        try:
            if session_id != self._active_session_id or not self._active_team_name:
                logger.warning(
                    "[TeamManager] interact ignored for non-active team session: "
                    "session_id=%s active_session_id=%s active_team_name=%s",
                    session_id,
                    self._active_session_id,
                    self._active_team_name,
                )
                return False

            team_name = self._active_team_name
            success = await Runner.interact_agent_team(
                user_input,
                team_name=team_name,
                session_id=session_id,
            )
            if not success:
                logger.warning(
                    "[TeamManager] interact failed against runner runtime: session_id=%s team=%s",
                    session_id,
                    team_name,
                )
            return success
        except Exception as exc:
            logger.error("[TeamManager] interact failed: session_id=%s, error=%s", session_id, exc)
            return False

    # TeamSkillRail accessors.

    def get_team_skill_rail(self, session_id: str) -> Any | None:
        return self._team_skill_rails.get(session_id)

    def get_team_skill_create_rail(self, session_id: str) -> Any | None:
        return self._team_skill_create_rails.get(session_id)

    def find_team_skill_rail_for_request(self, request_id: str) -> Any | None:
        """Find the TeamSkillRail that owns a pending patch with this request_id."""
        for rail in self._team_skill_rails.values():
            if request_id in getattr(rail, "_pending_patch_snapshots", {}):
                return rail
        return None

    async def drain_team_skill_events(self, session_id: str) -> list[dict]:
        """Drain buffered approval events from this session's TeamSkillRail."""
        rail = self._team_skill_rails.get(session_id)
        if rail is None:
            return []
        return await rail.drain_pending_approval_events()

    def register_team_skill_rail(self, session_id: str, rail: Any) -> None:
        """Register a TeamSkillRail instance for the given session."""
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
        self._team_rail_contexts[session_id] = context

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
        self._team_skill_sync_targets.pop(session_id, None)

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
            if isinstance(rail, TeamSkillRail) and mount_team_skill_rail:
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
            if context.team_workspace.skills_dir:
                self.register_team_skill_sync_target(
                    session_id,
                    Path(context.team_workspace.skills_dir),
                    get_agent_skills_dir(),
                )
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

        if not auto_scan_enabled:
            for session_id, rail in list(self._team_skill_rails.items()):
                await self._cancel_team_evolution_watcher(session_id)
                await self._unregister_live_rail(session_id, rail)
                self._team_skill_rails.pop(session_id, None)
                self._team_skill_sync_targets.pop(session_id, None)

        if not skill_create_enabled:
            for session_id, rail in list(self._team_skill_create_rails.items()):
                await self._unregister_live_rail(session_id, rail)
                self._team_skill_create_rails.pop(session_id, None)

        for session_id, context in list(self._team_rail_contexts.items()):
            needs_team_skill_rail = auto_scan_enabled and session_id not in self._team_skill_rails
            needs_team_skill_create_rail = skill_create_enabled and session_id not in self._team_skill_create_rails
            needs_skill_evolution_rail = (
                auto_scan_enabled
                and not self._team_member_skill_evolution_rails.get(session_id)
            )
            if needs_team_skill_rail or needs_team_skill_create_rail or needs_skill_evolution_rail:
                self._build_and_mount_member_rails_for_context(
                    session_id,
                    context,
                    mount_team_skill_rail=needs_team_skill_rail,
                    mount_team_skill_create_rail=needs_team_skill_create_rail,
                    mount_skill_evolution_rail=needs_skill_evolution_rail,
                )

    def register_team_skill_sync_target(
        self, session_id: str, source: Path, target: Path,
    ) -> None:
        """Register skill sync directories for the given session."""
        self._team_skill_sync_targets[session_id] = (source, target)

    def has_team_skill_sync_target(self, session_id: str) -> bool:
        """Return whether the session has a registered team skill sync target."""
        return session_id in self._team_skill_sync_targets

    # Skill sync helpers.

    def sync_team_skills(self, session_id: str) -> None:
        """Sync team skills from workspace dir to global team_skills dir after approval."""
        sync_info = self._team_skill_sync_targets.get(session_id)
        if sync_info is None:
            logger.debug("[TeamManager] no sync target for session_id=%s", session_id)
            return
        source, target = sync_info
        _sync_skills_dir(source, target)

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
                    await release_a2x_reservations_for_team(team_agent)
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

        try:
            from jiuwenclaw.agents.harness.team.remote_member_bootstrap import (
                attach_distributed_local_spawn_guard,
                attach_remote_bootstrap_ack_listener,
                attach_remote_teammate_bootstrap_listener,
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
            await release_a2x_reservations_for_team(team_agent)
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

            if has_local_team_runtime:
                cleaned = await self._destroy_team(session_id)
            else:
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
        """Delete a single team session runtime without deleting the whole team."""
        team_name = self._resolve_session_team_name(session_id)

        await self.stop_session_runtime(session_id, reason=reason)

        try:
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
    """Find the TeamSkillRail that owns a pending request across all channel managers."""
    for manager in _team_managers.values():
        rail = manager.find_team_skill_rail_for_request(request_id)
        if rail is not None:
            return rail
    return None


def sync_team_skills_across_managers(session_id: str) -> bool:
    """Sync team skills for the given session across all channel managers."""
    for manager in _team_managers.values():
        if manager.has_team_skill_sync_target(session_id):
            manager.sync_team_skills(session_id)
            return True
    return False


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
