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
    resolve_member_catalog_agent_id,
)
from jiuwenclaw.config import get_config

logger = logging.getLogger(__name__)

PLATFORM_MEMBER_RAILS = "swarm.platform_member_rails"


def _resolve_member_enabled_skills(
    context: SwarmBuildContext,
    *,
    member_name: str,
    role: str,
) -> list[str] | None:
    """Pick hydrated agent/yaml skills for this member only.

    Skills are per agent identity — never borrow another predefined member's
    list via the ``teammate`` role template. Lookup order:

    1. ``member_name`` (e.g. ``product-architect``)
    2. ``leader`` when ``role=leader`` and roster name ≠ ``leader``
    3. ``teammate`` only when this member *is* the teammate template identity
    """
    skills_map = getattr(context, "agent_skills_by_key", None)
    if not isinstance(skills_map, dict) or not skills_map:
        return None

    name = str(member_name or "").strip()
    role_s = str(role or "").strip().lower()
    candidates: list[str] = []
    if name:
        candidates.append(name)
    if role_s == "leader" and name != "leader":
        candidates.append("leader")
    elif name in {"", "teammate", "team_member"} and "teammate" not in candidates:
        # Dynamic / template identity: only then use teammate-key skills.
        candidates.append("teammate")

    seen: set[str] = set()
    for key_s in candidates:
        if not key_s or key_s in seen:
            continue
        seen.add(key_s)
        raw = skills_map.get(key_s)
        if isinstance(raw, list):
            names = [str(s).strip() for s in raw if str(s).strip()]
            if names:
                return names
    return None


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

    # Prefer template key for modes.team / tip catalog; session-scoped team_id
    # is still kept on workspace for path identity.
    catalog_team_id = (
        str(getattr(context, "team_template_id", "") or "").strip()
        or str(getattr(context, "team_id", "") or "").strip()
        or None
    )
    catalog_agent_id = resolve_member_catalog_agent_id(
        config if isinstance(config, dict) else None,
        member_name=member_name,
        role=role,
        team_id=catalog_team_id,
    )
    enabled_skills = _resolve_member_enabled_skills(
        context,
        member_name=member_name,
        role=role,
    )
    logger.info(
        "[swarm.member_rails] catalog resolve role=%s member=%s "
        "team_id=%s team_template_id=%s catalog_team_id=%s catalog_agent_id=%s "
        "yaml_skills=%s",
        role,
        member_name,
        getattr(context, "team_id", ""),
        getattr(context, "team_template_id", ""),
        catalog_team_id,
        catalog_agent_id or "(none)",
        len(enabled_skills or []),
    )

    member_info = MemberInfo(
        agent_name=member_name,
        role=role,
        model_name=get_default_model_name(config),
        catalog_agent_id=catalog_agent_id,
        enabled_skills=enabled_skills,
    )
    runtime = RuntimeInfo(channel=channel, language=language)
    team_workspace = TeamWorkspaceInfo(
        root_dir=context.team_ws_root,
        skills_dir=context.team_skills_dir,
        leader_skills_dir=context.leader_skills_dir,
        global_skills_dir=context.global_skills_dir,
        team_id=catalog_team_id,
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
