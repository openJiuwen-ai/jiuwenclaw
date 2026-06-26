# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Swarm team-spec enrichment entry point.

``enrich_team_spec_for_swarm`` is the single seam between the platform and the
provider-based assembly. Given a ``TeamAgentSpec`` it:

* registers all swarm providers / rail types (idempotent),
* builds the per-team base :class:`SwarmBuildContext` carrying the live runtime
  handles every provider needs,
* rewrites each present member spec ("leader" / "teammate") with its
  config-sourced rails and tools, and
* attaches the base context to ``spec.build_context`` so openjiuwen's
  ``setup_agent`` derives a per-member view through ``derive()``.

It never receives or inspects a pre-built ``DeepAgent``: members are assembled
purely from the config source plus provider name references.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from openjiuwen.agent_evolving.trajectory import InMemoryTrajectoryRegistry
from openjiuwen.agent_teams.paths import team_home
from openjiuwen.agent_teams.schema.deep_agent_spec import WorkspaceSpec

from jiuwenswarm.agents.swarm.config_specs import build_member_deep_agent_spec
from jiuwenswarm.agents.swarm.context import SwarmBuildContext
from jiuwenswarm.agents.swarm.registry import register_swarm_providers
from jiuwenswarm.common.config import get_config
from jiuwenswarm.common.mcp_config import build_enabled_mcp_server_configs
from jiuwenswarm.common.utils import get_agent_skills_dir

logger = logging.getLogger(__name__)

# Member roles enriched in place, in deterministic order.
_MEMBER_ROLES: tuple[str, ...] = ("leader", "teammate")


def _with_project_workspace(member_spec: Any, project_dir: str | None) -> Any:
    """Default a member workspace to the request project directory."""
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
    """Return whether the team spec requested managed worktree isolation."""
    worktree = getattr(spec, "worktree", None)
    return bool(worktree is not None and getattr(worktree, "enabled", False))


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
    """Enrich *spec* in place for provider-based swarm assembly.

    Registers swarm providers, builds the per-team base context, rewrites the
    present member specs with their config-sourced capabilities, and attaches the
    base context to the spec. Modifies *spec* in place and returns nothing.

    Args:
        spec: The ``TeamAgentSpec`` to enrich (mutated in place).
        session_id: Active session id.
        mode: Request mode (e.g. "team").
        project_dir: Resolved project directory, if any.
        request_id: Originating request id, if any.
        channel_id: Raw channel id from the request, if any.
        request_metadata: Request metadata mapping (carries ``mode`` etc.).
    """
    register_swarm_providers()

    config = get_config()
    workspace = spec.workspace
    team_ws_root = (
        workspace.root_path
        if workspace and workspace.root_path
        else str(team_home(spec.team_name) / "team-workspace")
    )
    team_skills_dir = str(Path(team_ws_root) / "skills")
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
        global_skills_dir=global_skills_dir,
        trajectory_registry=InMemoryTrajectoryRegistry(),
        config=config,
    )
    mcp_configs = build_enabled_mcp_server_configs(
        config,
        server_id_scope=f"team:{spec.team_name}",
    )

    for role in _MEMBER_ROLES:
        if role in spec.agents:
            member_spec = build_member_deep_agent_spec(
                config,
                mode,
                role,
                spec.agents[role],
                enable_permissions=spec.enable_permissions,
                mcp_configs=mcp_configs,
            )
            if _worktree_enabled(spec):
                member_spec = _with_project_workspace(member_spec, project_dir)
            spec.agents[role] = member_spec

    spec.build_context = base
    # Carry a serializable seed alongside the live context so members rebuilt
    # across a serialization boundary (spawned teammate, distributed remote,
    # cold recovery) can reconstruct the context via the registered factory.
    spec.build_context_seed = base.to_seed()
    logger.info(
        "[swarm.assembly] enriched team spec '%s' (roles=%s, session=%s, mcps=%d)",
        spec.team_name,
        [role for role in _MEMBER_ROLES if role in spec.agents],
        session_id,
        len(mcp_configs),
    )


__all__ = ["enrich_team_spec_for_swarm"]
