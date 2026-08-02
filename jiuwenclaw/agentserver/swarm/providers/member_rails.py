# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Swarm member rail providers for jiuwenclaw team assembly."""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen.agent_teams.harness.manifest import ElementKind, harness_element
from openjiuwen.agent_teams.rails.team_context import (
    get_messager,
    get_permissions_override,
    get_team_backend,
)

from jiuwenclaw.agentserver.swarm.context import SwarmBuildContext
from jiuwenclaw.agentserver.team.team_runtime_inheritance import (
    MemberInfo,
    RuntimeInfo,
    TeamWorkspaceInfo,
    build_member_rails,
    build_team_permission_rails,
    get_default_model_name,
)
from jiuwenclaw.config import get_config

logger = logging.getLogger(__name__)

PLATFORM_MEMBER_RAILS = "swarm.platform_member_rails"


@harness_element(
    kind=ElementKind.RAIL,
    name=PLATFORM_MEMBER_RAILS,
    description="jiuwenclaw platform member rails (RuntimePrompt, StreamEvent, permissions, …).",
)


def _build_platform_member_rails(
    params: dict[str, Any],
    context: SwarmBuildContext,
) -> list[Any]:
    """Build imperative platform rails via the enrich provider path."""
    role = str(getattr(context, "role", "") or "")
    member_name = str(getattr(context, "member_name", "") or "team_member")
    language = str(getattr(context, "language", "") or "cn")
    channel = str(getattr(context, "channel", "") or "default")
    enable_permissions = bool(params.get("enable_permissions", False))
    leader_member_name = str(params.get("leader_member_name") or "")

    config = context.config if isinstance(context.config, dict) else get_config()
    team_backend = get_team_backend(context)
    messager = get_messager(context)
    permissions_override = get_permissions_override(context)

    member_info = MemberInfo(
        agent_name=member_name,
        role=role,
        model_name=get_default_model_name(config),
    )
    runtime = RuntimeInfo(channel=channel, language=language)
    team_workspace = TeamWorkspaceInfo(
        root_dir=context.team_ws_root,
        skills_dir=context.team_skills_dir,
        leader_skills_dir=context.leader_skills_dir,
        global_skills_dir=context.global_skills_dir,
        team_id=context.team_id or None,
        config=config,
    )
    rails = build_member_rails(
        member_info=member_info,
        runtime=runtime,
        team_workspace=team_workspace,
    )
    if enable_permissions:
        perm_cfg = config.get("permissions") if isinstance(config, dict) else {}
        perm_cfg = perm_cfg if isinstance(perm_cfg, dict) else {}
        rails.extend(
            build_team_permission_rails(
                role=role,
                language=language,
                permissions_config=perm_cfg,
                team_backend=team_backend,
                messager=messager,
                member_name=member_name,
                leader_member_name=leader_member_name,
                permissions_override=permissions_override,
            )
        )
    logger.info(
        "[swarm.member_rails] platform_member_rails role=%s member=%s count=%d",
        role,
        member_name,
        len(rails),
    )
    return rails


__all__ = ["PLATFORM_MEMBER_RAILS"]
