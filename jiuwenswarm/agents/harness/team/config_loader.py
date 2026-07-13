# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team configuration loader."""

from __future__ import annotations

import logging
from copy import deepcopy
from pathlib import Path
from typing import Any

from openjiuwen.agent_teams.paths import get_agent_teams_home

from jiuwenswarm.common.config import get_config

logger = logging.getLogger(__name__)

_DEFAULT_MAX_ITERATIONS = 200
_DEFAULT_COMPLETION_TIMEOUT = 600.0
_DEFAULT_AGENT_WORKSPACE = {"stable_base": True}
_DEFAULT_TEAM_WORKSPACE = {"enabled": True}
_DEFAULT_TRANSPORT = {"type": "inprocess"}


def _select_first_modes_team(config_base: dict[str, Any]) -> dict[str, Any]:
    modes_raw = config_base.get("modes", {})
    if not isinstance(modes_raw, dict):
        return {}

    teams_raw = modes_raw.get("team", {})
    if not isinstance(teams_raw, dict):
        return {}

    for team_name, team_raw in teams_raw.items():
        if isinstance(team_raw, dict):
            logger.debug("[TeamConfigLoader] selected team from modes.team: %s", team_name)
            return team_raw

    return {}


def _resolve_team_raw_for_storage(config_base: dict[str, Any]) -> dict[str, Any]:
    selected = _select_first_modes_team(config_base)
    if selected:
        return selected

    legacy_team = config_base.get("team", {})
    if isinstance(legacy_team, dict) and legacy_team:
        return legacy_team

    if any(key in config_base for key in ("team_name", "leader", "agents", "storage", "predefined_members")):
        return config_base

    return {}


def resolve_team_sqlite_db_path(config_base: dict[str, Any] | None = None) -> Path | None:
    """Resolve the team sqlite database path using openjiuwen semantics."""
    if config_base is None:
        config_base = get_config()

    team_raw = _resolve_team_raw_for_storage(config_base)
    if not isinstance(team_raw, dict):
        return None

    storage_raw = team_raw.get("storage", {})
    if not isinstance(storage_raw, dict):
        return None

    storage_type = str(storage_raw.get("type", "")).strip().lower()
    if storage_type and storage_type != "sqlite":
        return None

    storage_params = storage_raw.get("params", {})
    if not isinstance(storage_params, dict):
        storage_params = {}

    conn_str = str(storage_params.get("connection_string", "")).strip()
    if not conn_str:
        return get_agent_teams_home() / "team.db"

    db_path = Path(conn_str).expanduser()
    if db_path.is_absolute():
        return db_path

    return get_agent_teams_home() / conn_str


def _resolve_default_model_config(
    config_base: dict[str, Any],
    *,
    requested_model_name: str | None = None,
) -> dict[str, Any]:
    models_raw = config_base.get("models", {})
    if not isinstance(models_raw, dict):
        return {}

    defaults_raw = models_raw.get("defaults")
    if isinstance(defaults_raw, list):
        # When the caller (chat page) provides a requested model name, prefer
        # the entry whose ``model_client_config.model_name`` matches it so
        # team members without an explicit ``modes.team.agents.*.model`` fall
        # back to the page-selected model instead of the first list item.
        requested = (requested_model_name or "").strip()
        if requested:
            for item in defaults_raw:
                if not isinstance(item, dict):
                    continue
                mcc = item.get("model_client_config") or {}
                if isinstance(mcc, dict) and mcc.get("model_name") == requested:
                    return item

        for item in defaults_raw:
            if isinstance(item, dict):
                return item

    legacy_default = models_raw.get("default")
    if isinstance(legacy_default, dict):
        return legacy_default

    return {}


def _build_default_model_dict(
    config_base: dict[str, Any],
    *,
    requested_model_name: str | None = None,
) -> dict[str, Any]:
    model_config = _resolve_default_model_config(
        config_base,
        requested_model_name=requested_model_name,
    )
    model_client_config = dict(model_config.get("model_client_config", {}))
    model_request_config = dict(model_config.get("model_config_obj", {}))

    model_name = model_client_config.get("model_name", "")
    if model_name and "model" not in model_request_config:
        model_request_config["model"] = model_name

    logger.info(
        "[TeamConfigLoader] model config loaded: model_name=%s, provider=%s",
        model_name,
        model_client_config.get("client_provider", "unknown"),
    )
    return {
        "model_client_config": model_client_config,
        "model_request_config": model_request_config,
    }


