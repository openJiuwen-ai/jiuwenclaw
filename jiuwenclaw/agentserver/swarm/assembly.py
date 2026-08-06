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

_MEMBER_ROLES: tuple[str, ...] = ("leader", "teammate")


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

    base = SwarmBuildContext(
        session_id=session_id,
        request_id=request_id,
        channel_id=channel_id,
        channel=channel_id or "default",
        request_metadata=request_metadata,
        mode=mode,
        project_dir=project_dir,
        team_id=spec.team_name,
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

    for role in _MEMBER_ROLES:
        if role in spec.agents:
            member_spec = build_member_deep_agent_spec(
                config,
                mode,
                role,
                spec.agents[role],
                enable_permissions=bool(getattr(spec, "enable_permissions", False)),
                mcp_configs=mcp_configs,
                leader_member_name=leader_name,
            )
            if _worktree_enabled(spec):
                member_spec = _with_project_workspace(member_spec, project_dir)
            spec.agents[role] = member_spec

    spec.build_context = base
    spec.build_context_seed = base.to_seed()
    logger.info(
        "[swarm.assembly] enriched team spec '%s' (roles=%s, session=%s, mcps=%d)",
        spec.team_name,
        [role for role in _MEMBER_ROLES if role in spec.agents],
        session_id,
        len(mcp_configs),
    )


__all__ = ["enrich_team_spec_for_swarm"]
