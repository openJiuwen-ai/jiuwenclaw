# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team configuration loader."""

from __future__ import annotations

import logging
from copy import deepcopy
from pathlib import Path
from typing import Any

from openjiuwen.agent_teams.paths import get_agent_teams_home

from jiuwenclaw.config import get_config, hydrate_team_agent_model_from_tip

logger = logging.getLogger(__name__)

_DEFAULT_MAX_ITERATIONS = 200
_DEFAULT_COMPLETION_TIMEOUT = 600.0
_DEFAULT_AGENT_WORKSPACE = {"stable_base": True}
_DEFAULT_TEAM_WORKSPACE = {"enabled": True}
_DEFAULT_TRANSPORT = {"type": "inprocess"}


class TeamTemplateNotFoundError(ValueError):
    """Raised when a bound team references a template that no longer exists."""


def _get_modes_team(config_base: dict[str, Any]) -> dict[str, Any]:
    modes_raw = config_base.get("modes", {})
    if not isinstance(modes_raw, dict):
        return {}

    teams_raw = modes_raw.get("team", {})
    if not isinstance(teams_raw, dict):
        return {}
    return teams_raw


def get_team_template_snapshot(
    config_base: dict[str, Any] | None = None,
    *,
    template_id: str,
) -> dict[str, Any]:
    """Return a copy of the selected raw team template for team entity persistence."""
    if config_base is None:
        config_base = get_config()
    resolved_template_id, team_raw = _select_modes_team(
        config_base,
        template_id=template_id,
        strict_template=True,
    )
    snapshot = deepcopy(team_raw)
    if resolved_template_id and not str(snapshot.get("team_name") or "").strip():
        snapshot["team_name"] = resolved_template_id
    return snapshot


def _select_modes_team(
    config_base: dict[str, Any],
    template_id: str | None = None,
    *,
    strict_template: bool = False,
) -> tuple[str, dict[str, Any]]:
    """Select a team template from ``modes.team`` only."""
    teams_raw = _get_modes_team(config_base)
    requested_template_id = str(template_id or "").strip()
    if requested_template_id:
        candidate = teams_raw.get(requested_template_id)
        if isinstance(candidate, dict):
            logger.debug("[TeamConfigLoader] selected team template: %s", requested_template_id)
            return requested_template_id, candidate
        if strict_template:
            raise TeamTemplateNotFoundError(f"team template not found: {requested_template_id}")
        logger.warning("[TeamConfigLoader] requested team template not found: %s", requested_template_id)

    for team_name, team_raw in teams_raw.items():
        if isinstance(team_raw, dict):
            logger.debug("[TeamConfigLoader] selected team from modes.team: %s", team_name)
            return str(team_name), team_raw

    return "", {}


def _select_first_modes_team(config_base: dict[str, Any]) -> dict[str, Any]:
    _, team_raw = _select_modes_team(config_base)
    return team_raw


