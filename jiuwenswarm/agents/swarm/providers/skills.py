# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Member-skill rail provider for swarm provider-based team assembly.

This module ports the "member skills" branch of the legacy
``team_manager`` customizer into a config-sourced rail provider. The provider
factory links the member's configured skills into the member workspace
``skills`` directory and returns a ``MemberSkillToolkitRail`` bound to the
shared agent workspace, so members share one skill store while each exposes
only its own configured skill view through directory links.

The directory-preparation helpers below are pure functions extracted from the
former customizer closure: the variables the closure captured implicitly
(``global_skills_dir`` / the per-channel team manager) are now explicit
parameters or resolved from the build context.
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

from jiuwenswarm.agents.harness.team.rails.team_member_skill_toolkit_rail import (
    MemberSkillToolkitRail,
)
from jiuwenswarm.agents.harness.team.team_skill_links import (
    is_valid_skill_dir,
    link_skill_dir,
    path_exists_or_link,
    prune_skill_dir_links,
    remove_skill_dir_link,
)
from jiuwenswarm.common.utils import get_agent_workspace_dir, get_agent_skills_dir
from jiuwenswarm.server.runtime.skill.skill_manager import SkillManager

logger = logging.getLogger(__name__)

# Provider name registered for the member-skill toolkit rail.
MEMBER_SKILL_TOOLKIT = "swarm.member_skill_toolkit"

# Provider name for the chat-profile member skill-use rail (Reconciling 版).
TEAM_SKILL_USE = "swarm.team_skill_use"


def _member_workspace_skill_dirs(ctx: Any) -> list[str]:
    """复刻 agent-core ``factory._make_skill_rail`` 的 skills_dir 语义。

    member workspace 的 ``skills`` 节点 + 各 team 共享 workspace 链接下的
    ``skills``（符号链接指向共享 workspace 根）。任一来源缺失即跳过——
    SkillUseRail 对不存在的目录在 refresh 时容错。
    """
    dirs: list[str] = []
    workspace = getattr(ctx, "workspace", None)
    if workspace is None:
        return dirs
    get_node_path = getattr(workspace, "get_node_path", None)
    if callable(get_node_path):
        base = get_node_path("skills")
        if base:
            dirs.append(str(base))
    list_team_links = getattr(workspace, "list_team_links", None)
    if callable(list_team_links):
        for _team_id, target_path in list_team_links():
            dirs.append(str(Path(target_path) / "skills"))
    return dirs


def _collect_disabled_skills(skills_dirs: list[str]) -> list[str]:
    """收集各 skills_dir 的 skills_state.json 里禁用的技能名（同 agent-core factory）。"""
    try:
        from openjiuwen.harness.factory import _collect_disabled_skills_from_state

        return _collect_disabled_skills_from_state(skills_dirs)
    except Exception as exc:
        logger.warning("[swarm.team_skill_use] collect disabled skills failed: %s", exc)
        return []


class TeamSkillUseInput(ConstructionInput):
    """Construction inputs for the chat-profile member skill-use rail."""

    skill_mode: str = param_field(
        default="all",
        description="Skill exposure mode; factory auto-inject 恒为 all,"
        " retrieval 模式由 _normalize_skill_use_rails_for_agentic_retrieval 覆写为 auto_list。",
    )
    include_tools: bool = param_field(
        default=False,
        description="chat profile 恒有 SysOperationRail 持文件工具，skill rail 不再带 fallback 集。",
    )


