# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Swarm team-spec enrichment entry point."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from openjiuwen.agent_teams.paths import team_home
from openjiuwen.agent_teams.schema.deep_agent_spec import WorkspaceSpec

from jiuwenclaw.agentserver.swarm.config_specs import build_member_deep_agent_spec
from jiuwenclaw.agentserver.swarm.context import SwarmBuildContext
from jiuwenclaw.agentserver.swarm.registry import register_swarm_providers
from jiuwenclaw.config import get_config
from jiuwenclaw.utils import get_agent_skills_dir

logger = logging.getLogger(__name__)

# Role-template keys always enriched. Named predefined members (e.g.
# ``product-architect``) are enriched too — see ``_iter_enrich_agent_keys``.
_MEMBER_ROLE_TEMPLATES: tuple[str, ...] = ("leader", "teammate")
# Avatar / special roles keep their own harness path; do not fold platform
# member rails onto them via the teammate capability set.
_SKIP_PLATFORM_ENRICH_KEYS: frozenset[str] = frozenset({"human_agent", "bridge_agent"})


def _ensure_member_role_templates(spec: Any) -> None:
    """Guarantee ``agents.leader`` / ``agents.teammate`` exist before enrich.

    OfficeClaw presets and some resume paths may only carry a leader template
    (or non-role keys). A missing ``teammate`` key would leave dynamic spawns
    without the shared member platform rails/tools.
    """
    agents = getattr(spec, "agents", None)
    if not isinstance(agents, dict):
        return
    try:
        from openjiuwen.agent_teams.schema.blueprint import DeepAgentSpec
    except ImportError:  # pragma: no cover - schema path variants
        from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec

    if "leader" not in agents:
        agents["leader"] = DeepAgentSpec()
        logger.info(
            "[swarm.assembly] agents missing leader; added default DeepAgentSpec template"
        )
    if "teammate" not in agents:
        agents["teammate"] = DeepAgentSpec()
        logger.info(
            "[swarm.assembly] agents missing teammate; added default DeepAgentSpec template"
        )


def _iter_enrich_agent_keys(agents: dict[str, Any]) -> list[str]:
    """Keys that receive shared member platform rails + catalog tools.

    - ``leader``: leader-only rails decide at provider time via ``context.role``
    - ``teammate``: role template for dynamic spawns (own skills only)
    - named predefined members: same common rails; skills stay per-key
    """
    keys: list[str] = []
    for key in agents:
        key_s = str(key or "").strip()
        if not key_s or key_s in _SKIP_PLATFORM_ENRICH_KEYS:
            continue
        keys.append(key_s)
    # Stable order: templates first, then the rest sorted.
    head = [k for k in _MEMBER_ROLE_TEMPLATES if k in keys]
    tail = sorted(k for k in keys if k not in _MEMBER_ROLE_TEMPLATES)
    return head + tail


def _capability_role_for_agent_key(agent_key: str) -> str:
    """Map agents-dict key → capability role used while folding RailSpecs."""
    return "leader" if agent_key == "leader" else "teammate"


def _with_project_workspace(member_spec: Any, project_dir: str | None) -> Any:
    project_root = str(project_dir or "").strip()
    if not project_root:
        return member_spec

    workspace = getattr(member_spec, "workspace", None)
    if workspace is not None and str(getattr(workspace, "root_path", "") or "").strip() not in {"", "./"}:
        return member_spec

    if workspace is None:
        workspace = WorkspaceSpec(root_path=project_root)
    else:
        workspace = workspace.model_copy(update={"root_path": project_root})
    return member_spec.model_copy(update={"workspace": workspace})


def _worktree_enabled(spec: Any) -> bool:
    worktree = getattr(spec, "worktree", None)
    return bool(worktree is not None and getattr(worktree, "enabled", False))


def _leader_member_name(spec: Any) -> str:
    leader = getattr(spec, "leader", None)
    if leader is None:
        return ""
    return str(getattr(leader, "member_name", "") or "")


