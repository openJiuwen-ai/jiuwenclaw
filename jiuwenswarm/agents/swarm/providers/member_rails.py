# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Swarm member rail providers (config-sourced, per-member).

Each provider is a factory ``factory(params, context) -> rail | list | None``
invoked by openjiuwen at build time with the per-member ``SwarmBuildContext``.
Returning ``None`` / ``[]`` means "skip this rail for this member" (config gate).
Providers take precedence over same-named class registrations.

Mirrors the legacy ``build_member_rails`` runtime-prompt / report-path /
context-processor segments and the team manager plugin-rails segment, but driven
by the build context instead of imperatively threaded dataclasses.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from openjiuwen.agent_teams.harness.manifest import (
    ConstructionInput,
    context_field,
    ElementKind,
    harness_element,
    param_field,
)

from jiuwenswarm.agents.harness.common.plugins.rail_manager import get_rail_manager
from jiuwenswarm.agents.harness.common.rails.runtime_prompt_rail import (
    RuntimePromptRail,
)
from jiuwenswarm.agents.harness.team.rails.team_skill_storage_policy_rail import (
    TeamSkillStoragePolicyRail,
)
from jiuwenswarm.agents.harness.team.rails.team_shared_skill_link_refresh_rail import (
    TeamSharedSkillLinkRefreshRail,
)
from jiuwenswarm.agents.harness.team.rails.team_workspace_report_path_rail import (
    TeamWorkspaceReportPathRail,
)
from jiuwenswarm.agents.harness.team.team_runtime_inheritance import (
    _build_context_processor_rail,
)
from jiuwenswarm.agents.swarm.context import SwarmBuildContext

logger = logging.getLogger(__name__)

RUNTIME_PROMPT = "swarm.runtime_prompt"
TEAM_SKILL_STORAGE_POLICY = "swarm.team_skill_storage_policy"
TEAM_SHARED_SKILL_LINK_REFRESH = "swarm.team_shared_skill_link_refresh"
TEAM_WORKSPACE_REPORT_PATH = "swarm.team_workspace_report_path"
CONTEXT_PROCESSOR = "swarm.context_processor"
PLUGIN_RAILS = "swarm.plugin_rails"


def _workspace_root(ctx: SwarmBuildContext) -> str | None:
    """Resolve the member workspace root path."""
    workspace = getattr(ctx, "workspace", None)
    return getattr(workspace, "root_path", None) if workspace else None


class RuntimePromptInput(ConstructionInput):
    """Construction inputs for the member runtime prompt rail."""

    language: str = context_field(
        attr="language",
        default="cn",
        description="Resolved member language code.",
    )
    channel: str = context_field(
        attr="channel",
        default="default",
        description="Resolved channel key.",
    )


@harness_element(
    kind=ElementKind.RAIL,
    name=RUNTIME_PROMPT,
    description="Per-member runtime prompt rail bound to the member's language and channel.",
    input_model=RuntimePromptInput,
)
def _build_runtime_prompt_rail(
    params: dict[str, Any],
    context: SwarmBuildContext,
) -> RuntimePromptRail:
    """Build the runtime prompt rail for a member.

    Args:
        params: Spec params (unused; kept for the provider contract).
        context: Per-member build context.

    Returns:
        A ``RuntimePromptRail`` bound to the member's language and channel.
    """
    inp = RuntimePromptInput.resolve(params, context)
    return RuntimePromptRail(language=inp.language, channel=inp.channel)


class TeamSkillStoragePolicyInput(ConstructionInput):
    """Construction inputs for the team skill storage policy rail."""

    global_skills_dir: str | None = context_field(
        attr="global_skills_dir",
        description="Global shared skills source directory.",
    )
    team_ws_root: str | None = context_field(
        attr="team_ws_root",
        description="Team shared workspace root.",
    )
    team_skills_dir: str | None = context_field(
        attr="team_skills_dir",
        description="Team shared skills linked view.",
    )
    member_workspace_root: str | None = context_field(
        resolver=_workspace_root,
        description="Current member workspace root.",
    )


@harness_element(
    kind=ElementKind.RAIL,
    name=TEAM_SKILL_STORAGE_POLICY,
    description="Team-only policy that stores all skill authoring outputs in "
    "the global shared skills source directory.",
    input_model=TeamSkillStoragePolicyInput,
)
def _build_team_skill_storage_policy_rail(
    params: dict[str, Any],
    context: SwarmBuildContext,
) -> TeamSkillStoragePolicyRail | None:
    """Build the team skill storage policy rail when the global skill root exists.

    Args:
        params: Spec params (unused; kept for the provider contract).
        context: Per-member build context.

    Returns:
        A ``TeamSkillStoragePolicyRail`` or ``None`` when no global skills
        directory is available.
    """
    inp = TeamSkillStoragePolicyInput.resolve(params, context)
    if not inp.global_skills_dir:
        return None
    return TeamSkillStoragePolicyRail(
        global_skills_dir=inp.global_skills_dir,
        team_workspace_root=inp.team_ws_root,
        team_skills_dir=inp.team_skills_dir,
        member_workspace_root=inp.member_workspace_root,
    )


class TeamSharedSkillLinkRefreshInput(ConstructionInput):
    """Construction inputs for refreshing team shared skill links."""

    global_skills_dir: str | None = context_field(
        attr="global_skills_dir",
        description="Global shared skills source directory.",
    )
    session_id: str = context_field(
        attr="session_id",
        default="",
        description="Active session id.",
    )
    channel: str = context_field(
        attr="channel",
        default="default",
        description="Resolved channel key for the per-channel team manager.",
    )