@harness_element(
    kind=ElementKind.RAIL,
    name=TEAM_SKILL_USE,
    description="Chat-profile member skill-use rail (ReconcilingSkillUseRail): "
                "workspace-derived skills_dir, session-baseline reconcile on hot unmount.",
    input_model=TeamSkillUseInput,
)
def build_team_skill_use(params: dict, ctx: Any) -> object | None:
    """Build the chat-profile member skill-use rail.

    chat profile 此前依赖 agent-core ``enable_skill_discovery`` 自动注入的
    普通 ``SkillUseRail``——session baseline「只建不刷」，团包 skills 热卸后
    旧技能残留系统提示词 ``# 技能`` 段。此处显式声明 ``ReconcilingSkillUseRail``
    （单专家同款 baseline 双向对齐兜底），并经 ``issubclass`` 命中 factory 的
    ``_already_provided(SkillUseRail)`` 短路，抑制自动注入的普通 rail。

    Args:
        params: Provider params（skill_mode / include_tools，均有默认）。
        ctx: The active ``SwarmBuildContext`` for the current member.

    Returns:
        A ``ReconcilingSkillUseRail`` instance, or ``None`` when the member has
        no workspace（无 skills 来源，交给 factory 自动注入兜底）。
    """
    from jiuwenswarm.server.runtime.agent_adapter.skill_rail_reconcile import (
        ReconcilingSkillUseRail,
    )

    inp = TeamSkillUseInput.resolve(params, ctx)
    skills_dirs = _member_workspace_skill_dirs(ctx)
    if not skills_dirs:
        return None
    return ReconcilingSkillUseRail(
        skills_dir=skills_dirs,
        skill_mode=inp.skill_mode,
        include_tools=inp.include_tools,
        disabled_skills=_collect_disabled_skills(skills_dirs) or None,
    )


def _link_member_configured_skills(
    member_skills_dir: Path,
    selected_skills: list[str],
    global_skills_dir: Path,
) -> None:
    """Link the member's configured skills into its own skills directory.

    Synchronizes the member ``skills`` directory so it holds exactly one
    directory link per selected skill, pruning links for skills no longer
    selected. Skills are linked (not copied) so runtime installs/uninstalls in
    the shared store propagate without stale copies.

    Args:
        member_skills_dir: Member workspace ``skills`` directory.
        selected_skills: Skill names selected for this member.
        global_skills_dir: Global agent skills directory to link from.
    """
    if not global_skills_dir.exists():
        logger.warning(
            "[swarm.member_skill_toolkit] global_skills_dir does not exist: %s",
            global_skills_dir,
        )
        return

    selected_skill_set = set(selected_skills)
    member_skills_dir.mkdir(parents=True, exist_ok=True)
    prune_skill_dir_links(global_skills_dir, member_skills_dir, selected_skill_set)
    linked_count = 0
    for skill_dir in global_skills_dir.iterdir():
        if not is_valid_skill_dir(skill_dir):
            continue
        if skill_dir.name not in selected_skill_set:
            continue
        dest = member_skills_dir / skill_dir.name
        if path_exists_or_link(dest):
            continue
        link_skill_dir(skill_dir, dest)
        linked_count += 1
        logger.info(
            "[swarm.member_skill_toolkit] Linked skill '%s' to member workspace",
            skill_dir.name,
        )

    existing_skill_names = {
        path.name for path in member_skills_dir.iterdir() if path_exists_or_link(path)
    }
    missing = sorted(selected_skill_set - existing_skill_names)
    if missing:
        logger.warning(
            "[swarm.member_skill_toolkit] configured skills not found in global dir: %s",
            missing,
        )

    logger.info(
        "[swarm.member_skill_toolkit] Total configured skills linked to member: %d",
        linked_count,
    )


def _extract_skill_name_from_tool_result(result: dict[str, object]) -> str:
    """Extract a skill name from a skill tool result.

    Args:
        result: The skill tool invocation result mapping.

    Returns:
        The resolved skill name, or an empty string when none is present.
    """
    skill = result.get("skill")
    if isinstance(skill, dict):
        skill_name = str(skill.get("name", "")).strip()
        if skill_name:
            return skill_name
    return str(result.get("skill_name", "") or result.get("name", "")).strip()


def _workspace_root(ctx: Any) -> str | None:
    """Resolve the member workspace root path (gate for the toolkit)."""
    workspace = ctx.workspace
    return getattr(workspace, "root_path", None) if workspace else None