def enrich_team_spec_for_swarm(
    spec: Any,
    *,
    session_id: str,
    mode: str,
    project_dir: str | None = None,
    request_id: str | None = None,
    channel_id: str | None = None,
    request_metadata: dict[str, Any] | None = None,
) -> None:
    """Enrich *spec* in place for provider-based swarm assembly."""
    register_swarm_providers()
    _ensure_member_role_templates(spec)

    config = get_config()
    # When plan/project root is bound, bake shared workspace under
    # ``{project}/.agent_teams/{team}/team-workspace`` (caller should enter
    # ``agent_teams_home_scope`` so ``team_home`` resolves there).
    project_root = str(project_dir or "").strip()
    if project_root:
        desired_ws = str(team_home(spec.team_name) / "team-workspace")
        workspace = spec.workspace
        if workspace is None:
            spec.workspace = WorkspaceSpec(enabled=True, root_path=desired_ws)
        elif not str(getattr(workspace, "root_path", "") or "").strip():
            spec.workspace = workspace.model_copy(update={"root_path": desired_ws})

    workspace = spec.workspace
    team_ws_root = (
        workspace.root_path
        if workspace and workspace.root_path
        else str(team_home(spec.team_name) / "team-workspace")
    )
    team_skills_dir = str(Path(team_ws_root) / "skills")
    leader_skills_dir = str(Path(team_ws_root) / "leader-skills")
    global_skills_dir = str(get_agent_skills_dir())

    meta = request_metadata if isinstance(request_metadata, dict) else {}
    # chat.send ``team_name`` is the modes.team template key; ``spec.team_name``
    # may already be session-scoped (``{template}_{session_id}``).
    team_template_id = str(meta.get("team_name") or "").strip()
    if not team_template_id:
        scoped = str(spec.team_name or "").strip()
        sid = str(session_id or "").strip()
        suffix = f"_{sid}" if sid else ""
        if sid and scoped.endswith(suffix) and len(scoped) > len(suffix):
            team_template_id = scoped[: -len(suffix)]
        else:
            team_template_id = scoped

    logger.info(
        "[swarm.assembly] team context team_id=%s team_template_id=%s session=%s",
        spec.team_name,
        team_template_id,
        session_id,
    )

    agent_skills_by_key: dict[str, list[str]] = {}
    for key, agent in (getattr(spec, "agents", None) or {}).items():
        raw_skills = getattr(agent, "skills", None)
        if raw_skills is None and isinstance(agent, dict):
            raw_skills = agent.get("skills")
        if not isinstance(raw_skills, list):
            continue
        names = [str(s).strip() for s in raw_skills if str(s).strip()]
        if names:
            agent_skills_by_key[str(key)] = names

    base = SwarmBuildContext(
        session_id=session_id,
        request_id=request_id,
        channel_id=channel_id,
        channel=channel_id or "default",
        request_metadata=request_metadata,
        mode=mode,
        project_dir=project_dir,
        team_id=spec.team_name,
        team_template_id=team_template_id,
        agent_skills_by_key=agent_skills_by_key or None,
        team_ws_root=team_ws_root,
        team_skills_dir=team_skills_dir,
        leader_skills_dir=leader_skills_dir,
        global_skills_dir=global_skills_dir,
        trajectory_registry=None,
        config=config,
    )
    # MCP provider assembly deferred; catalog tools mount via BuiltinToolSpec.
    mcp_configs: list[Any] = []
    leader_name = _leader_member_name(spec)
    agents_map = getattr(spec, "agents", None) or {}
    enrich_keys = _iter_enrich_agent_keys(agents_map if isinstance(agents_map, dict) else {})

    for agent_key in enrich_keys:
        base_agent = agents_map.get(agent_key)
        if base_agent is None:
            continue
        capability_role = _capability_role_for_agent_key(agent_key)
        member_spec = build_member_deep_agent_spec(
            config,
            mode,
            capability_role,
            base_agent,
            enable_permissions=bool(getattr(spec, "enable_permissions", False)),
            mcp_configs=mcp_configs,
            leader_member_name=leader_name,
        )
        if _worktree_enabled(spec):
            member_spec = _with_project_workspace(member_spec, project_dir)
        spec.agents[agent_key] = member_spec

    spec.build_context = base
    spec.build_context_seed = base.to_seed()
    logger.info(
        "[swarm.assembly] enriched team spec '%s' (agent_keys=%s, session=%s, mcps=%d)",
        spec.team_name,
        enrich_keys,
        session_id,
        len(mcp_configs),
    )


__all__ = ["enrich_team_spec_for_swarm"]