def _resolve_storage_config(storage_raw: dict[str, Any]) -> dict[str, Any]:
    storage_dict = deepcopy(storage_raw)
    storage_params = storage_dict.get("params", {})
    if "connection_string" not in storage_params:
        return storage_dict

    db_path = resolve_team_sqlite_db_path({"storage": storage_dict})
    if db_path is None:
        return storage_dict

    storage_params["connection_string"] = str(db_path)

    db_dir = db_path.parent
    if not db_dir.exists():
        db_dir.mkdir(parents=True, exist_ok=True)
        logger.info("[TeamConfigLoader] Created database directory: %s", db_dir)

    return storage_dict


def _build_agent_defaults() -> tuple[dict[str, Any], int, float]:
    return (
        deepcopy(_DEFAULT_AGENT_WORKSPACE),
        _DEFAULT_MAX_ITERATIONS,
        _DEFAULT_COMPLETION_TIMEOUT,
    )


def _build_agent_spec_dict(
    agent_config: dict[str, Any],
    *,
    default_model: dict[str, Any],
    default_workspace: dict[str, Any],
    max_iterations: int,
    completion_timeout: float,
) -> dict[str, Any]:
    merged = deepcopy(agent_config)
    merged.setdefault("model", deepcopy(default_model))
    merged.setdefault("workspace", deepcopy(default_workspace))
    merged.setdefault("max_iterations", max_iterations)
    merged.setdefault("completion_timeout", completion_timeout)
    return merged


def _build_agents_config(
    team_raw: dict[str, Any],
    config_base: dict[str, Any],
    *,
    requested_model_name: str | None = None,
) -> dict[str, Any]:
    default_model = _build_default_model_dict(
        config_base,
        requested_model_name=requested_model_name,
    )
    default_workspace, max_iterations, completion_timeout = _build_agent_defaults()

    agents_raw = team_raw.get("agents", {})
    if not isinstance(agents_raw, dict) or not agents_raw:
        logger.warning("[TeamConfigLoader] agents config is empty, using default leader/teammate")
        agents_raw = {"leader": {}, "teammate": {}}

    top_agents = config_base.get("agents", {})
    if not isinstance(top_agents, dict):
        top_agents = {}

    agents: dict[str, Any] = {}
    for agent_key, raw_agent_config in agents_raw.items():
        if isinstance(raw_agent_config, str) and raw_agent_config.startswith("$"):
            ref_name = raw_agent_config[1:]
            if ref_name in top_agents:
                agent_config = deepcopy(top_agents[ref_name])
                logger.debug(
                    "[TeamConfigLoader] resolved agent reference $%s -> agents.%s",
                    ref_name,
                    ref_name,
                )
            else:
                logger.warning(
                    "[TeamConfigLoader] agent reference '$%s' not found in top-level agents, using defaults",
                    ref_name,
                )
                agent_config = {}
        else:
            agent_config = dict(raw_agent_config) if isinstance(raw_agent_config, dict) else {}
        # No longer auto-fill all skills from global into each member by default.
        # On spawn, each member workspace exposes only its configured skill links.
        # Team-shared skills are maintained in the team workspace skill view.
        agent_spec = _build_agent_spec_dict(
            agent_config,
            default_model=default_model,
            default_workspace=default_workspace,
            max_iterations=max_iterations,
            completion_timeout=completion_timeout,
        )
        agents[agent_key] = agent_spec

    if "leader" not in agents:
        agents["leader"] = _build_agent_spec_dict(
            {},
            default_model=default_model,
            default_workspace=default_workspace,
            max_iterations=max_iterations,
            completion_timeout=completion_timeout,
        )

    if set(agents.keys()) == {"leader"}:
        logger.info(
            "[TeamConfigLoader] agents config contains only leader; "
            "adding default teammate template"
        )
        agents["teammate"] = _build_agent_spec_dict(
            {},
            default_model=default_model,
            default_workspace=default_workspace,
            max_iterations=max_iterations,
            completion_timeout=completion_timeout,
        )

    return agents


def _build_workspace_spec(team_raw: dict[str, Any]) -> dict[str, Any] | None:
    workspace_raw = team_raw.get("workspace")
    if not isinstance(workspace_raw, dict):
        workspace_spec = deepcopy(_DEFAULT_TEAM_WORKSPACE)
        workspace_spec.setdefault("version_control", False)
        return workspace_spec

    workspace_spec = deepcopy(workspace_raw)
    workspace_spec.setdefault("enabled", True)
    workspace_spec.setdefault("version_control", False)
    return workspace_spec


def _build_transport_spec(team_raw: dict[str, Any]) -> dict[str, Any]:
    transport_raw = team_raw.get("transport")
    if not isinstance(transport_raw, dict):
        return deepcopy(_DEFAULT_TRANSPORT)

    transport_spec = deepcopy(transport_raw)
    transport_spec.setdefault("type", "inprocess")
    return transport_spec