class MemberSkillToolkitInput(ConstructionInput):
    """Construction inputs for the member skill-toolkit rail."""

    skills: list[str] = param_field(
        default_factory=list,
        description="Selected member skill names to expose in the toolkit.",
    )
    workspace_root: str | None = context_field(
        resolver=_workspace_root,
        description="Member workspace root (gate; skipped when absent).",
    )
    global_skills_dir: str | None = context_field(
        attr="global_skills_dir",
        description="Global agent skills directory.",
    )
    session_id: str = context_field(
        attr="session_id",
        default="",
        description="Active session id (for runtime skill-link refresh).",
    )
    channel: str = context_field(
        attr="channel",
        default="default",
        description="Resolved channel key for the per-channel team manager.",
    )


@harness_element(
    kind=ElementKind.RAIL,
    name=MEMBER_SKILL_TOOLKIT,
    description="Member-scoped skill toolkit rail: prepares the member workspace "
    "skills directory and exposes skill discovery/management.",
    input_model=MemberSkillToolkitInput,
)
def build_member_skill_toolkit(params: dict, ctx: Any) -> object | None:
    """Build a member-scoped skill toolkit rail for the current member.

    Links the member's configured skills into its workspace ``skills``
    directory and returns a ``MemberSkillToolkitRail`` bound to the shared
    agent workspace, wired with a callback that refreshes the link views after
    runtime skill installs/uninstalls.

    Args:
        params: Provider params; ``params["skills"]`` carries the selected
            member skill names (from ``config_specs``).
        ctx: The active ``SwarmBuildContext`` for the current member.

    Returns:
        A ``MemberSkillToolkitRail`` instance, or ``None`` when the member has
        no usable workspace (the capability is skipped for this member).
    """
    inp = MemberSkillToolkitInput.resolve(params, ctx)
    root_path = inp.workspace_root
    if not root_path:
        return None

    member_skills_dir = Path(root_path) / "skills"
    selected_skills = [str(skill).strip() for skill in inp.skills if str(skill).strip()]
    global_skills_dir = Path(inp.global_skills_dir) if inp.global_skills_dir else get_agent_skills_dir()
    agent_workspace_dir = get_agent_workspace_dir()
    session_id = inp.session_id
    channel = inp.channel

    # Link member-configured skills so the member workspace exposes only that
    # member's skill view (no copies, no per-member skills_state.json).
    try:
        member_skills_dir.mkdir(parents=True, exist_ok=True)
        if selected_skills:
            _link_member_configured_skills(
                member_skills_dir, selected_skills, global_skills_dir
            )
    except Exception as exc:
        logger.warning(
            "[swarm.member_skill_toolkit] skill link refresh failed: %s", exc
        )

    # The skill manager / toolkit operate on the shared agent workspace so
    # installs are shared; each member only sees its own linked view.
    member_skill_manager: Any | None = None
    try:
        member_skill_manager = SkillManager(workspace_dir=str(agent_workspace_dir))
    except Exception as exc:
        logger.warning(
            "[swarm.member_skill_toolkit] member SkillManager setup failed: %s", exc
        )

    def refresh_member_skill_links(result: dict[str, object]) -> None:
        """Refresh linked skill views after a member skill tool mutation."""
        from jiuwenswarm.agents.harness.team.team_manager import get_team_manager

        if result.get("skill_removed") or result.get("removed"):
            skill_name = _extract_skill_name_from_tool_result(result)
            if skill_name:
                remove_skill_dir_link(member_skills_dir / skill_name)
        get_team_manager(channel).refresh_team_shared_skill_links(session_id)

    logger.info(
        "[swarm.member_skill_toolkit] MemberSkillToolkitRail built for skill workspace: %s",
        agent_workspace_dir,
    )
    return MemberSkillToolkitRail(
        workspace_dir=str(agent_workspace_dir),
        manager=member_skill_manager,
        refresh_links=refresh_member_skill_links,
    )


__all__ = [
    "MEMBER_SKILL_TOOLKIT",
    "TEAM_SKILL_USE",
    "build_member_skill_toolkit",
    "build_team_skill_use",
]