@harness_element(
    kind=ElementKind.RAIL,
    name=TEAM_SHARED_SKILL_LINK_REFRESH,
    description="Refresh team shared skill links after tools write into the "
    "global shared skills source directory.",
    input_model=TeamSharedSkillLinkRefreshInput,
)
def _build_team_shared_skill_link_refresh_rail(
    params: dict[str, Any],
    context: SwarmBuildContext,
) -> TeamSharedSkillLinkRefreshRail | None:
    """Build the rail that refreshes team shared skill links after writes.

    Args:
        params: Spec params (unused; kept for the provider contract).
        context: Per-member build context.

    Returns:
        A ``TeamSharedSkillLinkRefreshRail`` or ``None`` when required runtime
        context is missing.
    """
    inp = TeamSharedSkillLinkRefreshInput.resolve(params, context)
    if not inp.global_skills_dir or not inp.session_id:
        return None

    def refresh_links() -> None:
        """Refresh the current team's shared skill link view."""
        from jiuwenswarm.agents.harness.team.team_manager import get_team_manager

        get_team_manager(inp.channel).refresh_team_shared_skill_links(inp.session_id)

    return TeamSharedSkillLinkRefreshRail(
        global_skills_dir=Path(inp.global_skills_dir),
        refresh_links=refresh_links,
    )


class TeamWorkspaceReportPathInput(ConstructionInput):
    """Construction inputs for the team workspace report-path rail."""

    team_ws_root: str | None = context_field(
        attr="team_ws_root",
        description="Team shared workspace root path (gate; skipped when absent).",
    )
    team_id: str = context_field(attr="team_id", default="", description="Team name.")
    language: str = context_field(
        attr="language",
        default="cn",
        description="Resolved member language code.",
    )


@harness_element(
    kind=ElementKind.RAIL,
    name=TEAM_WORKSPACE_REPORT_PATH,
    description="Rewrites report paths under the shared team workspace root "
    "(skipped when no shared root is configured).",
    input_model=TeamWorkspaceReportPathInput,
)
def _build_team_workspace_report_path_rail(
    params: dict[str, Any],
    context: SwarmBuildContext,
) -> TeamWorkspaceReportPathRail | None:
    """Build the team workspace report-path rail when a shared root exists.

    Args:
        params: Spec params (unused; kept for the provider contract).
        context: Per-member build context.

    Returns:
        A ``TeamWorkspaceReportPathRail`` rooted at the team workspace, or
        ``None`` when no shared workspace root is configured.
    """
    inp = TeamWorkspaceReportPathInput.resolve(params, context)
    if not inp.team_ws_root:
        return None
    return TeamWorkspaceReportPathRail(
        root_dir=inp.team_ws_root,
        team_id=inp.team_id,
        language=inp.language,
    )


class ContextProcessorInput(ConstructionInput):
    """Construction inputs for the context-compression rail."""

    context_engine_enabled: bool = param_field(
        default=True,
        description="Whether the context engine is enabled in config (gate).",
    )
    context_engine_config: dict[str, Any] = param_field(
        default_factory=dict,
        description="Context-engine config (compressor sub-configs).",
    )


@harness_element(
    kind=ElementKind.RAIL,
    name=CONTEXT_PROCESSOR,
    description="Context-compression rail, mounted only when the context engine "
    "is enabled in config.",
    input_model=ContextProcessorInput,
)
def _build_context_processor(
    params: dict[str, Any],
    context: SwarmBuildContext,
) -> Any | None:
    """Build the context-compression rail when the context engine is enabled.

    Args:
        params: Spec params (unused; kept for the provider contract).
        context: Per-member build context.

    Returns:
        A preset ``ContextProcessorRail`` when enabled, otherwise ``None``.
    """
    inp = ContextProcessorInput.resolve(params, context)
    if not inp.context_engine_enabled:
        return None
    return _build_context_processor_rail(
        {"context_engine_config": inp.context_engine_config}
    )


@harness_element(
    kind=ElementKind.RAIL,
    name=PLUGIN_RAILS,
    description="User-registered extension rails: a fresh instance of every "
    "registered rail extension, one per member.",
)
def _build_plugin_rails(
    params: dict[str, Any],
    context: SwarmBuildContext,
) -> list[Any]:
    """Build user-registered extension rails for a member.

    Enumerates every registered rail extension and instantiates a fresh
    instance per member, skipping any that fail to load.

    Args:
        params: Spec params (unused; kept for the provider contract).
        context: Per-member build context.

    Returns:
        A list of extension rail instances (possibly empty).
    """
    rail_manager = get_rail_manager()
    rails: list[Any] = []
    for rail_name in rail_manager.get_registered_rail_names():
        try:
            rail_instance = rail_manager.load_rail_instance_without_enabled_check(
                rail_name,
            )
            if rail_instance is not None:
                rails.append(rail_instance)
        except Exception as exc:
            logger.warning(
                "[SwarmRails] load extension rail %s failed: %s",
                rail_name,
                exc,
            )
    return rails


__all__ = [
    "RUNTIME_PROMPT",
    "TEAM_SKILL_STORAGE_POLICY",
    "TEAM_SHARED_SKILL_LINK_REFRESH",
    "TEAM_WORKSPACE_REPORT_PATH",
    "CONTEXT_PROCESSOR",
    "PLUGIN_RAILS",
]
