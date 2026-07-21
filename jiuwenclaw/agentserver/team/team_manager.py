# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Team lifecycle manager."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.paths import team_home
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.spawn.context import reset_session_id, set_session_id
from openjiuwen.harness import DeepAgent

from jiuwenclaw.agentserver.runtime_scope import RuntimeScopeKey
from jiuwenclaw.agentserver.team.bootstrap import configure_agent_teams_home

configure_agent_teams_home()

from jiuwenclaw.agentserver.team.config_loader import (
    load_team_spec_dict,
)
from jiuwenclaw.agentserver.team.monitor_handler import TeamMonitorHandler
from jiuwenclaw.agentserver.team.team_runtime_inheritance import (
    RAIL_WHITELIST,
    MemberRailConfig,
    build_member_rails,
    filter_inheritable_ability_cards,
    get_default_model_name,
)
from jiuwenclaw.agentserver.tools.deepresearch_tools import (
    push_deepresearch_route,
    reset_deepresearch_route,
)

logger = logging.getLogger(__name__)

# (service_id, agent_id, session_id)
_TeamSessionKey = tuple[str, str, str]


class TeamManager:
    """Manage team instances across tenants and sessions.

    Mutual exclusion (product 2b): within one ``(service_id, agent_id)`` at most
    one live Team; creating a team for session B destroys other sessions of the
    same tenant. Different tenants do not affect each other.
    """

    def __init__(self):
        self._team_agents: dict[_TeamSessionKey, TeamAgent] = {}
        self._team_monitors: dict[_TeamSessionKey, TeamMonitorHandler] = {}
        self._stream_tasks: dict[_TeamSessionKey, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _require_session_scope(scope: RuntimeScopeKey) -> _TeamSessionKey:
        if not str(scope.session_id or "").strip():
            raise ValueError("TeamManager requires RuntimeScopeKey.session_id")
        return scope.session_key()

    def has_stream_task(self, scope: RuntimeScopeKey) -> bool:
        return self._require_session_scope(scope) in self._stream_tasks

    def pop_stream_task(self, scope: RuntimeScopeKey) -> asyncio.Task | None:
        return self._stream_tasks.pop(self._require_session_scope(scope), None)

    @staticmethod
    def _load_team_spec(session_id: str) -> TeamAgentSpec:
        return TeamAgentSpec.model_validate(load_team_spec_dict(session_id))

    @staticmethod
    async def _cleanup_team_runtime_state(
        spec: TeamAgentSpec,
    ) -> tuple[list[str], list[str]]:
        from openjiuwen.agent_teams.paths import get_agent_teams_home
        from openjiuwen.agent_teams.spawn.shared_resources import get_shared_db
        from openjiuwen.agent_teams.tools.database import DatabaseConfig

        db_config = spec.storage.build() if spec.storage else DatabaseConfig()
        if db_config.db_type == "sqlite" and not db_config.connection_string:
            db_config.connection_string = str(get_agent_teams_home() / "team.db")
        try:
            shared_db = get_shared_db(db_config)
            return await shared_db.cleanup_all_runtime_state()
        except Exception as exc:
            logger.warning("[TeamManager] runtime cleanup failed for team=%s: %s", spec.team_name, exc)
            return [], []

    @staticmethod
    def register_member_runtime_tools(
        agent: DeepAgent,
        *,
        session_id: str,
        request_id: str | None,
        channel_id: str | None,
        request_metadata: dict[str, Any] | None,
        service_id: str | None = None,
        tenant_agent_id: str | None = None,
    ) -> None:
        from openjiuwen.core.runner import Runner
        from jiuwenclaw.config import get_config
        from jiuwenclaw.agentserver.cron_config import should_register_cron_tools
        from jiuwenclaw.agentserver.deep_agent.cron_runtime import CronRuntimeBridge
        from jiuwenclaw.agentserver.tools.send_file_to_user import SendFileToolkit

        member_agent_id = getattr(getattr(agent, "card", None), "id", None)
        if should_register_cron_tools():
            cron_runtime = CronRuntimeBridge()
            cron_metadata = dict(request_metadata) if isinstance(request_metadata, dict) else {}
            resolved_service_id = service_id or "default"
            resolved_tenant_agent_id = tenant_agent_id or "default"
            cron_metadata.setdefault("service_id", resolved_service_id)
            cron_metadata.setdefault("agent_id", resolved_tenant_agent_id)
            cron_context = SimpleNamespace(
                tool_scope=f"team_member_{member_agent_id or 'unknown'}",
                channel_id=channel_id or "web",
                session_id=session_id,
                metadata=cron_metadata,
                mode="team",
            )

            try:
                cron_tools = cron_runtime.build_tools(
                    context=cron_context,
                    agent_id=member_agent_id,
                    service_id=resolved_service_id,
                    tenant_agent_id=resolved_tenant_agent_id,
                )
                for cron_tool in cron_tools:
                    if not Runner.resource_mgr.get_tool(cron_tool.card.id):
                        Runner.resource_mgr.add_tool(cron_tool)
                    agent.ability_manager.add(cron_tool.card)
                logger.info(
                    "[TeamManager] Registered %d cron tools for member agent=%s",
                    len(cron_tools),
                    member_agent_id,
                )
            except Exception as exc:
                logger.warning(
                    "[TeamManager] cron tool registration failed for member agent=%s: %s",
                    member_agent_id,
                    exc,
                )
        else:
            logger.info(
                "[TeamManager] skip cron tool registration for member agent=%s: disabled by env",
                member_agent_id,
            )
        # 设置 DeepResearch 路由上下文（Team 模式）
        dr_token = push_deepresearch_route(
            request_id=request_id or "",
            channel_id=channel_id or "web",
            session_id=session_id,
            service_id=service_id or "default",
            agent_id=tenant_agent_id or "default",
        )

        # 整个方法体放在 try-finally 中，确保路由上下文在退出时重置
        try:
            # SendFileToolkit 注册（仅在 request_id 和 channel_id 有效时）
            if request_id and channel_id:
                try:
                    config = get_config()
                    send_file_enabled = (
                        config.get("channels", {})
                        .get(str(channel_id), {})
                        .get("send_file_allowed", False)
                    )
                    if send_file_enabled:
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
                    else:
                        logger.info(
                            "[TeamManager] SendFileToolkit skipped: send_file_allowed=False for channel=%s",
                            channel_id,
                        )
                except Exception as exc:
                    logger.warning("[TeamManager] SendFileToolkit registration failed: %s", exc)
            else:
                logger.info("[TeamManager] SendFileToolkit skipped: missing request_id or channel_id")

        finally:
            # 重置 DeepResearch 路由上下文
            reset_deepresearch_route(dr_token)

    @staticmethod
    def build_agent_customizer(
        spec: TeamAgentSpec,
        deep_agent: DeepAgent,
        session_id: str,
        *,
        request_id: str | None = None,
        channel_id: str | None = None,
        request_metadata: dict[str, Any] | None = None,
        runtime_scope: RuntimeScopeKey | None = None,
    ) -> Callable[..., None]:
        from jiuwenclaw.agentserver.deep_agent.rails.team_member_skill_toolkit_rail import (
            MemberSkillToolkitRail,
        )
        from jiuwenclaw.agentserver.skill_manager import SkillManager
        from jiuwenclaw.agentserver.extensions.rail_manager import get_rail_manager
        from jiuwenclaw.utils import get_multi_tenant_skill_dirs

        if runtime_scope is not None:
            rail_scope = runtime_scope
        else:
            sid = getattr(deep_agent, "_env_service_id", None) or getattr(
                deep_agent, "_service_id", None
            )
            aid = getattr(deep_agent, "_env_agent_id", None) or getattr(
                deep_agent, "_agent_id", None
            )
            if sid is None or aid is None:
                raise ValueError(
                    "build_agent_customizer requires runtime_scope "
                    "or deep_agent tenant ids (_env_service_id/_env_agent_id)"
                )
            rail_scope = RuntimeScopeKey.from_ids(sid, aid, session_id)
        global_skills_dir = get_multi_tenant_skill_dirs(
            rail_scope.service_id, rail_scope.agent_id
        )[0]
        global_skills_state_path = global_skills_dir / "skills_state.json"
        resolved_channel = channel_id or "default"
        resolved_model_name = get_default_model_name()

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

            try:
                from jiuwenclaw.agentserver.team.member_subagents import assign_team_member_subagents

                assign_team_member_subagents(deep_agent, agent)
            except Exception as exc:
                logger.warning("[TeamManager] assign_team_member_subagents failed: %s", exc)

            inheritable_cards = filter_inheritable_ability_cards(
                deep_agent,
                exclude_tool_names=frozenset({"task_tool"}),
            )
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
            if member_workspace and member_workspace.root_path:
                member_skills_dir = Path(member_workspace.root_path) / "skills"
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

                try:
                    from jiuwenclaw.config import get_config as _get_cfg
                    _react = ((_get_cfg() or {}).get("react") or {})
                    _ce_enabled = bool(
                        (_react.get("context_engine_config") or {}).get("enabled", True)
                    )
                except Exception:
                    _react = {}
                    _ce_enabled = True

                try:
                    member_rails = build_member_rails(
                        MemberRailConfig(
                            skills_dir=str(member_skills_dir),
                            language="cn",
                            channel=resolved_channel,
                            agent_name=getattr(agent.card, "name", "team_member"),
                            model_name=resolved_model_name,
                            session_id=session_id,
                            member_id=getattr(agent.card, "id", None),
                            context_engine_enabled=_ce_enabled,
                            react_config=_react,
                            service_id=rail_scope.service_id,
                            agent_id=rail_scope.agent_id,
                        )
                    )
                    logger.info(
                        "[TeamManager] member context_engine_config.enabled=%s (member_id=%s session_id=%s)",
                        _ce_enabled,
                        getattr(agent.card, "id", None),
                        session_id,
                    )
                    for rail in member_rails:
                        if type(rail).__name__ in RAIL_WHITELIST:
                            agent.add_rail(rail)
                        else:
                            logger.debug("[TeamManager] Skipping non-whitelisted rail: %s", type(rail).__name__)
                    logger.info("[TeamManager] Added %d rails for team member", len(member_rails))
                except Exception as exc:
                    logger.warning("[TeamManager] build_member_rails failed: %s", exc)

            rail_manager = get_rail_manager(rail_scope)
            for rail_name in rail_manager.get_registered_rail_names():
                try:
                    # Team members must not share the main-agent cached rail instance.
                    rail_instance = rail_manager.create_fresh_rail_instance(rail_name)
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
                service_id=rail_scope.service_id,
                tenant_agent_id=rail_scope.agent_id,
            )

        return customizer


    @staticmethod
    def _copy_global_skills_to_team_shared_dir(
        spec: TeamAgentSpec,
        *,
        service_id: str,
        agent_id: str,
    ) -> None:
        """Copy global skills to team shared directory (executed once after team build)."""
        from jiuwenclaw.utils import get_multi_tenant_skill_dirs

        global_skills_dir = get_multi_tenant_skill_dirs(service_id, agent_id)[0]
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

    async def create_team(
        self,
        scope: RuntimeScopeKey,
        deep_agent: DeepAgent,
        request_id: str | None = None,
        channel_id: str | None = None,
        request_metadata: dict[str, Any] | None = None,
    ) -> TeamAgent:
        key = self._require_session_scope(scope)
        session_id = scope.session_id
        logger.info(
            "[TeamManager] building TeamAgentSpec: scope=%s session_id=%s",
            scope.tenant(),
            session_id,
        )
        spec = self._load_team_spec(session_id)
        deleted_tables, cleared_tables = await self._cleanup_team_runtime_state(spec)
        if deleted_tables or cleared_tables:
            logger.info(
                "[TeamManager] pre-create cleanup deleted dynamic tables=%s cleared static tables=%s",
                deleted_tables,
                cleared_tables,
            )

        spec.agent_customizer = self.build_agent_customizer(
            spec,
            deep_agent,
            session_id,
            request_id=request_id,
            channel_id=channel_id,
            request_metadata=request_metadata,
            runtime_scope=scope,
        )

        logger.info("[TeamManager] TeamAgentSpec ready: team_name=%s", spec.team_name)

        token = set_session_id(session_id)
        try:
            logger.info("[TeamManager] creating TeamAgent from spec")
            team_agent = spec.build()
            self._team_agents[key] = team_agent

            # After build, copy global skills to team shared directory (only once)
            self._copy_global_skills_to_team_shared_dir(
                spec,
                service_id=scope.service_id,
                agent_id=scope.agent_id,
            )

            logger.info(
                "[TeamManager] Team created: scope=%s session_id=%s, team_name=%s",
                scope.tenant(),
                session_id,
                spec.team_name,
            )
            return team_agent
        finally:
            reset_session_id(token)

    async def get_or_create_team(
        self,
        scope: RuntimeScopeKey,
        deep_agent: DeepAgent,
        request_id: str | None = None,
        channel_id: str | None = None,
        request_metadata: dict[str, Any] | None = None,
    ) -> TeamAgent:
        key = self._require_session_scope(scope)
        async with self._lock:
            team_agent = self._team_agents.get(key)
            if team_agent is not None:
                return team_agent

            # 2b: destroy other sessions of the same tenant only
            await self._destroy_other_sessions_in_tenant(scope)
            return await self.create_team(
                scope,
                deep_agent,
                request_id,
                channel_id,
                request_metadata,
            )

    async def interact(self, scope: RuntimeScopeKey, user_input: str) -> bool:
        key = self._require_session_scope(scope)
        team_agent = self._team_agents.get(key)
        if team_agent is None:
            logger.warning(
                "[TeamManager] interact failed, missing team: scope=%s session_id=%s",
                scope.tenant(),
                scope.session_id,
            )
            return False

        try:
            await team_agent.interact(user_input)
            logger.debug(
                "[TeamManager] interact sent: scope=%s session_id=%s",
                scope.tenant(),
                scope.session_id,
            )
            return True
        except Exception as exc:
            logger.error(
                "[TeamManager] interact failed: scope=%s session_id=%s, error=%s",
                scope.tenant(),
                scope.session_id,
                exc,
            )
            return False

    async def destroy_team(self, scope: RuntimeScopeKey) -> bool:
        async with self._lock:
            return await self._destroy_team(self._require_session_scope(scope))

    async def _destroy_other_sessions_in_tenant(self, current: RuntimeScopeKey) -> None:
        """Destroy other sessions under the same (service_id, agent_id) only."""
        tenant = current.tenant()
        current_key = self._require_session_scope(current)
        stale_keys = [
            key
            for key in list(self._team_agents.keys())
            if key[:2] == tenant and key != current_key
        ]
        for stale_key in stale_keys:
            await self._destroy_team(stale_key)

    async def _destroy_team(self, key: _TeamSessionKey) -> bool:
        session_id = key[2]
        stream_task = self._stream_tasks.pop(key, None)
        if stream_task and not stream_task.done():
            stream_task.cancel()
            try:
                await stream_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning(
                    "[TeamManager] stream stop failed: key=%s error=%s",
                    key,
                    exc,
                )

        monitor_handler = self._team_monitors.pop(key, None)
        if monitor_handler is not None:
            try:
                await monitor_handler.stop()
            except Exception as exc:
                logger.warning(
                    "[TeamManager] monitor stop failed: key=%s error=%s",
                    key,
                    exc,
                )

        team_agent = self._team_agents.pop(key, None)
        cleaned = False
        cleanup_spec: TeamAgentSpec | None = None
        try:
            cleanup_spec = self._load_team_spec(session_id)
            if team_agent is None:
                logger.info(
                    "[TeamManager] no in-memory team for key=%s, run runtime cleanup fallback only",
                    key,
                )
                return False

            token = set_session_id(session_id)
            try:
                cleaned = await team_agent.destroy_team(force=True)
            finally:
                reset_session_id(token)

            logger.info(
                "[TeamManager] Team cleaned via core API: key=%s cleaned=%s",
                key,
                cleaned,
            )
        except Exception as exc:
            logger.error(
                "[TeamManager] destroy team failed: key=%s error=%s",
                key,
                exc,
            )
        finally:
            if cleanup_spec is None:
                try:
                    cleanup_spec = self._load_team_spec(session_id)
                except Exception as exc:
                    logger.warning(
                        "[TeamManager] failed to rebuild team spec for cleanup: key=%s error=%s",
                        key,
                        exc,
                    )
                    cleanup_spec = None
            deleted_tables: list[str] = []
            cleared_tables: list[str] = []
            if cleanup_spec is not None:
                deleted_tables, cleared_tables = await self._cleanup_team_runtime_state(cleanup_spec)
            if deleted_tables or cleared_tables:
                logger.info(
                    "[TeamManager] fallback cleanup after destroy deleted dynamic tables=%s "
                    "cleared static tables=%s",
                    deleted_tables,
                    cleared_tables,
                )

        return cleaned

    async def cleanup_all(self) -> None:
        async with self._lock:
            keys = list(self._team_agents.keys())
            for key in keys:
                await self._destroy_team(key)
            logger.info("[TeamManager] all teams cleaned")

    def get_team_agent(self, scope: RuntimeScopeKey) -> TeamAgent | None:
        return self._team_agents.get(self._require_session_scope(scope))

    def register_monitor(self, scope: RuntimeScopeKey, handler: TeamMonitorHandler) -> None:
        self._team_monitors[self._require_session_scope(scope)] = handler

    def register_stream_task(self, scope: RuntimeScopeKey, task: asyncio.Task) -> None:
        self._stream_tasks[self._require_session_scope(scope)] = task

    async def cancel_all_stream_tasks(self, reason: str = "") -> None:
        """Gateway 与 AgentServer 断开时取消 Team 后台 stream 协程（含 create_task 绕开 SessionManager 的任务）。"""
        async with self._lock:
            pending = list(self._stream_tasks.items())
        for key, task in pending:
            if task.done():
                continue
            logger.info(
                "[TeamManager] %scancel stream task key=%s",
                reason,
                key,
            )
            task.cancel()
        for key, task in pending:
            if task.done():
                continue
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning(
                    "[TeamManager] stream task await after cancel failed key=%s: %s",
                    key,
                    exc,
                )
        async with self._lock:
            self._stream_tasks.clear()


_team_manager: TeamManager | None = None


def get_team_manager() -> TeamManager:
    global _team_manager
    if _team_manager is None:
        _team_manager = TeamManager()
    return _team_manager


def reset_team_manager() -> None:
    global _team_manager
    _team_manager = None