def _build_leader_spec(team_raw: dict[str, Any]) -> dict[str, Any]:
    leader_raw = team_raw.get("leader", {})
    leader_name = (
        str(leader_raw.get("name", "")).strip()
        or str(leader_raw.get("display_name", "")).strip()
        or "TeamLeader"
    )
    return {
        "member_name": leader_raw.get("member_name", "team_leader"),
        "display_name": leader_raw.get("display_name", "Team Leader"),
        "name": leader_name,
        "persona": leader_raw.get("persona", "天才项目管理专家"),
    }


def _build_predefined_members(team_raw: dict[str, Any]) -> list[dict[str, Any]]:
    predefined_members_raw = team_raw.get("predefined_members", [])
    if not isinstance(predefined_members_raw, list):
        logger.warning("[TeamConfigLoader] predefined_members must be a list, ignored")
        return []

    predefined_members: list[dict[str, Any]] = []
    for item in predefined_members_raw:
        if not isinstance(item, dict):
            continue

        member_name = str(item.get("member_name", "")).strip()
        if not member_name:
            logger.warning("[TeamConfigLoader] skipped predefined member without member_name: %s", item)
            continue

        identity_name = item.get("name") or item.get("display_name")
        if not identity_name or not str(identity_name).strip():
            logger.warning(
                "[TeamConfigLoader] skipped predefined member without name/display_name: %s",
                item,
            )
            continue

        member_spec = deepcopy(item)
        member_spec["member_name"] = member_name
        member_spec["display_name"] = str(identity_name).strip()
        member_spec["persona"] = member_spec.get("persona") or ""
        # openjiuwen TeamMemberSpec 现按 role_type 判别联合类型，缺省补 teammate
        role_type = str(member_spec.get("role_type") or "").strip()
        member_spec["role_type"] = role_type or "teammate"

        predefined_members.append(member_spec)

    return predefined_members


def _resolve_enable_permissions(config_base: dict[str, Any], team_raw: dict[str, Any]) -> bool:
    """Resolve the effective team-permission toggle.

    The effective value is ``permissions.enabled`` (global) AND
    ``enable_permissions`` (team-level). Both must be true for
    TeamPermissionRail to mount on teammates.
    """
    global_enabled = bool((config_base.get("permissions") or {}).get("enabled", False))
    team_enabled = bool(team_raw.get("enable_permissions", False))
    return global_enabled and team_enabled


def load_team_spec_dict(
    config_base: dict[str, Any] | None = None,
    *,
    requested_model_name: str | None = None,
) -> dict[str, Any]:
    """Load team config and build a TeamAgentSpec-compatible dict.

    When ``requested_model_name`` is provided (e.g. from the chat page model
    selector), team members without an explicit ``modes.team.agents.*.model``
    fall back to the matching entry in ``models.defaults`` instead of the
    first list item.
    """
    if config_base is None:
        config_base = get_config()
    team_raw = _select_first_modes_team(config_base)

    if not team_raw:
        logger.warning("[TeamConfigLoader] no modes.team config found, using defaults")
        team_raw = {}

    agents = _build_agents_config(
        team_raw,
        config_base,
        requested_model_name=requested_model_name,
    )
    spec_dict = deepcopy(team_raw)
    spec_dict.pop("enable_team_plan", None)

    spec_dict["team_name"] = str(team_raw.get("team_name", "team")).strip() or "team"
    spec_dict["lifecycle"] = team_raw.get("lifecycle", "persistent")
    spec_dict["teammate_mode"] = team_raw.get("teammate_mode", "build_mode")
    spec_dict["spawn_mode"] = team_raw.get("spawn_mode", "inprocess")
    spec_dict["enable_hitt"] = team_raw.get("enable_hitt", True)
    spec_dict["enable_permissions"] = _resolve_enable_permissions(config_base, team_raw)
    spec_dict["leader"] = _build_leader_spec(team_raw)
    spec_dict["agents"] = agents
    spec_dict["language"] = str(config_base.get("preferred_language", "zh")).strip().lower()

    workspace_spec = _build_workspace_spec(team_raw)
    if workspace_spec is not None:
        spec_dict["workspace"] = workspace_spec

    spec_dict["transport"] = _build_transport_spec(team_raw)

    predefined_members = _build_predefined_members(team_raw)
    if predefined_members:
        spec_dict["predefined_members"] = predefined_members
    elif "predefined_members" in spec_dict:
        spec_dict.pop("predefined_members", None)

    storage_raw = team_raw.get("storage", {})
    if storage_raw:
        spec_dict["storage"] = _resolve_storage_config(storage_raw)

    logger.info(
        "[TeamConfigLoader] team config loaded: team_name=%s, lifecycle=%s, agents=%s, predefined_members=%s",
        spec_dict["team_name"],
        spec_dict["lifecycle"],
        list(agents.keys()),
        [item["member_name"] for item in predefined_members],
    )
    return spec_dict