def resolve_team_section(config_base: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the active ``modes.team`` entry (first / only operational team section).

    Used by distributed / remote helpers that previously read a top-level
    ``config.team`` block. Roster and runtime settings live under ``modes.team``.
    """
    if config_base is None:
        config_base = get_config()
    return _select_first_modes_team(config_base)


def _resolve_team_raw_for_storage(config_base: dict[str, Any]) -> dict[str, Any]:
    return _select_first_modes_team(config_base) or {}


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

    # Shared default must not bake credentials: each member hydrates from its
    # own catalog agent_id tip (plan-equivalent). Drop secrets that get_config()
    # may have already resolved from the request-bound tip.
    model_client_config.pop("api_base", None)
    model_client_config.pop("api_key", None)

    logger.info(
        "[TeamConfigLoader] model config loaded: model_name=%s, provider=%s",
        model_name,
        model_client_config.get("client_provider", "unknown"),
    )
    return {
        "model_client_config": model_client_config,
        "model_request_config": model_request_config,
    }


def _resolve_member_tip_agent_id(*, agent_id: Any = None) -> str | None:
    """Tip bag id for a team member from roster ``agent_id``.

    Must match ``sync_agents_configs`` / ``teams.agents`` keys
    (e.g. ``expert-chief-researcher``). No ``tpl_*`` / ``member_name`` rewriting.
    """
    aid = str(agent_id or "").strip()
    return aid or None


def _member_tip_agent_id_by_role(team_raw: dict[str, Any]) -> dict[str, str]:
    """Map ``modes.team.agents`` keys (leader / member_name) → catalog tip id."""
    mapping: dict[str, str] = {}

    def _put(role_key: str, *, agent_id: Any = None) -> None:
        role = str(role_key or "").strip()
        tip_id = _resolve_member_tip_agent_id(agent_id=agent_id)
        if role and tip_id:
            mapping[role] = tip_id

    leader_raw = team_raw.get("leader")
    if isinstance(leader_raw, dict):
        member_name = leader_raw.get("member_name")
        agent_id = leader_raw.get("agent_id")
        # agents.leader block is keyed "leader"; also map member_name for safety.
        _put("leader", agent_id=agent_id)
        _put(str(member_name or "").strip(), agent_id=agent_id)

    teammate_raw = team_raw.get("teammate")
    if isinstance(teammate_raw, dict):
        _put("teammate", agent_id=teammate_raw.get("agent_id"))

    predefined = team_raw.get("predefined_members")
    if isinstance(predefined, list):
        for item in predefined:
            if not isinstance(item, dict):
                continue
            member_name = item.get("member_name")
            _put(str(member_name or "").strip(), agent_id=item.get("agent_id"))

    return mapping


def _skills_list_from_tip(tip_agent_id: str | None) -> list[str] | None:
    """Parse tip ``ENABLED_SKILLS`` into a skill-name list (None if empty)."""
    from jiuwenclaw.agentserver.skill_manager import resolve_string_or_list_config
    from jiuwenclaw.config import resolve_model_credential_tip

    aid = str(tip_agent_id or "").strip()
    if not aid:
        return None
    tip = resolve_model_credential_tip(agent_id=aid) or {}
    skills = resolve_string_or_list_config(tip.get("ENABLED_SKILLS"))
    return skills or None


def _normalize_skills_list(raw: Any) -> list[str] | None:
    from jiuwenclaw.agentserver.skill_manager import resolve_string_or_list_config

    skills = resolve_string_or_list_config(raw)
    return skills or None


def _hydrate_agent_skills_for_role(
    skills_raw: Any,
    *,
    tip_agent_id: str | None,
) -> list[str] | None:
    """Load ``skills`` from yaml first; tip only if yaml empty."""
    yaml_skills = _normalize_skills_list(skills_raw)
    if yaml_skills is not None:
        return yaml_skills
    return _skills_list_from_tip(tip_agent_id)


def _hydrate_agent_model_for_role(
    model_raw: dict[str, Any] | None,
    *,
    tip_agent_id: str | None,
) -> dict[str, Any]:
    """Build runtime model from tip (MODEL_*/API_*) plus local client knobs.

    Tip supplies identity and credentials. ``timeout`` / ``verify_ssl`` use
    local defaults when absent.
    """
    from jiuwenclaw.config import resolve_model_credential_tip

    if isinstance(model_raw, dict) and model_raw:
        model = deepcopy(model_raw)
    else:
        model = {
            "model_client_config": {
                "timeout": 1800,
                "verify_ssl": False,
                "custom_headers": {},
            },
            "model_request_config": {},
        }
    mcc = model.get("model_client_config")
    if isinstance(mcc, dict):
        # Drop sticky secrets so the member tip always wins.
        mcc.pop("api_base", None)
        mcc.pop("api_key", None)
        mcc.setdefault("timeout", 1800)
        mcc.setdefault("verify_ssl", False)
        mcc.setdefault("custom_headers", {})
    else:
        model["model_client_config"] = {
            "timeout": 1800,
            "verify_ssl": False,
            "custom_headers": {},
        }

    tip = resolve_model_credential_tip(agent_id=tip_agent_id)
    tip_keys_present = sorted(
        key
        for key in ("MODEL_NAME", "MODEL_PROVIDER", "API_BASE", "API_KEY", "ENABLED_SKILLS")
        if str((tip or {}).get(key) or "").strip()
    )
    hydrated = hydrate_team_agent_model_from_tip(
        model,
        agent_id=tip_agent_id,
    )
    out_mcc = hydrated.get("model_client_config") or {}
    if tip_agent_id and "MODEL_NAME" not in tip_keys_present:
        logger.warning(
            "[TeamConfigLoader] tip miss identity: tip_agent_id=%s tip_keys=%s "
            "(expected MODEL_NAME in sync agents[].env)",
            tip_agent_id,
            tip_keys_present,
        )
    elif tip_agent_id and "API_BASE" not in tip_keys_present:
        logger.warning(
            "[TeamConfigLoader] tip miss credentials: tip_agent_id=%s tip_keys=%s",
            tip_agent_id,
            tip_keys_present,
        )
    # Stash for caller log (non-secret presence flags only).
    hydrated["_tip_diag"] = {
        "tip_agent_id": tip_agent_id,
        "tip_keys": tip_keys_present,
        "provider": str(out_mcc.get("client_provider") or "").strip() or None,
        "model_name": str(out_mcc.get("model_name") or "").strip()
        or str((hydrated.get("model_request_config") or {}).get("model") or "").strip()
        or None,
        "api_base_set": bool(str(out_mcc.get("api_base") or "").strip()),
        "api_key_set": bool(str(out_mcc.get("api_key") or "").strip()),
    }
    return hydrated


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
    tip_by_role = _member_tip_agent_id_by_role(team_raw)

    agents_raw = team_raw.get("agents", {})
    if not isinstance(agents_raw, dict) or not agents_raw:
        logger.warning("[TeamConfigLoader] agents config is empty, using default leader/teammate")
        agents_raw = {"leader": {}, "teammate": {}}

    top_agents = config_base.get("agents", {})
    if not isinstance(top_agents, dict):
        top_agents = {}

    agents: dict[str, Any] = {}
    for role_key, raw_agent_config in agents_raw.items():
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
        # Credentials from tip via catalog agent_id.
        tip_agent_id = tip_by_role.get(str(role_key))
        # Always assemble model from tip (+ local timeout/verify_ssl knobs).
        agent_spec["model"] = _hydrate_agent_model_for_role(
            agent_spec.get("model") if isinstance(agent_spec.get("model"), dict) else None,
            tip_agent_id=tip_agent_id,
        )
        # Tip ENABLED_SKILLS → agent.skills (yaml may already have a copy from sync).
        hydrated_skills = _hydrate_agent_skills_for_role(
            agent_spec.get("skills"),
            tip_agent_id=tip_agent_id,
        )
        if hydrated_skills is not None:
            agent_spec["skills"] = hydrated_skills
        else:
            agent_spec.pop("skills", None)
        diag = agent_spec["model"].pop("_tip_diag", {}) if isinstance(agent_spec["model"], dict) else {}
        if not tip_agent_id:
            logger.warning(
                "[TeamConfigLoader] agent role=%s missing catalog agent_id; "
                "hydrating from bound tip only",
                role_key,
            )
        logger.info(
            "[TeamConfigLoader] agent role=%s tip_agent_id=%s provider=%s model=%s "
            "api_base_set=%s api_key_set=%s skills=%s tip_keys=%s",
            role_key,
            tip_agent_id or "(bound)",
            diag.get("provider"),
            diag.get("model_name"),
            diag.get("api_base_set"),
            diag.get("api_key_set"),
            len(hydrated_skills or []),
            diag.get("tip_keys"),
        )
        agents[role_key] = agent_spec

    if "leader" not in agents:
        agents["leader"] = _build_agent_spec_dict(
            {},
            default_model=default_model,
            default_workspace=default_workspace,
            max_iterations=max_iterations,
            completion_timeout=completion_timeout,
        )
        tip_id = tip_by_role.get("leader")
        agents["leader"]["model"] = _hydrate_agent_model_for_role(
            agents["leader"].get("model"),
            tip_agent_id=tip_id,
        )
        if isinstance(agents["leader"].get("model"), dict):
            agents["leader"]["model"].pop("_tip_diag", None)
        leader_skills = _hydrate_agent_skills_for_role(None, tip_agent_id=tip_id)
        if leader_skills is not None:
            agents["leader"]["skills"] = leader_skills

    # Always ensure a teammate role template exists. Presets / tip sync may only
    # ship ``agents.leader`` (or leader + unrelated keys); enrich only rewrites
    # the fixed roles ``leader`` / ``teammate``.
    if "teammate" not in agents:
        logger.info(
            "[TeamConfigLoader] agents config missing teammate; "
            "adding default teammate template (existing_keys=%s)",
            sorted(str(k) for k in agents.keys()),
        )
        agents["teammate"] = _build_agent_spec_dict(
            {},
            default_model=default_model,
            default_workspace=default_workspace,
            max_iterations=max_iterations,
            completion_timeout=completion_timeout,
        )
        tip_id = tip_by_role.get("teammate")
        agents["teammate"]["model"] = _hydrate_agent_model_for_role(
            agents["teammate"].get("model"),
            tip_agent_id=tip_id,
        )
        if isinstance(agents["teammate"].get("model"), dict):
            agents["teammate"]["model"].pop("_tip_diag", None)
        mate_skills = _hydrate_agent_skills_for_role(None, tip_agent_id=tip_id)
        if mate_skills is not None:
            agents["teammate"]["skills"] = mate_skills

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


def _build_leader_spec(team_raw: dict[str, Any], *, language: str | None = None) -> dict[str, Any]:
    leader_raw = team_raw.get("leader", {})
    leader_name = (
        str(leader_raw.get("name", "")).strip()
        or str(leader_raw.get("display_name", "")).strip()
        or "TeamLeader"
    )
    leader_spec = {
        "member_name": leader_raw.get("member_name", "team_leader"),
        "display_name": leader_raw.get("display_name", "Team Leader"),
        "name": leader_name,
    }
    leader_spec.update(
        _map_member_public_private_fields(
            leader_raw,
            default_desc="天才项目管理专家",
            language=language,
        )
    )
    return leader_spec


# Appended to every team member private prompt (including leader).
# Roster sections key on member_name; models may leak IDs into user-facing prose.
# Require display_name in prose; member_name only in tool arguments.
# Selected by preferred_language (same pattern as team resume protocol).
_TEAM_MEMBER_DISPLAY_NAME_RULE_CN = (
    "## 成员称呼规范\n"
    "面向用户可见的内容（@提及、点名、广播、进展汇报、总结等正文）中提及团队成员时，"
    "一律使用其显示名（display_name，如「用户研究员」）。member_name（如 user-researcher）"
    "是系统内部标识，仅允许用于工具参数（如 send_message 的 to、create_task 的 assignee），"
    "不得出现在正文里。"
)
_TEAM_MEMBER_DISPLAY_NAME_RULE_EN = (
    "## Member naming convention\n"
    "When mentioning team members in user-visible text (@-mentions, call-outs, "
    "broadcasts, progress reports, summaries, etc.), always use their display name "
    "(display_name, e.g. \"User Researcher\"). member_name (e.g. user-researcher) "
    "is an internal identifier and may only appear in tool arguments "
    "(e.g. send_message `to`, create_task `assignee`); it must not appear in prose."
)
# Default / backward-compatible alias (Chinese is primary when language is unset).
_TEAM_MEMBER_DISPLAY_NAME_RULE = _TEAM_MEMBER_DISPLAY_NAME_RULE_CN


def _normalize_prompt_language(language: str | None) -> str:
    """Map config preferred_language to prompt locale ``cn`` or ``en``.

    Aligns with ``swarm.config_specs._subagent_language`` and agent-core
    ``resolve_language`` supported set (``cn`` / ``en``). Raw values like
    ``zh`` must not be written onto ``TeamSpec.language``.
    """
    lang = str(language or "zh").strip().lower()
    if lang in {"en", "english"}:
        return "en"
    if lang in {"zh", "cn", "zh-cn", "zh_cn", "chinese"}:
        return "cn"
    return "cn"


def _team_member_display_name_rule(language: str | None = None) -> str:
    """Return the display-name prompt rule for the configured language."""
    if _normalize_prompt_language(language) == "en":
        return _TEAM_MEMBER_DISPLAY_NAME_RULE_EN
    return _TEAM_MEMBER_DISPLAY_NAME_RULE_CN


def _map_member_public_private_fields(
    raw: dict[str, Any],
    *,
    default_desc: str = "",
    language: str | None = None,
) -> dict[str, str]:
    """Map relay/legacy identity fields onto TeamMemberSpec ``desc`` / ``prompt``.

    openjiuwen F_49 split public roster text (``desc``) from private prompt
    (``prompt``). Relay still syncs ``persona`` / ``prompt_hint``; without this
    mapping those keys are dropped by pydantic and the Leader roster has empty
    ``desc``, so ``create_task`` omits ``assignee`` and members race one claim
    pool (assistant often wins). Swarm Leaders set assignees because their
    roster carries real capability text.

    Append the language-selected display-name rule to the private prompt.
    """
    rule = _team_member_display_name_rule(language)
    desc = str(raw.get("desc") or raw.get("persona") or default_desc or "").strip()
    prompt = str(raw.get("prompt") or "").strip()
    if not prompt:
        prompt_parts = [
            str(raw.get("persona") or "").strip(),
            str(raw.get("prompt_hint") or "").strip(),
        ]
        prompt = "\n\n".join(part for part in prompt_parts if part)
    prompt = f"{prompt}\n\n{rule}" if prompt else rule
    return {"desc": desc, "prompt": prompt}


def _build_predefined_members(
    team_raw: dict[str, Any],
    *,
    language: str | None = None,
) -> list[dict[str, Any]]:
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
        member_spec.update(
            _map_member_public_private_fields(member_spec, language=language)
        )
        # Drop legacy keys so TeamMemberSpec does not silently ignore them.
        member_spec.pop("persona", None)
        member_spec.pop("prompt_hint", None)
        # TeamMemberSpec discriminates on role_type; default missing values to teammate.
        role_type = str(member_spec.get("role_type") or "").strip()
        member_spec["role_type"] = role_type or "teammate"

        predefined_members.append(member_spec)

    return predefined_members


def _resolve_enable_permissions(config_base: dict[str, Any], _team_raw: dict[str, Any]) -> bool:
    """Resolve the effective team-permission toggle.

    Team mode uses the same safety rail as plan: the global
    ``permissions.enabled`` switch. A per-team ``enable_permissions``
    field is ignored (not AND-gated) so the master switch applies
    directly.
    """
    return bool((config_base.get("permissions") or {}).get("enabled", False))


def load_team_spec_dict(
    config_base: dict[str, Any] | None = None,
    *,
    requested_model_name: str | None = None,
    template_id: str | None = None,
) -> dict[str, Any]:
    """Load team config and build a TeamAgentSpec-compatible dict.

    When ``requested_model_name`` is provided (e.g. from the chat page model
    selector), team members without an explicit ``modes.team.agents.*.model``
    fall back to the matching entry in ``models.defaults`` instead of the
    first list item.

    When ``template_id`` is provided (e.g. chat.send ``params.team_name`` from
    a bound expert team), select that ``modes.team`` entry instead of the first
    key. Missing template raises ``TeamTemplateNotFoundError``.
    """
    if config_base is None:
        config_base = get_config()
    requested_template = str(template_id or "").strip() or None
    if requested_template:
        resolved_id, team_raw = _select_modes_team(
            config_base,
            template_id=requested_template,
            strict_template=True,
        )
        logger.info(
            "[TeamConfigLoader] selected team by template_id=%s resolved=%s",
            requested_template,
            resolved_id,
        )
    else:
        resolved_id, team_raw = _select_modes_team(config_base)
        if resolved_id:
            logger.info(
                "[TeamConfigLoader] selected first modes.team entry: %s",
                resolved_id,
            )

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

    # Prefer modes.team map key when present so session-scoped naming stays aligned
    # with the catalog key relay sends as chat.send params.team_name.
    spec_dict["team_name"] = (
        str(resolved_id or team_raw.get("team_name", "team") or "team").strip() or "team"
    )
    spec_dict["lifecycle"] = team_raw.get("lifecycle", "persistent")
    spec_dict["teammate_mode"] = team_raw.get("teammate_mode", "build_mode")
    spec_dict["spawn_mode"] = team_raw.get("spawn_mode", "inprocess")
    # Human-member (HITT) is not migrated for ENT/relay yet — keep off unless
    # modes.team.enable_hitt is explicitly set true in config.
    spec_dict["enable_hitt"] = team_raw.get("enable_hitt", False)
    spec_dict["enable_permissions"] = _resolve_enable_permissions(config_base, team_raw)
    # Normalize before any consumer (prompt injection, TeamAgentSpec.build,
    # rails). preferred_language is often ``zh``; agent-core / rails expect
    # ``cn`` | ``en`` only.
    language = _normalize_prompt_language(config_base.get("preferred_language"))
    spec_dict["leader"] = _build_leader_spec(team_raw, language=language)
    spec_dict["agents"] = agents
    spec_dict["language"] = language

    workspace_spec = _build_workspace_spec(team_raw)
    if workspace_spec is not None:
        spec_dict["workspace"] = workspace_spec

    spec_dict["transport"] = _build_transport_spec(team_raw)

    predefined_members = _build_predefined_members(team_raw, language=language)
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
