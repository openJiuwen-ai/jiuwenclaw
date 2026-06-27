# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""WebChannel RPC handlers and shared constants (used by app gateway; single source with app.py)."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re
import secrets
import shutil
import time
import base64
import uuid
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv
import psutil
from openjiuwen.core.common.logging import LogManager
from openjiuwen.core.foundation.llm import Model, ProviderType
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig

from jiuwenswarm.common.config import (
    get_config,
    get_config_raw,
    get_default_models,
    replace_teams_in_config,
    update_default_models_in_config,
    update_heartbeat_in_config,
    update_channel_in_config,
    update_browser_in_config,
    update_preferred_language_in_config,
    update_context_engine_enabled_in_config,
    update_kv_cache_affinity_enabled_in_config,
    update_skill_retrieval_in_config,
    update_symphony_in_config,
    update_permissions_enabled_in_config,
    update_memory_forbidden_enabled_in_config,
    update_memory_forbidden_description_in_config,
    update_a2ui_in_config,
    update_updater_in_config,
)
from jiuwenswarm.server.runtime.a2ui.integration import (
    get_a2ui_config_payload,
    get_default_a2ui_config_payload,
    validate_a2ui_config_update,
)
from jiuwenswarm.common.reasoning_injector import build_reasoning_model_request_kwargs
from jiuwenswarm.common.updater import UpdaterService
from jiuwenswarm.common.utils import (
    get_agent_sessions_dir,
    get_env_file,
    get_root_dir,
    get_user_workspace_dir
)
from jiuwenswarm.agents.harness.common.auto_harness import AutoHarnessService
from jiuwenswarm.agents.harness.common.tools.web_file_download import build_file_download_info
from jiuwenswarm.common.version import __version__
from jiuwenswarm.symphony.skill_retrieval.taxonomy_config import (
    coerce_root_categories_value,
    root_categories_to_text,
)

for _jiuwen_log in LogManager.get_all_loggers().values():
    _jiuwen_log.set_level(logging.INFO)

logger = logging.getLogger(__name__)

_PROJECT_ROOT = get_root_dir()
_ENV_FILE = get_env_file()
load_dotenv(dotenv_path=_ENV_FILE, override=True)


_ENV_VAR_PLACEHOLDER_RE = re.compile(r"^\$\{([^:}]+)(?::-([^}]*))?\}$")


def _is_env_var_placeholder(value: Any) -> bool:
    return isinstance(value, str) and bool(_ENV_VAR_PLACEHOLDER_RE.match(value.strip()))


def _values_match(parsed_val: Any, resolved_val: Any) -> bool:
    """Compare a frontend-sent value against the resolved value of a model entry.

    Numeric and stringified env-var output (e.g. ``${TEMP:-0.95}`` resolves to ``"0.95"``)
    are normalized so that ``0.95 == "0.95"`` is treated as "unchanged".
    """
    if isinstance(parsed_val, bool) or isinstance(resolved_val, bool):
        return bool(parsed_val) == bool(resolved_val)
    if parsed_val is None and resolved_val is None:
        return True
    try:
        return float(parsed_val) == float(resolved_val)
    except (TypeError, ValueError):
        pass
    return str(parsed_val if parsed_val is not None else "") == str(
        resolved_val if resolved_val is not None else ""
    )


def _serialize_reasoning_level(value: Any) -> Any:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    from ruamel.yaml.scalarstring import DoubleQuotedScalarString
    # Always emit a quoted YAML string so the same field never round-trips
    # as a mix of plain scalars and quoted scalars.
    return DoubleQuotedScalarString(text)


def _merge_models_for_replace_all(
        parsed: list[dict[str, Any]],
        raw_defaults: list[dict[str, Any]],
        resolved_defaults: list[dict[str, Any]],
        crypto: Any,
) -> list[dict[str, Any]]:
    """Merge the frontend draft with the persisted YAML so that env-var placeholders
    (``${VAR:-default}``) survive when the user edits unrelated fields.

    For each frontend entry that carries an ``origin_index`` pointing at a still-existing
    persisted entry, we deep-copy the raw entry (preserving placeholders, custom_headers,
    etc.) and only overwrite the fields whose value differs from the resolved snapshot
    the frontend was originally shown. New entries (no ``origin_index``) fall back to
    encrypting/storing the frontend payload verbatim.
    """
    import copy as _copy

    out: list[dict[str, Any]] = []
    for item in parsed:
        origin_idx = item.get("origin_index")
        raw_entry = None
        resolved_entry = None
        if isinstance(origin_idx, int) and 0 <= origin_idx < len(raw_defaults):
            raw_entry = raw_defaults[origin_idx]
            if 0 <= origin_idx < len(resolved_defaults):
                resolved_entry = resolved_defaults[origin_idx]

        if raw_entry is not None and isinstance(raw_entry, dict):
            new_entry = _copy.deepcopy(raw_entry)
            new_mcc = new_entry.setdefault("model_client_config", {})
            new_mco = new_entry.setdefault("model_config_obj", {})
            resolved_mcc = (resolved_entry or {}).get("model_client_config", {}) or {}
            resolved_mco = (resolved_entry or {}).get("model_config_obj", {}) or {}

            if not _values_match(item["model_name"], resolved_mcc.get("model_name")):
                new_mcc["model_name"] = item["model_name"]
            if not _values_match(item["api_base"], resolved_mcc.get("api_base")):
                new_mcc["api_base"] = item["api_base"]
            # client_provider: 当 YAML 仍是 ${MODEL_PROVIDER} 占位符时，其解析值会与前端
            # 选择（如 OpenAI）一致而被误判为"未改"，导致首次配置后占位符残留。只要原值是
            # 占位符就用前端值固化它。
            if item["model_provider"] and (
                _is_env_var_placeholder(new_mcc.get("client_provider"))
                or not _values_match(item["model_provider"], resolved_mcc.get("client_provider"))
            ):
                new_mcc["client_provider"] = item["model_provider"]
            if not _values_match(item["temperature"], resolved_mco.get("temperature")):
                new_mco["temperature"] = item["temperature"]
            reasoning_level = item.get("reasoning_level", "")
            if not _values_match(reasoning_level, resolved_mco.get("reasoning_level")):
                if reasoning_level:
                    new_mco["reasoning_level"] = _serialize_reasoning_level(reasoning_level)
                else:
                    new_mco.pop("reasoning_level", None)
            if not _values_match(item["timeout"], resolved_mcc.get("timeout")):
                new_mcc["timeout"] = item["timeout"]
            if not _values_match(item["alias"], (resolved_entry or {}).get("alias")):
                new_entry["alias"] = item["alias"]
            new_entry["is_default"] = item["is_default"]
            # api_key: resolved holds the decrypted plaintext shown to the frontend.
            # Unchanged → keep raw (placeholder or ciphertext); changed → encrypt new value.
            if not _values_match(item["api_key"], resolved_mcc.get("api_key")):
                new_mcc["api_key"] = (
                    crypto.encrypt(item["api_key"]) if (item["api_key"] and crypto) else item["api_key"]
                )
        else:
            # New entry — frontend payload is the source of truth.
            new_entry = {
                "model_client_config": {
                    "api_base": item["api_base"],
                    "api_key": (
                        crypto.encrypt(item["api_key"]) if (item["api_key"] and crypto) else item["api_key"]
                    ),
                    "model_name": item["model_name"],
                    "client_provider": item["model_provider"],
                    "timeout": item["timeout"],
                    "verify_ssl": item["verify_ssl"],
                },
                "model_config_obj": {
                    "temperature": item["temperature"],
                    **({"reasoning_level": _serialize_reasoning_level(item.get("reasoning_level"))}
                       if item.get("reasoning_level") else {}),
                },
                "is_default": item["is_default"],
                "alias": item["alias"],
            }

        out.append(new_entry)
    return out


# 仅满足 Channel 构造所需，不入队、不路由；仅用 channel_manager + message_handler 做入站/出站
class _DummyBus:
    async def publish_user_messages(self, msg):  # noqa: ANN001, ARG002
        pass

    async def route_incoming_message(self, msg):  # noqa: ANN001, ARG002
        pass

    async def route_user_message(self, msg):
        pass


# 仅转发到 Agent 的 Web method
_FORWARD_REQ_METHODS = frozenset({
    "initialize",
    "session.create",
    "session.switch",
    "acp.tool_response",
    "team.delete",
    "chat.send",
    "chat.interrupt",
    "chat.resume",
    "chat.user_answer",
    "history.get",
    "browser.start",
    # "tts.synthesize",
    "skills.marketplace.list",
    "skills.list",
    "skills.installed",
    "skills.get",
    "skills.toggle",
    "skills.install",
    "skills.import_local",
    "skills.marketplace.add",
    "skills.marketplace.remove",
    "skills.marketplace.toggle",
    "skills.uninstall",
    "skills.skillnet.search",
    "skills.skillnet.install",
    "skills.skillnet.install_status",
    "skills.skillnet.evaluate",
    "skills.clawhub.get_token",
    "skills.clawhub.set_token",
    "skills.clawhub.search",
    "skills.clawhub.download",
    "skills.teamskillshub.info",
    "skills.teamskillshub.init",
    "skills.teamskillshub.validate",
    "skills.teamskillshub.pack",
    "skills.teamskillshub.search",
    "skills.teamskillshub.install",
    "skills.teamskillshub.publish",
    "skills.teamskillshub.delete",
    "skills.retrieval.status",
    "skills.retrieval.index_build",
    "skills.retrieval.index_cancel",
    "skills.retrieval.search",
    "skills.retrieval.tree",
    "skills.evolution.status",
    "skills.evolution.get",
    "skills.evolution.save",
    "symphony.build_score",
    "symphony.pause_build",
    "symphony.score_status",
    "symphony.graph",
    "symphony.plan",
    "plugins.list",
    "plugins.install",
    "plugins.uninstall",
    "plugins.enable",
    "plugins.disable",
    "plugins.reload",
    "extensions.list",
    "extensions.import",
    "extensions.delete",
    "extensions.toggle",
    "team.snapshot",
    "team.history.get",
    # Agent configuration
    "agents.list",
    "agents.get",
    "agents.create",
    "agents.update",
    "agents.delete",
    "agents.enable",
    "agents.disable",
    "agents.tools_list",
    # Schedule task management
    "schedule.check_config",
    "schedule.update_config",
    "schedule.create",
    "schedule.run",
    "schedule.list",
    "schedule.status",
    "schedule.logs",
    "schedule.cancel",
    "schedule.delete",
    "issue.watch_once",
    "issue.state.list",
    "issue.matrix",
    "issue.delete",
})

_FORWARD_NO_LOCAL_HANDLER_METHODS = frozenset({
    "initialize",
    "session.create",
    "session.switch",
    "acp.tool_response",
    "team.delete",
    "browser.start",
    "team.snapshot",
    "team.history.get",
    "skills.marketplace.list",
    "skills.list",
    "skills.installed",
    "skills.get",
    "skills.toggle",
    "skills.install",
    "skills.import_local",
    "skills.marketplace.add",
    "skills.marketplace.remove",
    "skills.marketplace.toggle",
    "skills.uninstall",
    "skills.skillnet.search",
    "skills.skillnet.install",
    "skills.skillnet.install_status",
    "skills.skillnet.evaluate",
    "skills.clawhub.get_token",
    "skills.clawhub.set_token",
    "skills.clawhub.search",
    "skills.clawhub.download",
    "skills.teamskillshub.info",
    "skills.teamskillshub.init",
    "skills.teamskillshub.validate",
    "skills.teamskillshub.pack",
    "skills.teamskillshub.search",
    "skills.teamskillshub.install",
    "skills.teamskillshub.publish",
    "skills.teamskillshub.delete",
    "skills.retrieval.status",
    "skills.retrieval.index_build",
    "skills.retrieval.index_cancel",
    "skills.retrieval.search",
    "skills.retrieval.tree",
    "skills.evolution.status",
    "skills.evolution.get",
    "skills.evolution.save",
    "symphony.build_score",
    "symphony.pause_build",
    "symphony.score_status",
    "symphony.graph",
    "symphony.plan",
    "plugins.list",
    "plugins.install",
    "plugins.uninstall",
    "plugins.enable",
    "plugins.disable",
    "plugins.reload",
    "extensions.list",
    "extensions.import",
    "extensions.delete",
    "extensions.toggle",
    # Agent configuration
    "agents.list",
    "agents.get",
    "agents.create",
    "agents.update",
    "agents.delete",
    "agents.enable",
    "agents.disable",
    "agents.tools_list",
})

# 配置信息：config.get 返回、config.set 可修改的键（前端 param 名 -> 环境变量名）
# default 模型 + video/audio/vision 多模型
_CONFIG_SET_ENV_MAP = {
    # default 模型（主对话）
    "model_provider": "MODEL_PROVIDER",
    "model": "MODEL_NAME",
    "api_base": "API_BASE",
    "api_key": "API_KEY",
    # video 模型
    "video_api_base": "VIDEO_API_BASE",
    "video_api_key": "VIDEO_API_KEY",
    "video_model": "VIDEO_MODEL_NAME",
    "video_provider": "VIDEO_PROVIDER",
    # audio 模型
    "audio_api_base": "AUDIO_API_BASE",
    "audio_api_key": "AUDIO_API_KEY",
    "audio_model": "AUDIO_MODEL_NAME",
    "audio_provider": "AUDIO_PROVIDER",
    # vision 模型
    "vision_api_base": "VISION_API_BASE",
    "vision_api_key": "VISION_API_KEY",
    "vision_model": "VISION_MODEL_NAME",
    "vision_provider": "VISION_PROVIDER",
    # 其他
    "email_address": "EMAIL_ADDRESS",
    "email_token": "EMAIL_TOKEN",
    "embed_api_key": "EMBED_API_KEY",
    "embed_api_base": "EMBED_API_BASE",
    "embed_model": "EMBED_MODEL",
    "jina_api_key": "JINA_API_KEY",
    "bocha_api_key": "BOCHA_API_KEY",
    "serper_api_key": "SERPER_API_KEY",
    "perplexity_api_key": "PERPLEXITY_API_KEY",
    "github_token": "GITHUB_TOKEN",
    "evolution_auto_scan": "EVOLUTION_AUTO_SCAN",
    "skill_create": "SKILL_CREATE",
    "teamskills_market_url": "TEAM_SKILLS_HUB_BASE_URL",
    "teamskills_user_token": "TEAM_SKILLS_HUB_USER_TOKEN",
    "teamskills_system_token": "TEAM_SKILLS_HUB_SYSTEM_TOKEN",
    "teamskills_allowed_download_hosts": "TEAM_SKILLS_HUB_ALLOWED_DOWNLOAD_HOSTS",
    "free_search_ddg_enabled": "FREE_SEARCH_DDG_ENABLED",
    "free_search_bing_enabled": "FREE_SEARCH_BING_ENABLED",
    "free_search_proxy_url": "FREE_SEARCH_PROXY_URL",
    # agents
    "skills": "SKILLS",
    "max_iterations": "MAX_ITERATIONS",
    "completion_timeout": "COMPLETION_TIMEOUT",
    # team
    "team_name": "TEAM_NAME",
    "lifecycle": "LIFECYCLE",
    "teammate_mode": "TEAMATE_MODE",
    "spawn_mode": "SPAWN_MODE",
    "member_name": "MEMBER_NAME",
    "display_name": "DISPLAY_NAME",
    "persona": "PERSONA",
    "agent_key": "AGENT_KEY",
    "role_type": "ROLE_TYPE",
    "prompt_hint": "PROMPT_HINT",
}
# 配置项键名列表，用于日志等说明
CONFIG_KEYS = tuple(_CONFIG_SET_ENV_MAP.keys())

# 来自 config.yaml 的配置项（前端 param 名 -> config.yaml 路径）
_CONFIG_YAML_KEYS = frozenset({
    "context_engine_enabled",
    "kv_cache_affinity_enabled",
    "permissions_enabled",
    "memory_forbidden_enabled",
    "memory_forbidden_description",
    "a2ui_enabled",
})

_SYMPHONY_CONFIG_SPECS: dict[str, tuple[tuple[str, ...], str, Any]] = {
    "symphony_enabled": (("enabled",), "bool", False),
}
_SYMPHONY_CONFIG_KEYS = tuple(_SYMPHONY_CONFIG_SPECS.keys())
_SKILL_RETRIEVAL_CONFIG_SPECS: dict[str, tuple[tuple[str, ...], str, Any]] = {
    "skill_retrieval_enabled": (("enabled",), "bool", False),
    "skill_retrieval_build_branching_factor": (("build", "branching_factor"), "int", 128),
    "skill_retrieval_build_max_depth": (("build", "max_depth"), "int", 6),
    "skill_retrieval_build_root_categories": (("build", "root_categories"), "root_categories", ""),
    "skill_retrieval_build_max_workers": (("build", "max_workers"), "int", 2),
    "skill_retrieval_build_max_retries": (("build", "max_retries"), "non_negative_int", 2),
    "skill_retrieval_build_request_timeout_seconds": (("build", "request_timeout_seconds"), "float", 420.0),
    "skill_retrieval_build_total_timeout_seconds": (("build", "total_timeout_seconds"), "float", 0.0),
    "skill_retrieval_build_classification_batch_limit": (("build", "classification_batch_limit"), "int", 32),
    "skill_retrieval_build_discovery_seed": (("build", "discovery_seed"), "raw_int", 42),
    "skill_retrieval_build_postprocess_enabled": (("build", "postprocess_enabled"), "bool", True),
    "skill_retrieval_build_postprocess_max_passes": (("build", "postprocess_max_passes"), "non_negative_int", 1),
    "skill_retrieval_build_postprocess_min_skills": (("build", "postprocess_min_skills"), "int", 6),
    "skill_retrieval_build_equivalence_enabled": (("build", "equivalence_enabled"), "bool", True),
    "skill_retrieval_retrieve_compact_codes_enabled": (("retrieve", "compact_codes_enabled"), "bool", False),
    "skill_retrieval_retrieve_flatten_tree": (("retrieve", "flatten_tree"), "bool", False),
    "skill_retrieval_retrieve_max_exposure_depth": (("retrieve", "max_exposure_depth"), "int", 1),
}
_SKILL_RETRIEVAL_CONFIG_KEYS = tuple(_SKILL_RETRIEVAL_CONFIG_SPECS.keys())


def _coerce_config_panel_value(value: Any, value_type: str, default: Any) -> Any:
    if value_type == "bool":
        return str(value).strip().lower() in ("true", "1", "yes", "on", "enabled")
    if value_type == "int":
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return default
    if value_type == "non_negative_int":
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return default
    if value_type == "raw_int":
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    if value_type == "float":
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return default
    if value_type == "root_categories":
        return coerce_root_categories_value(value, allow_path=False) or ""
    return str(value if value is not None else default)


def _set_nested_config_value(target: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    current = target
    for segment in path[:-1]:
        child = current.get(segment)
        if not isinstance(child, dict):
            child = {}
            current[segment] = child
        current = child
    current[path[-1]] = value


def _get_nested_config_value(source: dict[str, Any], path: tuple[str, ...], default: Any) -> Any:
    current: Any = source
    for segment in path:
        if not isinstance(current, dict) or segment not in current:
            return default
        current = current.get(segment)
    return default if current is None else current


def _flatten_symphony_for_config_panel(raw: dict[str, Any]) -> dict[str, str]:
    symphony = raw.get("symphony") if isinstance(raw.get("symphony"), dict) else {}
    flat: dict[str, str] = {}
    for key, (path, value_type, default) in _SYMPHONY_CONFIG_SPECS.items():
        value = _get_nested_config_value(symphony, path, default)
        if value_type == "bool":
            flat[key] = "true" if bool(value) else "false"
        elif value_type == "root_categories":
            flat[key] = root_categories_to_text(value)
        else:
            flat[key] = str(value)
    flat.update(_flatten_skill_retrieval_for_config_panel(raw))
    return flat


def _flatten_skill_retrieval_for_config_panel(raw: dict[str, Any]) -> dict[str, str]:
    symphony = raw.get("symphony") if isinstance(raw.get("symphony"), dict) else {}
    section = symphony.get("skill_retrieval") if isinstance(symphony.get("skill_retrieval"), dict) else {}
    flat: dict[str, str] = {}
    for key, (path, value_type, default) in _SKILL_RETRIEVAL_CONFIG_SPECS.items():
        value = _get_nested_config_value(section, path, default)
        if value_type == "bool":
            flat[key] = "true" if bool(value) else "false"
        elif value_type == "root_categories":
            flat[key] = root_categories_to_text(value)
        else:
            flat[key] = str(value)
    return flat


def _build_symphony_config_update(params: dict[str, Any]) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    for key, (path, value_type, default) in _SYMPHONY_CONFIG_SPECS.items():
        if key not in params:
            continue
        value = _coerce_config_panel_value(params[key], value_type, default)
        _set_nested_config_value(updates, path, value)
    return updates


def _build_skill_retrieval_config_update(params: dict[str, Any]) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    for key, (path, value_type, default) in _SKILL_RETRIEVAL_CONFIG_SPECS.items():
        if key not in params:
            continue
        value = _coerce_config_panel_value(params[key], value_type, default)
        _set_nested_config_value(updates, path, value)
    return updates


def _flatten_modes_team_for_config_panel(raw: dict[str, Any]) -> dict[str, str]:
    """Return the legacy flat fields consumed by the web config panel."""
    modes = raw.get("modes")
    teams_raw = modes.get("team") if isinstance(modes, dict) else {}
    if not isinstance(teams_raw, dict):
        teams_raw = {}

    flat: dict[str, str] = {}
    agent_specs: dict[str, dict[str, Any]] = {}

    panel_cfg = raw.get("web_config_panel")
    if isinstance(panel_cfg, dict):
        registry = panel_cfg.get("agent_team_agents")
        if isinstance(registry, dict):
            for agent_key, spec in registry.items():
                if isinstance(agent_key, str) and isinstance(spec, dict):
                    agent_specs[agent_key] = spec

    def add_agent(agent_key: str, spec: Any) -> str:
        if not agent_key:
            return ""
        if isinstance(spec, dict) and agent_key not in agent_specs:
            agent_specs[agent_key] = spec
        return agent_key

    def model_name_from_spec(spec: dict[str, Any]) -> str:
        model_cfg = spec.get("model")
        if not isinstance(model_cfg, dict):
            return ""
        if model_cfg.get("model") is not None:
            return str(model_cfg.get("model") or "")
        request_cfg = model_cfg.get("model_request_config")
        if isinstance(request_cfg, dict) and request_cfg.get("model") is not None:
            return str(request_cfg.get("model") or "")
        client_cfg = model_cfg.get("model_client_config")
        if isinstance(client_cfg, dict) and client_cfg.get("model_name") is not None:
            return str(client_cfg.get("model_name") or "")
        return ""

    for team_idx, (team_name, team_spec) in enumerate(teams_raw.items()):
        if team_idx >= 10 or not isinstance(team_spec, dict):
            continue
        team_prefix = f"team_{team_idx}_"
        flat[f"{team_prefix}name"] = str(team_spec.get("team_name") or team_name or "")
        flat[f"{team_prefix}lifecycle"] = str(team_spec.get("lifecycle") or "")
        flat[f"{team_prefix}teammate_mode"] = str(team_spec.get("teammate_mode") or "")
        flat[f"{team_prefix}spawn_mode"] = str(team_spec.get("spawn_mode") or "")
        flat[f"{team_prefix}enable_permissions"] = (
            "true" if bool(team_spec.get("enable_permissions", False)) else "false"
        )

        agents = team_spec.get("agents")
        if not isinstance(agents, dict):
            agents = {}

        leader = team_spec.get("leader")
        if isinstance(leader, dict):
            for key in ("member_name", "display_name", "persona"):
                flat[f"{team_prefix}leader_{key}"] = str(leader.get(key) or "")
        leader_key = str(leader.get("agent_key") or "") if isinstance(leader, dict) else ""
        if not leader_key:
            leader_key = f"{team_name}_leader"
        flat[f"{team_prefix}leader_agent_key"] = add_agent(leader_key, agents.get("leader"))

        teammate_spec = agents.get("teammate")
        if isinstance(teammate_spec, dict):
            teammate = team_spec.get("teammate")
            teammate_key = str(teammate.get("agent_key") or "") if isinstance(teammate, dict) else ""
            if not teammate_key:
                teammate_key = f"{team_name}_teammate"
            flat[f"{team_prefix}teammate_agent_key"] = add_agent(teammate_key, teammate_spec)
        else:
            flat[f"{team_prefix}teammate_agent_key"] = ""

        members_out: list[dict[str, str]] = []
        members = team_spec.get("predefined_members")
        if isinstance(members, list):
            for member in members:
                if not isinstance(member, dict):
                    continue
                member_name = str(member.get("member_name") or "")
                agent_key = str(member.get("agent_key") or "")
                if not agent_key:
                    agent_key = f"{team_name}_{member_name}" if member_name else ""
                if agent_key:
                    add_agent(agent_key, agents.get(member_name))
                members_out.append({
                    "member_name": member_name,
                    "display_name": str(member.get("display_name") or ""),
                    "persona": str(member.get("persona") or ""),
                    "prompt_hint": str(member.get("prompt_hint") or ""),
                    "agent_key": agent_key,
                })
        flat[f"{team_prefix}predefined_members"] = json.dumps(members_out, ensure_ascii=False)

    for agent_idx, (agent_key, spec) in enumerate(agent_specs.items()):
        if agent_idx >= 10:
            break
        flat[f"agent_name_{agent_idx}"] = agent_key
        flat[f"agent_model_{agent_idx}"] = model_name_from_spec(spec)
        skills = spec.get("skills")
        flat[f"agent_skills_{agent_idx}"] = ",".join(str(item) for item in skills) if isinstance(skills, list) else ""
        flat[f"agent_max_iterations_{agent_idx}"] = str(spec.get("max_iterations") or 200)
        flat[f"agent_completion_timeout_{agent_idx}"] = str(spec.get("completion_timeout") or 600)

    return flat


async def _clear_agent_config_cache(agent_client=None) -> None:
    """写回 config.yaml 后清除 agent 侧配置缓存，使下次读取时得到最新文件内容。"""
    try:
        if agent_client is not None:
            from jiuwenswarm.common.e2a.gateway_normalize import e2a_from_agent_fields
            from jiuwenswarm.common.schema.message import ReqMethod

            env = e2a_from_agent_fields(
                request_id=f"cfg-reload-{uuid.uuid4().hex[:8]}",
                channel_id="",
                req_method=ReqMethod.AGENT_RELOAD_CONFIG,
            )
            await agent_client.send_request(env)
        else:
            get_config()
    except Exception:  # noqa: BLE001
        pass


def _make_session_id() -> str:
    # 与前端 generateSessionId 保持一致：毫秒时间戳(16进制) + 6位随机16进制
    ts = format(int(time.time() * 1000), "x")
    suffix = secrets.token_hex(3)
    return f"sess_{ts}_{suffix}"


@dataclass
class WebHandlersBindParams:
    """Named bundle for :func:`_register_web_handlers` (avoids long positional / keyword lists)."""

    channel: Any
    agent_client: Any = None
    message_handler: Any = None
    channel_manager: Any = None
    on_config_saved: Any = None
    heartbeat_service: Any = None
    cron_controller: Any = None
    updater_service: UpdaterService | None = None


def _register_web_handlers(bind: WebHandlersBindParams) -> None:
    """注册 Web 前端需要的 method 与 on_connect。
    on_config_saved: 可选，config.set 写回后调用的回调；
        updated_env_keys 为本次改动的键名集合，
        env_updates 为本次变更的环境变量增量（仅包含更新项），
        config_payload 为当前最新配置快照；
        返回 True 表示已热更新未重启，False 表示已安排进程重启。
    heartbeat_service: 可选，GatewayHeartbeatService 实例，用于处理 heartbeat.get_conf / heartbeat.set_conf。
    """
    channel = bind.channel
    agent_client = bind.agent_client
    message_handler = bind.message_handler
    channel_manager = bind.channel_manager
    on_config_saved = bind.on_config_saved
    heartbeat_service = bind.heartbeat_service
    cron_controller = bind.cron_controller
    updater_service = bind.updater_service

    from jiuwenswarm.common.schema.message import Message, EventType

    def _resolve(ref, key="value"):
        """若为 ref 字典则取 key（无则返回 None），否则返回自身。"""
        if isinstance(ref, dict):
            return ref.get(key)
        return ref

    def _schedule_clear_agent_config_cache(name: str) -> None:
        asyncio.create_task(
            _clear_agent_config_cache(_resolve(agent_client)),
            name=f"{name}.clear_agent_config_cache",
        )

    def _resolve_env_vars(value: Any) -> Any:
        """Recursively resolve environment variables in config values."""
        if isinstance(value, str):
            pattern = r'\$\{([^:}]+)(?::-([^}]*))?\}'

            def replace_env(match):
                var_name = match.group(1)
                default = match.group(2) if match.group(2) is not None else ""
                return os.getenv(var_name, default)

            return re.sub(pattern, replace_env, value)
        elif isinstance(value, dict):
            return {k: _resolve_env_vars(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [_resolve_env_vars(item) for item in value]
        else:
            return value

    async def _on_connect(ws):
        ac = _resolve(agent_client)
        if ac is None or not getattr(ac, "server_ready", False):
            logger.debug("[_on_connect] Agent 未就绪，跳过 connection.ack")
            return
        sid = _make_session_id()

        ack_msg = Message(
            id=f"ack-{sid}",
            type="event",
            channel_id=channel.channel_id,
            session_id=sid,
            params={},
            timestamp=time.time(),
            ok=True,
            event_type=EventType.CONNECTION_ACK,
            payload={
                "session_id": sid,
                "mode": "BUILD",
                "tools": [],
                "protocol_version": "1.0",
            },
        )
        mh = _resolve(message_handler)
        if mh:
            await mh.publish_robot_messages(ack_msg)
        else:
            await channel.send(ack_msg)

    channel.on_connect(_on_connect)

    async def _config_get(ws, req_id, params, session_id):
        # 返回 _CONFIG_SET_ENV_MAP 里所有键对应的环境变量当前值
        payload = {
            param_key: (os.getenv(env_key) or "")
            for param_key, env_key in _CONFIG_SET_ENV_MAP.items()
        }
        payload["app_version"] = __version__
        # 合并 config.yaml 中的配置项
        try:
            raw = get_config_raw()
            for key, val in payload.items():
                from jiuwenswarm.extensions.registry import ExtensionRegistry
                if (("api_key" in key.lower() or "token" in key.lower())
                        and ExtensionRegistry.get_instance().get_crypto_provider()):
                    payload[key] = ExtensionRegistry.get_instance().get_crypto_provider().decrypt(val)
            ctx_cfg = (raw.get("react") or {}).get("context_engine_config") or {}
            payload["context_engine_enabled"] = "true" if ctx_cfg.get("enabled", False) else "false"
            payload["kv_cache_affinity_enabled"] = (
                "true" if ctx_cfg.get("enable_kv_cache_release", False) else "false"
            )
            perm_cfg = raw.get("permissions") or {}
            payload["permissions_enabled"] = "true" if perm_cfg.get("enabled", False) else "false"
            # skill_create / evolution_auto_scan: env var takes precedence, fallback to config.yaml
            evolution_cfg = (raw.get("react") or {}).get("evolution") or {}
            skill_create_env = os.getenv("SKILL_CREATE")
            if skill_create_env is not None:
                payload["skill_create"] = "true" if skill_create_env.lower() in ("true", "1", "yes") else "false"
            else:
                payload["skill_create"] = "true" if evolution_cfg.get("skill_create", False) else "false"
            auto_scan_env = os.getenv("EVOLUTION_AUTO_SCAN")
            if auto_scan_env is not None:
                payload["evolution_auto_scan"] = "true" if auto_scan_env.lower() in ("true", "1", "yes") else "false"
            else:
                payload["evolution_auto_scan"] = "true" if evolution_cfg.get("auto_scan", False) else "false"
            memory_cfg = (raw.get("memory") or {}).get("forbidden_memory_definition") or {}
            payload["memory_forbidden_enabled"] = "true" if memory_cfg.get("enabled", False) else "false"
            memory_desc = memory_cfg.get("description") or {}
            payload["memory_forbidden_description"] = memory_desc
            payload.update(get_a2ui_config_payload(raw))
            payload.update(_flatten_symphony_for_config_panel(raw))
            if not payload.get("free_search_ddg_enabled"):
                payload["free_search_ddg_enabled"] = "false"
            if not payload.get("free_search_bing_enabled"):
                payload["free_search_bing_enabled"] = "false"
            payload.update(_flatten_modes_team_for_config_panel(raw))
        except Exception:  # noqa: BLE001
            payload.setdefault("context_engine_enabled", "false")
            payload.setdefault("kv_cache_affinity_enabled", "false")
            payload.setdefault("permissions_enabled", "false")
            payload.setdefault("skill_create", "false")
            payload.setdefault("evolution_auto_scan", "false")
            payload.setdefault("memory_forbidden_enabled", "false")
            payload.setdefault("memory_forbidden_description", "")
            for key, value in get_default_a2ui_config_payload().items():
                payload.setdefault(key, value)
            for key, (_, value_type, default) in {
                **_SYMPHONY_CONFIG_SPECS,
                **_SKILL_RETRIEVAL_CONFIG_SPECS,
            }.items():
                if value_type == "bool":
                    default_text = "true" if default else "false"
                elif value_type == "root_categories":
                    default_text = root_categories_to_text(default)
                else:
                    default_text = str(default)
                payload.setdefault(key, default_text)
            payload.setdefault("free_search_ddg_enabled", "false")
            payload.setdefault("free_search_bing_enabled", "false")
        await channel.send_response(ws, req_id, ok=True, payload=payload)

    def _persist_env_updates(updates: dict[str, str]) -> None:
        """把已更新的环境变量写回 .env（仅覆盖或追加对应 KEY=value 行）。"""
        env_path = _ENV_FILE
        if not updates:
            return
        try:
            lines: list[str] = []
            if env_path.is_file():
                with open(env_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            updated_keys = set(updates.keys())
            new_lines: list[str] = []
            for line in lines:
                stripped = line.strip()
                found = False
                for env_key, value in updates.items():
                    if stripped.startswith(env_key + "="):
                        new_lines.append(f'{env_key}="{value}"\n' if value else f"{env_key}=\n")
                        found = True
                        break
                if not found:
                    new_lines.append(line)
            for env_key, value in updates.items():
                if not any(s.strip().startswith(env_key + "=") for s in new_lines):
                    new_lines.append(f'{env_key}="{value}"\n' if value else f"{env_key}=\n")
            env_path.parent.mkdir(parents=True, exist_ok=True)
            with open(env_path, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
        except OSError as e:
            logger.warning("[config.set] 写回 .env 失败: %s", e)

    class _ConfigBadRequest(ValueError):
        pass

    class _ConfigInternalError(RuntimeError):
        pass

    def _encrypt_config_params(params: dict[str, Any]) -> dict[str, Any]:
        encrypted = dict(params)
        for key, val in list(encrypted.items()):
            from jiuwenswarm.extensions.registry import ExtensionRegistry
            if (("api_key" in key.lower() or "token" in key.lower())
                    and ExtensionRegistry.get_instance().get_crypto_provider()):
                encrypted[key] = ExtensionRegistry.get_instance().get_crypto_provider().encrypt(val)
        return encrypted

    def _apply_config_payload(params: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
        """Apply config.set-style payload to .env/config.yaml without triggering reload."""
        params = _encrypt_config_params(params)
        env_updates: dict[str, str] = {}
        yaml_updated: list[str] = []
        available_model_providers = [provider.value for provider in ProviderType]

        for param_key, env_key in _CONFIG_SET_ENV_MAP.items():
            if param_key not in params:
                continue
            val = params[param_key]
            if param_key.endswith("_provider") and val and val not in available_model_providers:
                raise _ConfigBadRequest(f"Model provider must in: {available_model_providers} ")
            if val is None:
                env_updates[env_key] = ""
            else:
                env_updates[env_key] = str(val).strip()

        raw = get_config_raw()
        preferred_lang = raw.get("preferred_language", "zh")

        if "agents" in params or "team" in params:
            try:
                replace_teams_in_config(params)
                yaml_updated.append("modes.team")
            except ValueError as exc:
                raise _ConfigBadRequest(str(exc)) from exc
            except Exception as exc:  # noqa: BLE001
                logger.warning("[config.set] 写回 modes.team 失败: %s", exc)
                raise _ConfigInternalError("failed to update modes.team") from exc

        for param_key in _CONFIG_YAML_KEYS:
            if param_key not in params:
                continue
            val = params[param_key]
            parsed = str(val).strip().lower() in ("true", "1", "yes")
            try:
                if param_key == "context_engine_enabled":
                    update_context_engine_enabled_in_config(parsed)
                elif param_key == "kv_cache_affinity_enabled":
                    update_kv_cache_affinity_enabled_in_config(parsed)
                elif param_key == "permissions_enabled":
                    update_permissions_enabled_in_config(parsed)
                elif param_key == "memory_forbidden_enabled":
                    update_memory_forbidden_enabled_in_config(parsed)
                elif param_key == "memory_forbidden_description":
                    desc_val = str(val).strip()
                    update_memory_forbidden_description_in_config({preferred_lang: desc_val})
                elif param_key.startswith("a2ui_"):
                    ok, update, error = validate_a2ui_config_update(param_key, val)
                    if not ok:
                        raise _ConfigBadRequest(error or "invalid A2UI config")
                    update_a2ui_in_config(update)
                yaml_updated.append(param_key)
            except Exception as e:  # noqa: BLE001
                logger.warning("[config.set] 写回 config.yaml 失败 %s: %s", param_key, e)

        symphony_updates = _build_symphony_config_update(params)
        if symphony_updates:
            try:
                update_symphony_in_config(symphony_updates)
                yaml_updated.extend(k for k in _SYMPHONY_CONFIG_KEYS if k in params)
            except Exception as e:
                logger.warning("[config.set] 写回 symphony 失败: %s", e)

        try:
            skill_retrieval_updates = _build_skill_retrieval_config_update(params)
        except ValueError as exc:
            raise _ConfigBadRequest(str(exc)) from exc
        if skill_retrieval_updates:
            try:
                update_skill_retrieval_in_config(skill_retrieval_updates)
                yaml_updated.extend(k for k in _SKILL_RETRIEVAL_CONFIG_KEYS if k in params)
            except Exception as e:
                logger.warning("[config.set] 写回 skill_retrieval 失败: %s", e)

        for env_key, value in env_updates.items():
            os.environ[env_key] = value
        if env_updates:
            _persist_env_updates(env_updates)
            logger.info("[config.set] 已更新 .env: %s", list(env_updates.keys()))
        if yaml_updated:
            logger.info("[config.set] 已更新 config.yaml: %s", yaml_updated)

        return env_updates, yaml_updated

    async def _notify_config_saved_once(
            env_updates: dict[str, str],
            yaml_updated: list[str],
            *,
            force: bool = False,
    ) -> None:
        """Trigger at most one hot reload after all file writes are complete."""
        if not force and not (env_updates or yaml_updated):
            return
        if on_config_saved:
            config_payload = get_config()
            callback_result = on_config_saved(
                set(env_updates.keys()) | set(yaml_updated),
                env_updates=dict(env_updates),
                config_payload=config_payload,
            )
            if inspect.isawaitable(callback_result):
                await callback_result
        else:
            await _clear_agent_config_cache(_resolve(agent_client))

    def _build_models_defaults_from_frontend(raw_models: Any) -> list[dict[str, Any]]:
        if not isinstance(raw_models, list) or not raw_models:
            raise _ConfigBadRequest("models must be a non-empty list")

        available_model_providers = [p.value for p in ProviderType]
        parsed: list[dict] = []
        aliases_seen: dict[str, int] = {}
        for idx, item in enumerate(raw_models):
            if not isinstance(item, dict):
                raise _ConfigBadRequest(f"models[{idx}] must be object")
            model_name = str(item.get("model_name") or "").strip()
            if not model_name:
                raise _ConfigBadRequest(f"models[{idx}].model_name is required")
            origin_index_raw = item.get("origin_index")
            if origin_index_raw is None:
                origin_index = None
            else:
                try:
                    origin_index = int(origin_index_raw)
                except (TypeError, ValueError):
                    origin_index = None
            api_key = str(item.get("api_key") or "").strip()
            # New entries must carry a non-empty api_key. Existing entries may legitimately
            # be empty when the source is ``${API_KEY:-}`` and the env var is unset; in that
            # case origin_index lets replace_all preserve the original placeholder.
            if not api_key and origin_index is None:
                raise _ConfigBadRequest(f"models[{idx}].api_key is required")
            api_base = str(item.get("api_base") or "").strip()
            model_provider = str(item.get("model_provider") or "").strip()
            if model_provider and model_provider not in available_model_providers:
                raise _ConfigBadRequest(f"models[{idx}].model_provider must be one of: {available_model_providers}")
            try:
                temperature = float(item.get("temperature", 0.95))
            except (ValueError, TypeError):
                temperature = 0.95
            try:
                timeout = int(item.get("timeout", 1800))
            except (ValueError, TypeError):
                timeout = 1800
            verify_ssl = bool(item.get("verify_ssl", False))
            is_default = bool(item.get("is_default", False))
            alias = str(item.get("alias") or "").strip()
            reasoning_level = str(item.get("reasoning_level") or "").strip()

            if alias:
                if alias in aliases_seen:
                    prev_idx = aliases_seen[alias]
                    raise _ConfigBadRequest(f"Alias '{alias}' is used by both models[{prev_idx}] and models[{idx}]")
                aliases_seen[alias] = idx

            parsed.append({
                "model_name": model_name,
                "api_base": api_base,
                "api_key": api_key,
                "model_provider": model_provider,
                "temperature": temperature,
                "is_default": is_default,
                "timeout": timeout,
                "verify_ssl": verify_ssl,
                "alias": alias,
                "reasoning_level": reasoning_level,
                "origin_index": origin_index,
            })

        # alias 与其他条目的 model_name 冲突校验
        for i, p in enumerate(parsed):
            a = p["alias"]
            if not a:
                continue
            for j, q in enumerate(parsed):
                if i == j:
                    continue
                if q["model_name"] == a:
                    raise _ConfigBadRequest(f"Alias '{a}' on models[{i}] conflicts with model_name on models[{j}]")

        from jiuwenswarm.extensions.registry import ExtensionRegistry
        crypto = ExtensionRegistry.get_instance().get_crypto_provider()

        raw_cfg = get_config_raw()
        raw_defaults = raw_cfg.get("models", {}).get("defaults") if isinstance(raw_cfg, dict) else None
        if not isinstance(raw_defaults, list):
            raw_defaults = []
        resolved_defaults = get_default_models()

        new_models = _merge_models_for_replace_all(parsed, raw_defaults, resolved_defaults, crypto)
        from jiuwenswarm.common.config import _infer_is_default
        return _infer_is_default(new_models)

    async def _config_set(ws, req_id, params, session_id):
        """根据前端消息内容更新配置（支持 .env 与 config.yaml 中的键），并写回对应文件。"""
        if not isinstance(params, dict):
            await channel.send_response(ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST")
            return
        try:
            env_updates, yaml_updated = _apply_config_payload(params)
        except _ConfigBadRequest as exc:
            await channel.send_response(ws, req_id, ok=False, error=str(exc), code="BAD_REQUEST")
            return
        except _ConfigInternalError as exc:
            await channel.send_response(ws, req_id, ok=False, error=str(exc), code="INTERNAL_ERROR")
            return
        applied_without_restart = True

        updated_param_keys = [k for k, e in _CONFIG_SET_ENV_MAP.items() if e in env_updates] + yaml_updated
        await channel.send_response(
            ws, req_id, ok=True,
            payload={"updated": updated_param_keys, "applied_without_restart": applied_without_restart},
        )

        if env_updates or yaml_updated:
            try:
                await _notify_config_saved_once(env_updates, yaml_updated)
            except Exception as e:  # noqa: BLE001
                logger.warning("[config.set] on_config_saved failed: %s", e)

    async def _config_validate_model(ws, req_id, params, session_id, max_tokens_bounds=None):
        """Send a minimal chat completion (user message \"Hi\") using draft default-model fields.

        Tries ``max_tokens=infimum_max_tokens`` first to limit cost; if the API rejects it (e.g. minimum output length),
        retries with ``max_tokens=supremum_max_tokens``.
        """
        if max_tokens_bounds is None:
            max_tokens_bounds = {
                "infimum_max_tokens": 1,
                "supremum_max_tokens": 16,
            }

        if isinstance(max_tokens_bounds, dict):
            infimum_max_tokens = max_tokens_bounds.get("infimum_max_tokens")
            supremum_max_tokens = max_tokens_bounds.get("supremum_max_tokens")
        else:
            infimum_max_tokens = 1
            supremum_max_tokens = 16

        if not isinstance(params, dict):
            await channel.send_response(ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST")
            return
        api_base = str(params.get("api_base") or "").strip()
        api_key = str(params.get("api_key") or "").strip()
        model = str(params.get("model") or "").strip()
        model_provider = str(params.get("model_provider") or "").strip()
        if not all([api_base, api_key, model, model_provider]):
            await channel.send_response(
                ws, req_id, ok=False,
                error="api_base, api_key, model, and model_provider are required",
                code="BAD_REQUEST",
            )
            return
        available_model_providers = [provider.value for provider in ProviderType]
        if model_provider not in available_model_providers:
            await channel.send_response(
                ws, req_id, ok=False,
                error=f"Model provider must be one of: {available_model_providers}",
                code="BAD_REQUEST",
            )
            return
        if api_base.endswith("/chat/completions"):
            api_base = api_base.rsplit("/chat/completions", 1)[0]
        api_base = api_base.rstrip("/")

        verify_ssl = bool(params.get("verify_ssl", False))

        model_config_obj = {"temperature": 0}
        if "reasoning_level" in params:
            model_config_obj["reasoning_level"] = params.get("reasoning_level")
        reasoning_mcc = {
            "client_provider": model_provider,
            "api_base": api_base,
        }
        model_request_config = ModelRequestConfig(
            **build_reasoning_model_request_kwargs(
                model_client_config=reasoning_mcc,
                model_config_obj=model_config_obj,
                model_name=model,
            )
        )
        model_client_config = ModelClientConfig(
            client_id="config-validate",
            client_provider=model_provider,
            api_key=api_key,
            api_base=api_base,
            timeout=25.0,
            max_retries=0,
            verify_ssl=verify_ssl,
        )
        llm = Model(model_config=model_request_config, model_client_config=model_client_config)

        async def test_invoke(max_tokens: int):
            return await llm.invoke(
                [{"role": "user", "content": "Hi"}],
                max_tokens=max_tokens,
                temperature=0,
            )

        try:
            try:
                resp = await test_invoke(infimum_max_tokens)
            except Exception as first_exc:  # noqa: BLE001
                logger.info(
                    "[config.validate_model] max_tokens=%d failed, retrying with %d: %s",
                    infimum_max_tokens,
                    supremum_max_tokens,
                    first_exc,
                )
                try:
                    resp = await test_invoke(supremum_max_tokens)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[config.validate_model] Testing LLM failed: %s", exc)
                    await channel.send_response(
                        ws, req_id, ok=False,
                        error=str(exc).strip() or "LLM request failed",
                        code="LLM_ERROR",
                    )
                    return
        except Exception as exc:  # noqa: BLE001
            logger.warning("[config.validate_model] LLM probe failed: %s", exc)
            await channel.send_response(
                ws, req_id, ok=False,
                error=str(exc).strip() or "LLM request failed",
                code="LLM_ERROR",
            )
            return

        if hasattr(resp, "content"):
            content = resp.content
        elif isinstance(resp, dict):
            content = resp.get("content", "")
        else:
            content = str(resp)
        # For reasoning models (e.g. deepseek-v4-flash), the model may put all
        # tokens into reasoning_content while leaving content empty.  Treat a
        # non-empty reasoning_content as a valid response as well.
        reasoning_content = getattr(resp, "reasoning_content", None) if hasattr(resp, "reasoning_content") else None
        has_valid_response = (isinstance(content, str) and content) or (
                isinstance(reasoning_content, str) and reasoning_content
        )
        if not has_valid_response:
            await channel.send_response(
                ws, req_id, ok=False,
                error="Empty response from model",
                code="LLM_ERROR",
            )
            return

        await channel.send_response(
            ws, req_id, ok=True,
            payload={"ok": True, "model_provider": model_provider},
        )

    # ── models.* handlers ────────────────────────────────────────

    async def _models_list(ws, req_id, params, session_id):
        """返回已配置的所有默认模型列表（与 config.get 一致，返回解密后的完整值）。

        每条带 ``origin_index`` 指向 ``models.defaults`` 中的位置，配合 replace_all
        在保存时识别"未编辑字段"并保留原 YAML 占位符（如 ``${API_KEY}``）。
        """
        try:
            config = get_config()
            models = get_default_models(config)
            result = []
            active_model = ""
            for idx, entry in enumerate(models):
                mcc = entry.get("model_client_config", {})
                mco = entry.get("model_config_obj", {})
                is_default = entry.get("is_default", False)
                model_name = mcc.get("model_name", "")
                context_window_tokens = 0
                try:
                    from openjiuwen.core.context_engine.context.context_utils import ContextUtils
                    context_window_tokens = ContextUtils.resolve_context_max(model_name=model_name)
                except Exception:
                    logger.debug(
                        "Failed to resolve context_window_tokens for model %s",
                        model_name,
                        exc_info=True,
                    )
                result.append({
                    "model_name": model_name,
                    "api_base": mcc.get("api_base", ""),
                    "api_key": mcc.get("api_key", ""),
                    "model_provider": mcc.get("client_provider", ""),
                    "temperature": mco.get("temperature", 0.95),
                    "reasoning_level": "off" if mco.get("reasoning_level") is False else mco.get("reasoning_level", ""),
                    "is_default": is_default,
                    "alias": entry.get("alias", ""),
                    "origin_index": idx,
                    "context_window_tokens": context_window_tokens,
                })
                # active_model 为列表首位的模型（主对话默认）
            active_model = result[0]["model_name"] if result else ""
            await channel.send_response(ws, req_id, ok=True, payload={
                "models": result,
                "active_model": active_model,
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning("[models.list] %s", exc)
            await channel.send_response(ws, req_id, ok=False, error=str(exc), code="INTERNAL_ERROR")

    async def _models_replace_all(ws, req_id, params, session_id):
        """原子地用提交的列表整体替换 models.defaults。

        前端在保存配置时一次性提交完整的最终列表，避免按 model_name/index 分多步
        save+remove 在同 model_name 多条目场景下出现的位置覆写、漏删等问题。

        每条 entry 可携带 ``origin_index`` 指向 ``models.defaults`` 中的原始位置；
        命中后 raw YAML 中的占位符（如 ``${API_KEY}``）以及 custom_headers 等未在
        前端暴露的字段会被保留，仅当字段值与前端最初看到的解析值不一致时才覆写。
        """
        if not isinstance(params, dict):
            await channel.send_response(ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST")
            return
        try:
            new_models = _build_models_defaults_from_frontend(params.get("models"))
            update_default_models_in_config(new_models)

            await _notify_config_saved_once({}, ["models.defaults"], force=True)

            await channel.send_response(ws, req_id, ok=True, payload={
                "count": len(new_models),
            })
        except _ConfigBadRequest as exc:
            await channel.send_response(ws, req_id, ok=False, error=str(exc), code="BAD_REQUEST")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[models.replace_all] %s", exc)
            await channel.send_response(ws, req_id, ok=False, error=str(exc), code="INTERNAL_ERROR")

    async def _config_save_all(ws, req_id, params, session_id):
        """Batch-save config panel changes and trigger a single hot reload.

        Accepted payload keys:
        - config: config.set-style key/value updates
        - models: complete models.defaults draft list
        - agents/team: team editor payload
        """
        if not isinstance(params, dict):
            await channel.send_response(ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST")
            return

        env_updates: dict[str, str] = {}
        yaml_updated: list[str] = []
        models_count: int | None = None

        try:
            new_models: list[dict[str, Any]] | None = None
            if "models" in params:
                new_models = _build_models_defaults_from_frontend(params.get("models"))

            config_params: dict[str, Any] = {}
            raw_config_params = params.get("config")
            if raw_config_params is not None:
                if not isinstance(raw_config_params, dict):
                    raise _ConfigBadRequest("config must be object")
                config_params.update(raw_config_params)

            if "agents" in params:
                config_params["agents"] = params.get("agents")
            if "team" in params:
                config_params["team"] = params.get("team")

            if config_params:
                applied_env, applied_yaml = _apply_config_payload(config_params)
                env_updates.update(applied_env)
                yaml_updated.extend(applied_yaml)

            if new_models is not None:
                update_default_models_in_config(new_models)
                yaml_updated.append("models.defaults")
                models_count = len(new_models)

            await channel.send_response(
                ws,
                req_id,
                ok=True,
                payload={
                    "updated": [k for k, e in _CONFIG_SET_ENV_MAP.items() if e in env_updates] + yaml_updated,
                    "applied_without_restart": True,
                    "models_count": models_count,
                },
            )

            if env_updates or yaml_updated:
                try:
                    await _notify_config_saved_once(env_updates, yaml_updated, force=True)
                except Exception as e:  # noqa: BLE001
                    logger.warning("[config.save_all] on_config_saved failed: %s", e)
        except _ConfigBadRequest as exc:
            await channel.send_response(ws, req_id, ok=False, error=str(exc), code="BAD_REQUEST")
        except _ConfigInternalError as exc:
            await channel.send_response(ws, req_id, ok=False, error=str(exc), code="INTERNAL_ERROR")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[config.save_all] %s", exc)
            await channel.send_response(ws, req_id, ok=False, error=str(exc), code="INTERNAL_ERROR")

    async def _models_validate(ws, req_id, params, session_id):
        """测试指定模型配置是否可用（复用 config.validate_model 逻辑）。"""
        await _config_validate_model(ws, req_id, params, session_id)

    async def _channel_get(ws, req_id, params, session_id):
        """返回已注册的 channel 列表."""
        cm = _resolve(channel_manager)
        if cm is not None:
            channels = [{"channel_id": cid} for cid in cm.enabled_channels]
        else:
            channels = []
        await channel.send_response(ws, req_id, ok=True, payload={"channels": channels})

    async def _updater_get_status(ws, req_id, params, session_id):
        service = updater_service or UpdaterService()
        await channel.send_response(ws, req_id, ok=True, payload=service.get_status())

    async def _updater_check(ws, req_id, params, session_id):
        service = updater_service or UpdaterService()
        manual = bool((params or {}).get("manual", False)) if isinstance(params, dict) else False
        payload = await asyncio.to_thread(service.check, manual)
        await channel.send_response(ws, req_id, ok=True, payload=payload)

    async def _updater_download(ws, req_id, params, session_id):
        service = updater_service or UpdaterService()
        payload = service.start_download()
        await channel.send_response(ws, req_id, ok=True, payload=payload)

    async def _updater_upgrade(ws, req_id, params, session_id):
        service = updater_service or UpdaterService()
        payload = await asyncio.to_thread(service.start_upgrade)
        await channel.send_response(ws, req_id, ok=True, payload=payload)

    async def _updater_get_conf(ws, req_id, params, session_id):
        service = updater_service or UpdaterService()
        await channel.send_response(ws, req_id, ok=True, payload=service.get_runtime_config())

    async def _updater_set_conf(ws, req_id, params, session_id):
        if not isinstance(params, dict):
            await channel.send_response(ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST")
            return

        updates: dict[str, Any] = {}
        if "enabled" in params:
            updates["enabled"] = bool(params.get("enabled"))
        for key in ("repo_owner", "repo_name", "release_api_url", "asset_name_pattern",
                "release_api_type", "pypi_mirror"):
            if key in params:
                updates[key] = str(params.get(key) or "").strip()
        for plat in ("windows", "macos", "linux"):
            key = f"asset_name_pattern_{plat}"
            if key in params:
                updates[key] = str(params.get(key) or "").strip()
        if "timeout_seconds" in params:
            try:
                updates["timeout_seconds"] = max(5, int(params.get("timeout_seconds")))
            except (TypeError, ValueError):
                await channel.send_response(ws, req_id, ok=False,
                                            error="timeout_seconds must be integer", code="BAD_REQUEST")
                return

        try:
            update_updater_in_config(updates)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[updater.set_conf] 写回 config.yaml 失败: %s", exc)
            await channel.send_response(ws, req_id, ok=False,
                                        error=str(exc), code="INTERNAL_ERROR")
            return

        service = updater_service or UpdaterService()
        await channel.send_response(ws, req_id, ok=True, payload=service.get_runtime_config())

    async def _session_list(ws, req_id, params, session_id):
        """返回会话列表,包含完整的会话管理信息。"""
        limit = 20
        offset = 0
        if isinstance(params, dict):
            raw_limit = params.get("limit")
            if isinstance(raw_limit, int):
                limit = raw_limit
            elif isinstance(raw_limit, str) and raw_limit.strip().isdigit():
                limit = int(raw_limit.strip())

            raw_offset = params.get("offset")
            if isinstance(raw_offset, int):
                offset = raw_offset
            elif isinstance(raw_offset, str) and raw_offset.strip().isdigit():
                offset = int(raw_offset.strip())

        limit = max(1, min(limit, 200))
        offset = max(0, offset)

        from jiuwenswarm.server.runtime.session.session_metadata import get_all_sessions_metadata

        sessions, total = get_all_sessions_metadata(limit=limit, offset=offset)

        await channel.send_response(ws, req_id, ok=True, payload={
            "sessions": sessions,
            "total": total,
            "limit": limit,
            "offset": offset,
        })

    async def _session_create(ws, req_id, params, session_id):
        """创建一个新 session（在 agent/sessions 下创建一个新目录）。"""
        if not isinstance(params, dict):
            await channel.send_response(
                ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST",
            )
            return
        session_id_to_create = params.get("session_id")
        if not isinstance(session_id_to_create, str) or not session_id_to_create.strip():
            await channel.send_response(
                ws, req_id, ok=False, error="session_id is required", code="BAD_REQUEST",
            )
            return
        session_id_to_create = session_id_to_create.strip()

        workspace_session_dir = get_agent_sessions_dir()
        if not workspace_session_dir.exists():
            workspace_session_dir.mkdir(parents=True)
        session_dir = workspace_session_dir / session_id_to_create
        if session_dir.exists():
            await channel.send_response(
                ws, req_id, ok=False, error="session already exists", code="ALREADY_EXISTS",
            )
            return
        session_dir.mkdir()

        # 初始化会话元数据
        from jiuwenswarm.server.runtime.session.session_metadata import init_session_metadata
        init_session_metadata(
            session_id=session_id_to_create,
            channel_id=params.get("channel_id", ""),
            user_id=params.get("user_id", ""),
            title=params.get("title", ""),
            mode=params.get("mode", "unknown"),
        )

        await channel.send_response(ws, req_id, ok=True, payload={"session_id": session_id_to_create})

    async def _session_delete(ws, req_id, params, session_id):
        """删除一个 session（在 agent/sessions 下删除一个目录）。"""
        if not isinstance(params, dict):
            await channel.send_response(
                ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST",
            )
            return
        session_id_to_delete = params.get("session_id")
        if not isinstance(session_id_to_delete, str) or not session_id_to_delete.strip():
            await channel.send_response(
                ws, req_id, ok=False, error="session_id is required", code="BAD_REQUEST",
            )
            return
        session_id_to_delete = session_id_to_delete.strip()

        from jiuwenswarm.server.runtime.session.session_metadata import get_session_metadata
        from jiuwenswarm.common.e2a.gateway_normalize import e2a_from_agent_fields
        from jiuwenswarm.common.schema.message import ReqMethod

        ac = _resolve(agent_client)
        if ac is not None and getattr(ac, "server_ready", False):
            try:
                env = e2a_from_agent_fields(
                    request_id=str(req_id) if req_id else "",
                    channel_id="",
                    session_id=session_id,
                    req_method=ReqMethod.SESSION_DELETE,
                    params=params,
                )
                resp = await ac.send_request(env)
                if resp.ok:
                    pl = resp.payload if isinstance(resp.payload, dict) else {}
                    await channel.send_response(ws, req_id, ok=True, payload=pl)
                    return
                pl = resp.payload if isinstance(resp.payload, dict) else {}
                await channel.send_response(
                    ws,
                    req_id,
                    ok=False,
                    error=str(pl.get("error", "session.delete failed")),
                    code=pl.get("code"),
                )
                return
            except Exception as e:  # noqa: BLE001
                logger.warning("[session.delete] forward to agent failed, fallback local: %s", e)

        metadata = get_session_metadata(session_id_to_delete)
        if str(metadata.get("mode") or "").strip().lower() == "team":
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="team session delete requires agent server",
                code="AGENT_UNAVAILABLE",
            )
            return

        workspace_session_dir = get_agent_sessions_dir()
        session_dir = workspace_session_dir / session_id_to_delete
        if not session_dir.exists():
            await channel.send_response(
                ws, req_id, ok=False, error="session not found", code="NOT_FOUND",
            )
            return
        if not session_dir.is_dir():
            await channel.send_response(
                ws, req_id, ok=False, error="session is not a directory", code="BAD_REQUEST",
            )
            return
        shutil.rmtree(session_dir)
        await channel.send_response(ws, req_id, ok=True, payload={"session_id": session_id_to_delete})

    async def _path_get(ws, req_id, params, session_id):
        """读 browser.chrome_path 并返回给前端（会解析环境变量）。"""
        try:
            config_base = get_config()
        except FileNotFoundError:
            await channel.send_response(
                ws,
                req_id,
                ok=True,
                payload={"chrome_path": "", "headless": True},
            )
            return

        if not isinstance(config_base, dict):
            config_base = {}

        config = _resolve_env_vars(config_base)
        browser_cfg = config.get("browser", {}) if isinstance(config, dict) else {}
        chrome_path = ""
        headless = True
        if isinstance(browser_cfg, dict):
            value = browser_cfg.get("chrome_path", "")
            if isinstance(value, str):
                chrome_path = value
            raw_headless = browser_cfg.get("headless", True)
            headless = bool(raw_headless) if isinstance(raw_headless, bool) else True

        await channel.send_response(ws, req_id, ok=True, payload={"chrome_path": chrome_path, "headless": headless})

    async def _path_set(ws, req_id, params, session_id):
        """更新 browser.chrome_path / browser.headless 并写回 config。"""
        if not isinstance(params, dict):
            await channel.send_response(ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST")
            return

        chrome_path = params.get("chrome_path")
        if not isinstance(chrome_path, str):
            await channel.send_response(ws, req_id, ok=False, error="chrome_path must be string", code="BAD_REQUEST")
            return
        chrome_path = chrome_path.strip()

        raw_headless = params.get("headless", True)
        headless = bool(raw_headless) if isinstance(raw_headless, bool) else True

        try:
            update_browser_in_config({"chrome_path": chrome_path, "headless": headless})
            await _clear_agent_config_cache(_resolve(agent_client))
        except Exception as e:  # noqa: BLE001
            logger.warning("[path.set] 写回 config.yaml 失败: %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")
            return

        # When switching to headless, purge any persisted headed-Chrome profile so the
        # managed driver doesn't reuse an existing visible window on the next browser task.
        if headless:
            try:
                from pathlib import Path as _Path
                _profile_store = _Path(
                    os.getenv("BROWSER_PROFILE_STORE_PATH", "").strip()
                    or str(get_user_workspace_dir() / ".browser" / "profiles.json")
                ).expanduser()
                if _profile_store.exists():
                    _profile_store.unlink()
                    logger.info("[path.set] Cleared browser profile store for headless mode: %s", _profile_store)
            except Exception as _e:
                logger.debug("[path.set] Could not clear browser profile store: %s", _e)

        await channel.send_response(ws, req_id, ok=True, payload={"chrome_path": chrome_path, "headless": headless})

    async def _memory_compute(ws, req_id, params, session_id):

        process = psutil.Process()
        rss_bytes = process.memory_info().rss  # 物理内存
        rss_mb = rss_bytes / (1024 * 1024)

        mem = psutil.virtual_memory()
        total_mb = mem.total / (1024 * 1024)
        available_mb = mem.available / (1024 * 1024)
        used_percent = mem.percent

        await channel.send_response(ws, req_id, ok=True,
                                    payload={"rss_mb": rss_mb, "total_mb": total_mb,
                                             "available_mb": available_mb})

    async def _chat_send(ws, req_id, params, session_id):
        await channel.send_response(
            ws,
            req_id,
            ok=True,
            payload={"accepted": True, "session_id": session_id},
        )

    async def _chat_resume(ws, req_id, params, session_id):
        await channel.send_response(
            ws,
            req_id,
            ok=True,
            payload={"accepted": True, "session_id": session_id},
        )

    async def _chat_interrupt(ws, req_id, params, session_id):
        intent = params.get("intent") if isinstance(params, dict) else None
        payload = {"accepted": True, "session_id": session_id}
        if isinstance(intent, str) and intent:
            payload["intent"] = intent
        await channel.send_response(ws, req_id, ok=True, payload=payload)

    async def _chat_user_answer(ws, req_id, params, session_id):
        payload = {"accepted": True, "session_id": session_id}
        request_id = params.get("request_id") if isinstance(params, dict) else None
        if isinstance(request_id, str) and request_id:
            payload["request_id"] = request_id
        await channel.send_response(ws, req_id, ok=True, payload=payload)

    async def _history_get(ws, req_id, params, session_id):
        payload = {"accepted": True, "session_id": session_id}
        if isinstance(params, dict):
            if "session_id" in params:
                payload["session_id"] = params.get("session_id")
            if "page_idx" in params:
                payload["page_idx"] = params.get("page_idx")
        await channel.send_response(ws, req_id, ok=True, payload=payload)

    async def _locale_get_conf(ws, req_id, params, session_id):
        """返回当前 preferred_language 配置（zh / en）。"""
        try:
            cfg = get_config()
            lang = str(cfg.get("preferred_language") or "zh").strip().lower()
            if lang not in ("zh", "en"):
                lang = "zh"
            await channel.send_response(
                ws,
                req_id,
                ok=True,
                payload={"preferred_language": lang}
            )
        except Exception as e:
            logger.exception("[locale.get_conf] %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    async def _locale_set_conf(ws, req_id, params, session_id):
        """更新 preferred_language 并写回 config.yaml。"""
        if not isinstance(params, dict):
            await channel.send_response(ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST")
            return
        lang_raw = params.get("preferred_language")
        if not isinstance(lang_raw, str):
            await channel.send_response(
                ws, req_id, ok=False, error="preferred_language must be string", code="BAD_REQUEST"
            )
            return
        lang = lang_raw.strip().lower()
        if lang not in ("zh", "en"):
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="preferred_language must be zh or en",
                code="BAD_REQUEST"
            )
            return
        try:
            update_preferred_language_in_config(lang)
            await channel.send_response(ws, req_id, ok=True, payload={"preferred_language": lang})
        except Exception as e:
            logger.warning("[locale.set_conf] 写回 config.yaml 失败: %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    async def _heartbeat_get_conf(ws, req_id, params, session_id):
        """返回当前心跳配置（every / target / active_hours）。"""
        hb = _resolve(heartbeat_service)
        if hb is None:
            await channel.send_response(ws, req_id, ok=False, error="heartbeat service not available",
                                        code="SERVICE_UNAVAILABLE")
            return
        try:
            payload = dict(hb.get_heartbeat_conf())
            await channel.send_response(ws, req_id, ok=True, payload=payload)
        except Exception as e:
            logger.exception("[heartbeat.get_conf] %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    async def _heartbeat_set_conf(ws, req_id, params, session_id):
        """更新心跳配置并重启心跳服务；params 可含 every、target、active_hours。"""
        hb = _resolve(heartbeat_service)
        if hb is None:
            await channel.send_response(ws, req_id, ok=False, error="heartbeat service not available",
                                        code="SERVICE_UNAVAILABLE")
            return
        if not isinstance(params, dict):
            await channel.send_response(ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST")
            return
        try:
            every = params.get("every")
            target = params.get("target")
            active_hours = params.get("active_hours")
            if every is not None:
                every = float(every)
            if target is not None:
                target = str(target)
            if active_hours is not None:
                if not isinstance(active_hours, dict):
                    active_hours = None
                elif active_hours and ("start" not in active_hours or "end" not in active_hours):
                    # 必须同时包含 start/end，否则视为清除时间段（始终生效）
                    active_hours = None

            # 先检查：如果目标渠道是飞书，检测是否有可用的推送目标
            if target == "feishu":
                try:
                    raw = get_config_raw() or {}
                    ch_cfg = (raw.get("channels") or {}).get("feishu") or {}
                    has_target = bool(
                        str(ch_cfg.get("last_chat_id") or "").strip()
                        or str(ch_cfg.get("chat_id") or "").strip()
                    )
                    if not has_target:
                        await channel.send_response(
                            ws, req_id, ok=False,
                            error="feishuNoTarget",
                            code="feishuNoTarget",
                        )
                        return
                except Exception as e:
                    logger.debug("[heartbeat.set_conf] 飞书目标检测异常: %s", e)
                    await channel.send_response(
                        ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR",
                    )
                    return

            # 检查通过后再保存配置
            await hb.set_heartbeat_conf(every=every, target=target, active_hours=active_hours)
            payload = dict(hb.get_heartbeat_conf())
            should_clear_agent_config_cache = False
            try:
                update_heartbeat_in_config(payload)
                should_clear_agent_config_cache = True
            except Exception as e:  # noqa: BLE001
                logger.warning("[heartbeat.set_conf] 写回 config.yaml 失败: %s", e)
            try:
                await channel.send_response(ws, req_id, ok=True, payload=payload)
            finally:
                if should_clear_agent_config_cache:
                    _schedule_clear_agent_config_cache("heartbeat.set_conf")
        except ValueError as e:
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="BAD_REQUEST")
        except Exception as e:
            logger.exception("[heartbeat.set_conf] %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    async def _heartbeat_get_path(ws, req_id, params, session_id):
        """返回 HEARTBEAT.md 文件路径。"""
        from jiuwenswarm.common.utils import get_deepagent_heartbeat_path, get_agent_root_dir

        try:
            heartbeat_path = get_deepagent_heartbeat_path()
            # 返回相对于 agent 根目录的路径，与 file-api 格式一致
            agent_root = get_agent_root_dir()
            relative_path = heartbeat_path.relative_to(agent_root.parent)
            await channel.send_response(
                ws, req_id, ok=True,
                payload={"path": str(relative_path)}
            )
        except Exception as e:
            logger.exception("[heartbeat.get_path] %s", e)
            await channel.send_response(
                ws, req_id, ok=False,
                error=str(e), code="INTERNAL_ERROR"
            )

    async def _channel_feishu_get_conf(ws, req_id, params, session_id):
        """返回 FeishuChannel 的当前配置（由 ChannelManager 管理）。"""
        cm = _resolve(channel_manager)
        if cm is None:
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="channel manager not available",
                code="SERVICE_UNAVAILABLE",
            )
            return
        try:
            conf = cm.get_conf("feishu")
            await channel.send_response(ws, req_id, ok=True, payload={"config": conf})
        except Exception as e:  # noqa: BLE001
            logger.exception("[channel.feishu.get_conf] %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    async def _channel_feishu_set_conf(ws, req_id, params, session_id):
        """更新 FeishuChannel 的配置，并按新配置重新实例化通道。"""
        cm = _resolve(channel_manager)
        if cm is None:
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="channel manager not available",
                code="SERVICE_UNAVAILABLE",
            )
            return
        if not isinstance(params, dict):
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="params must be object",
                code="BAD_REQUEST",
            )
            return
        try:
            await cm.set_conf("feishu", params)
            conf = cm.get_conf("feishu")
            should_clear_agent_config_cache = False
            try:
                update_channel_in_config("feishu", conf)
                should_clear_agent_config_cache = True
            except Exception as e:  # noqa: BLE001
                logger.warning("[channel.feishu.set_conf] 写回 config.yaml 失败: %s", e)
            try:
                await channel.send_response(ws, req_id, ok=True, payload={"config": conf})
            finally:
                if should_clear_agent_config_cache:
                    _schedule_clear_agent_config_cache("channel.feishu.set_conf")
        except Exception as e:  # noqa: BLE001
            logger.exception("[channel.feishu.set_conf] %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    async def _channel_xiaoyi_get_conf(ws, req_id, params, session_id):
        """返回 XiaoyiChannel 的当前配置（由 ChannelManager 管理）。"""
        cm = _resolve(channel_manager)
        if cm is None:
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="channel manager not available",
                code="SERVICE_UNAVAILABLE",
            )
            return
        try:
            conf = cm.get_conf("xiaoyi")
            await channel.send_response(ws, req_id, ok=True, payload={"config": conf})
        except Exception as e:  # noqa: BLE001
            logger.exception("[channel.xiaoyi.get_conf] %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    async def _channel_xiaoyi_set_conf(ws, req_id, params, session_id):
        """更新 XiaoyiChannel 的配置，并按新配置重新实例化通道。"""
        cm = _resolve(channel_manager)
        if cm is None:
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="channel manager not available",
                code="SERVICE_UNAVAILABLE",
            )
            return
        if not isinstance(params, dict):
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="params must be object",
                code="BAD_REQUEST",
            )
            return
        try:
            await cm.set_conf("xiaoyi", params)
            conf = cm.get_conf("xiaoyi")
            try:
                update_channel_in_config("xiaoyi", conf)
                await _clear_agent_config_cache(_resolve(agent_client))
            except Exception as e:  # noqa: BLE001
                logger.warning("[channel.xiaoyi.set_conf] 写回 config.yaml 失败: %s", e)
            await channel.send_response(ws, req_id, ok=True, payload={"config": conf})
        except Exception as e:  # noqa: BLE001
            logger.exception("[channel.xiaoyi.set_conf] %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    async def _channel_telegram_get_conf(ws, req_id, params, session_id):
        """返回 TelegramChannel 的当前配置（由 ChannelManager 管理）。"""
        cm = _resolve(channel_manager)
        if cm is None:
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="channel manager not available",
                code="SERVICE_UNAVAILABLE",
            )
            return
        try:
            conf = cm.get_conf("telegram")
            await channel.send_response(ws, req_id, ok=True, payload={"config": conf})
        except Exception as e:  # noqa: BLE001
            logger.exception("[channel.telegram.get_conf] %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    async def _channel_telegram_set_conf(ws, req_id, params, session_id):
        """更新 TelegramChannel 的配置，并按新配置重新实例化通道。"""
        cm = _resolve(channel_manager)
        if cm is None:
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="channel manager not available",
                code="SERVICE_UNAVAILABLE",
            )
            return
        if not isinstance(params, dict):
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="params must be object",
                code="BAD_REQUEST",
            )
            return
        try:
            await cm.set_conf("telegram", params)
            conf = cm.get_conf("telegram")
            try:
                update_channel_in_config("telegram", conf)
                await _clear_agent_config_cache(_resolve(agent_client))
            except Exception as e:  # noqa: BLE001
                logger.warning("[channel.telegram.set_conf] 写回 config.yaml 失败: %s", e)
            await channel.send_response(ws, req_id, ok=True, payload={"config": conf})
        except Exception as e:  # noqa: BLE001
            logger.exception("[channel.telegram.set_conf] %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    async def _channel_dingtalk_get_conf(ws, req_id, params, session_id):
        cm = _resolve(channel_manager)
        if cm is None:
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="channel manager not available",
                code="SERVICE_UNAVAILABLE",
            )
            return
        try:
            conf = cm.get_conf("dingtalk")
            await channel.send_response(ws, req_id, ok=True, payload={"config": conf})
        except Exception as e:  # noqa: BLE001
            logger.exception("[channel.dingtalk.get_conf] %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    async def _channel_dingtalk_set_conf(ws, req_id, params, session_id):
        cm = _resolve(channel_manager)
        if cm is None:
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="channel manager not available",
                code="SERVICE_UNAVAILABLE",
            )
            return
        if not isinstance(params, dict):
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="params must be object",
                code="BAD_REQUEST",
            )
            return
        try:
            await cm.set_conf("dingtalk", params)
            conf = cm.get_conf("dingtalk")
            should_clear_agent_config_cache = False
            try:
                update_channel_in_config("dingtalk", conf)
                should_clear_agent_config_cache = True
            except Exception as e:  # noqa: BLE001
                logger.warning("[channel.dingtalk.set_conf] 写回 config.yaml 失败: %s", e)
            try:
                await channel.send_response(ws, req_id, ok=True, payload={"config": conf})
            finally:
                if should_clear_agent_config_cache:
                    _schedule_clear_agent_config_cache("channel.dingtalk.set_conf")
        except Exception as e:  # noqa: BLE001
            logger.exception("[channel.dingtalk.set_conf] %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    async def _channel_whatsapp_get_conf(ws, req_id, params, session_id):
        cm = _resolve(channel_manager)
        if cm is None:
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="channel manager not available",
                code="SERVICE_UNAVAILABLE",
            )
            return
        try:
            conf = cm.get_conf("whatsapp")
            await channel.send_response(ws, req_id, ok=True, payload={"config": conf})
        except Exception as e:  # noqa: BLE001
            logger.exception("[channel.whatsapp.get_conf] %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    async def _channel_whatsapp_set_conf(ws, req_id, params, session_id):
        cm = _resolve(channel_manager)
        if cm is None:
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="channel manager not available",
                code="SERVICE_UNAVAILABLE",
            )
            return
        if not isinstance(params, dict):
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="params must be object",
                code="BAD_REQUEST",
            )
            return
        try:
            await cm.set_conf("whatsapp", params)
            conf = cm.get_conf("whatsapp")
            try:
                update_channel_in_config("whatsapp", conf)
                await _clear_agent_config_cache(_resolve(agent_client))
            except Exception as e:  # noqa: BLE001
                logger.warning("[channel.whatsapp.set_conf] 写回 config.yaml 失败: %s", e)
            await channel.send_response(ws, req_id, ok=True, payload={"config": conf})
        except Exception as e:  # noqa: BLE001
            logger.exception("[channel.whatsapp.set_conf] %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    async def _channel_discord_get_conf(ws, req_id, params, session_id):
        cm = _resolve(channel_manager)
        if cm is None:
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="channel manager not available",
                code="SERVICE_UNAVAILABLE",
            )
            return
        try:
            conf = cm.get_conf("discord")
            await channel.send_response(ws, req_id, ok=True, payload={"config": conf})
        except Exception as e:  # noqa: BLE001
            logger.exception("[channel.discord.get_conf] %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    async def _channel_discord_set_conf(ws, req_id, params, session_id):
        cm = _resolve(channel_manager)
        if cm is None:
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="channel manager not available",
                code="SERVICE_UNAVAILABLE",
            )
            return
        if not isinstance(params, dict):
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="params must be object",
                code="BAD_REQUEST",
            )
            return
        try:
            await cm.set_conf("discord", params)
            conf = cm.get_conf("discord")
            try:
                update_channel_in_config("discord", conf)
                await _clear_agent_config_cache(_resolve(agent_client))
            except Exception as e:  # noqa: BLE001
                logger.warning("[channel.discord.set_conf] 写回 config.yaml 失败: %s", e)
            await channel.send_response(ws, req_id, ok=True, payload={"config": conf})
        except Exception as e:  # noqa: BLE001
            logger.exception("[channel.discord.set_conf] %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    async def _channel_wecom_get_conf(ws, req_id, params, session_id):
        cm = _resolve(channel_manager)
        if cm is None:
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="channel manager not available",
                code="SERVICE_UNAVAILABLE",
            )
            return
        try:
            conf = cm.get_conf("wecom")
            await channel.send_response(ws, req_id, ok=True, payload={"config": conf})
        except Exception as e:  # noqa: BLE001
            logger.exception("[channel.wecom.get_conf] %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    async def _channel_wecom_set_conf(ws, req_id, params, session_id):
        cm = _resolve(channel_manager)
        if cm is None:
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="channel manager not available",
                code="SERVICE_UNAVAILABLE",
            )
            return
        if not isinstance(params, dict):
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="params must be object",
                code="BAD_REQUEST",
            )
            return
        try:
            await cm.set_conf("wecom", params)
            conf = cm.get_conf("wecom")
            try:
                update_channel_in_config("wecom", conf)
                await _clear_agent_config_cache(_resolve(agent_client))
            except Exception as e:  # noqa: BLE001
                logger.warning("[channel.wecom.set_conf] 写回 config.yaml 失败: %s", e)
            await channel.send_response(ws, req_id, ok=True, payload={"config": conf})
        except Exception as e:  # noqa: BLE001
            logger.exception("[channel.wecom.set_conf] %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    async def _channel_wechat_get_conf(ws, req_id, params, session_id):
        cm = _resolve(channel_manager)
        if cm is None:
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="channel manager not available",
                code="SERVICE_UNAVAILABLE",
            )
            return
        try:
            conf = cm.get_conf("wechat")
            await channel.send_response(ws, req_id, ok=True, payload={"config": conf})
        except Exception as e:  # noqa: BLE001
            logger.exception("[channel.wechat.get_conf] %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    async def _channel_wechat_set_conf(ws, req_id, params, session_id):
        cm = _resolve(channel_manager)
        if cm is None:
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="channel manager not available",
                code="SERVICE_UNAVAILABLE",
            )
            return
        if not isinstance(params, dict):
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="params must be object",
                code="BAD_REQUEST",
            )
            return
        try:
            await cm.set_conf("wechat", params)
            conf = cm.get_conf("wechat")
            try:
                update_channel_in_config("wechat", conf)
                await _clear_agent_config_cache(_resolve(agent_client))
            except Exception as e:  # noqa: BLE001
                logger.warning("[channel.wechat.set_conf] 写回 config.yaml 失败: %s", e)
            await channel.send_response(ws, req_id, ok=True, payload={"config": conf})
        except Exception as e:  # noqa: BLE001
            logger.exception("[channel.wechat.set_conf] %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    async def _channel_wechat_get_login_ui(ws, req_id, params, session_id):
        from jiuwenswarm.gateway.channel_manager.im_platforms.wechat.wechat_connect import (
            snapshot_wechat_login_ui_state,
        )

        try:
            ui = await snapshot_wechat_login_ui_state()
            if "updated_at" in ui and isinstance(ui["updated_at"], (int, float)):
                ui["updated_at"] = int(ui["updated_at"])
            await channel.send_response(ws, req_id, ok=True, payload=ui)
        except Exception as e:  # noqa: BLE001
            logger.exception("[channel.wechat.get_login_ui] %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    async def _channel_wechat_unbind(ws, req_id, params, session_id):
        cm = _resolve(channel_manager)
        if cm is None:
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="channel manager not available",
                code="SERVICE_UNAVAILABLE",
            )
            return
        try:
            from jiuwenswarm.gateway.channel_manager.im_platforms.wechat.wechat_connect import \
                clear_wechat_bound_session, reset_wechat_login_ui_state

            conf = cm.get_conf("wechat")
            new_conf = clear_wechat_bound_session(conf)
            await reset_wechat_login_ui_state()
            # 若 YAML 里 bot_token 本就为空，仅删凭据文件时 dict 与上次相同，_should_restart_channel 不会重启，扫码 UI 会一直停在 idle
            cm.mark_channel_restart_pending("wechat")
            await cm.set_conf("wechat", new_conf)
            final = cm.get_conf("wechat")
            try:
                update_channel_in_config("wechat", final)
                await _clear_agent_config_cache(_resolve(agent_client))
            except Exception as e:  # noqa: BLE001
                logger.warning("[channel.wechat.unbind] 写回 config.yaml 失败: %s", e)
            await channel.send_response(ws, req_id, ok=True, payload={"config": final})
        except Exception as e:  # noqa: BLE001
            logger.exception("[channel.wechat.unbind] %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    # ----- cron jobs -----

    def _get_cron():
        return _resolve(cron_controller)

    async def _cron_job_list(ws, req_id, params, session_id):
        cc = _get_cron()
        if cc is None:
            await channel.send_response(ws, req_id, ok=False, error="cron not available", code="INTERNAL_ERROR")
            return
        jobs = await cc.list_jobs()
        await channel.send_response(ws, req_id, ok=True, payload={"jobs": jobs})

    async def _cron_job_meta(ws, req_id, params, session_id):
        cc = _get_cron()
        if cc is None:
            await channel.send_response(ws, req_id, ok=False, error="cron not available", code="INTERNAL_ERROR")
            return
        await channel.send_response(ws, req_id, ok=True, payload=cc.job_metadata())

    async def _cron_job_get(ws, req_id, params, session_id):
        cc = _get_cron()
        if cc is None:
            await channel.send_response(ws, req_id, ok=False, error="cron not available", code="INTERNAL_ERROR")
            return
        if not isinstance(params, dict):
            await channel.send_response(ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST")
            return
        job_id = str(params.get("id") or "").strip()
        if not job_id:
            await channel.send_response(ws, req_id, ok=False, error="id is required", code="BAD_REQUEST")
            return
        job = await cc.get_job(job_id)
        if job is None:
            await channel.send_response(ws, req_id, ok=False, error="job not found", code="NOT_FOUND")
            return
        await channel.send_response(ws, req_id, ok=True, payload={"job": job})

    async def _cron_job_create(ws, req_id, params, session_id):
        cc = _get_cron()
        if cc is None:
            await channel.send_response(ws, req_id, ok=False, error="cron not available", code="INTERNAL_ERROR")
            return
        if not isinstance(params, dict):
            await channel.send_response(ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST")
            return
        try:
            if session_id:
                params["session_id"] = session_id
            job = await cc.create_job(params)
            await channel.send_response(ws, req_id, ok=True, payload={"job": job})
        except Exception as e:  # noqa: BLE001
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="BAD_REQUEST")

    async def _cron_job_update(ws, req_id, params, session_id):
        cc = _get_cron()
        if cc is None:
            await channel.send_response(ws, req_id, ok=False, error="cron not available", code="INTERNAL_ERROR")
            return
        if not isinstance(params, dict):
            await channel.send_response(ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST")
            return
        job_id = str(params.get("id") or "").strip()
        patch = params.get("patch") or {}
        if not job_id:
            await channel.send_response(ws, req_id, ok=False, error="id is required", code="BAD_REQUEST")
            return
        if not isinstance(patch, dict):
            await channel.send_response(ws, req_id, ok=False, error="patch must be object", code="BAD_REQUEST")
            return
        try:
            job = await cc.update_job(job_id, patch)
            await channel.send_response(ws, req_id, ok=True, payload={"job": job})
        except KeyError:
            await channel.send_response(ws, req_id, ok=False, error="job not found", code="NOT_FOUND")
        except Exception as e:  # noqa: BLE001
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="BAD_REQUEST")

    async def _cron_job_delete(ws, req_id, params, session_id):
        cc = _get_cron()
        if cc is None:
            await channel.send_response(ws, req_id, ok=False, error="cron not available", code="INTERNAL_ERROR")
            return
        if not isinstance(params, dict):
            await channel.send_response(ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST")
            return
        job_id = str(params.get("id") or "").strip()
        if not job_id:
            await channel.send_response(ws, req_id, ok=False, error="id is required", code="BAD_REQUEST")
            return
        deleted = await cc.delete_job(job_id)
        if not deleted:
            await channel.send_response(ws, req_id, ok=False, error="job not found", code="NOT_FOUND")
            return
        await channel.send_response(ws, req_id, ok=True, payload={"deleted": True})

    async def _cron_job_toggle(ws, req_id, params, session_id):
        cc = _get_cron()
        if cc is None:
            await channel.send_response(ws, req_id, ok=False, error="cron not available", code="INTERNAL_ERROR")
            return
        if not isinstance(params, dict):
            await channel.send_response(ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST")
            return
        job_id = str(params.get("id") or "").strip()
        enabled = params.get("enabled", None)
        if not job_id:
            await channel.send_response(ws, req_id, ok=False, error="id is required", code="BAD_REQUEST")
            return
        if enabled is None:
            await channel.send_response(ws, req_id, ok=False, error="enabled is required", code="BAD_REQUEST")
            return
        try:
            job = await cc.toggle_job(job_id, bool(enabled))
            await channel.send_response(ws, req_id, ok=True, payload={"job": job})
        except KeyError:
            await channel.send_response(ws, req_id, ok=False, error="job not found", code="NOT_FOUND")

    async def _cron_job_preview(ws, req_id, params, session_id):
        cc = _get_cron()
        if cc is None:
            await channel.send_response(ws, req_id, ok=False, error="cron not available", code="INTERNAL_ERROR")
            return
        if not isinstance(params, dict):
            await channel.send_response(ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST")
            return
        job_id = str(params.get("id") or "").strip()
        count = params.get("count", 5)
        if not job_id:
            await channel.send_response(ws, req_id, ok=False, error="id is required", code="BAD_REQUEST")
            return
        try:
            next_runs = await cc.preview_job(job_id, int(count) if count is not None else 5)
            await channel.send_response(ws, req_id, ok=True, payload={"next": next_runs})
        except KeyError:
            await channel.send_response(ws, req_id, ok=False, error="job not found", code="NOT_FOUND")
        except Exception as e:  # noqa: BLE001
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="BAD_REQUEST")

    async def _cron_job_run_now(ws, req_id, params, session_id):
        cc = _get_cron()
        if cc is None:
            await channel.send_response(ws, req_id, ok=False, error="cron not available", code="INTERNAL_ERROR")
            return
        if not isinstance(params, dict):
            await channel.send_response(ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST")
            return
        job_id = str(params.get("id") or "").strip()
        if not job_id:
            await channel.send_response(ws, req_id, ok=False, error="id is required", code="BAD_REQUEST")
            return
        try:
            run_id = await cc.run_now(job_id)
            await channel.send_response(ws, req_id, ok=True, payload={"run_id": run_id})
        except KeyError:
            await channel.send_response(ws, req_id, ok=False, error="job not found", code="NOT_FOUND")
        except Exception as e:  # noqa: BLE001
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    channel.register_method("config.get", _config_get)
    channel.register_method("config.set", _config_set)
    channel.register_method("config.save_all", _config_save_all)
    channel.register_method("config.validate_model", _config_validate_model)
    channel.register_method("models.list", _models_list)
    channel.register_method("models.replace_all", _models_replace_all)
    channel.register_method("models.validate", _models_validate)
    channel.register_method("channel.get", _channel_get)

    channel.register_method("session.list", _session_list)
    channel.register_method("session.create", _session_create)
    channel.register_method("session.delete", _session_delete)

    channel.register_method("path.get", _path_get)
    channel.register_method("path.set", _path_set)

    async def _hooks_list(ws, req_id, params, session_id):
        from jiuwenswarm.common.hooks_config import load_hooks_config
        try:
            hooks_config = load_hooks_config(get_config())
            summary = hooks_config.get_event_summary()
            await channel.send_response(ws, req_id, ok=True,
                                        payload={
                                            "events": summary,
                                            "disable_all_hooks": hooks_config.disable_all_hooks,
                                            "source": "config.yaml",
                                        })
        except Exception as e:
            await channel.send_response(ws, req_id, ok=False,
                                        error=str(e), code="INTERNAL_ERROR")

    channel.register_method("memory.compute", _memory_compute)
    channel.register_method("hooks.list", _hooks_list)

    channel.register_method("chat.send", _chat_send)
    channel.register_method("chat.resume", _chat_resume)
    channel.register_method("chat.interrupt", _chat_interrupt)
    channel.register_method("chat.user_answer", _chat_user_answer)
    channel.register_method("history.get", _history_get)
    channel.register_method("locale.get_conf", _locale_get_conf)
    channel.register_method("locale.set_conf", _locale_set_conf)
    channel.register_method("updater.get_status", _updater_get_status)
    channel.register_method("updater.check", _updater_check)
    channel.register_method("updater.download", _updater_download)
    channel.register_method("updater.upgrade", _updater_upgrade)
    channel.register_method("updater.get_conf", _updater_get_conf)
    channel.register_method("updater.set_conf", _updater_set_conf)
    channel.register_method("heartbeat.get_conf", _heartbeat_get_conf)
    channel.register_method("heartbeat.set_conf", _heartbeat_set_conf)
    channel.register_method("heartbeat.get_path", _heartbeat_get_path)
    channel.register_method("channel.feishu.get_conf", _channel_feishu_get_conf)
    channel.register_method("channel.feishu.set_conf", _channel_feishu_set_conf)
    channel.register_method("channel.xiaoyi.get_conf", _channel_xiaoyi_get_conf)
    channel.register_method("channel.xiaoyi.set_conf", _channel_xiaoyi_set_conf)
    channel.register_method("channel.telegram.get_conf", _channel_telegram_get_conf)
    channel.register_method("channel.telegram.set_conf", _channel_telegram_set_conf)
    channel.register_method("channel.dingtalk.get_conf", _channel_dingtalk_get_conf)
    channel.register_method("channel.dingtalk.set_conf", _channel_dingtalk_set_conf)
    channel.register_method("channel.whatsapp.get_conf", _channel_whatsapp_get_conf)
    channel.register_method("channel.whatsapp.set_conf", _channel_whatsapp_set_conf)
    channel.register_method("channel.discord.get_conf", _channel_discord_get_conf)
    channel.register_method("channel.discord.set_conf", _channel_discord_set_conf)
    channel.register_method("channel.wecom.get_conf", _channel_wecom_get_conf)
    channel.register_method("channel.wecom.set_conf", _channel_wecom_set_conf)
    channel.register_method("channel.wechat.get_conf", _channel_wechat_get_conf)
    channel.register_method("channel.wechat.set_conf", _channel_wechat_set_conf)
    channel.register_method("channel.wechat.get_login_ui", _channel_wechat_get_login_ui)
    channel.register_method("channel.wechat.unbind", _channel_wechat_unbind)
    channel.register_method("cron.job.list", _cron_job_list)
    channel.register_method("cron.job.meta", _cron_job_meta)
    channel.register_method("cron.job.get", _cron_job_get)
    channel.register_method("cron.job.create", _cron_job_create)
    channel.register_method("cron.job.update", _cron_job_update)
    channel.register_method("cron.job.delete", _cron_job_delete)
    channel.register_method("cron.job.toggle", _cron_job_toggle)
    channel.register_method("cron.job.preview", _cron_job_preview)
    channel.register_method("cron.job.run_now", _cron_job_run_now)

    # 数字分身 — permissions.owner_scopes：仅 Web 网关直连 config（不经 E2A / config_rpc）。
    # 其余 permissions.*（tools / rules / approval_overrides）走 _forward_permissions_to_agent。

    async def _permissions_owner_scopes_get(ws, req_id, params, session_id):
        from jiuwenswarm.common.config import get_permissions_owner_scopes

        try:
            payload = get_permissions_owner_scopes()
            await channel.send_response(ws, req_id, ok=True, payload=payload)
        except Exception as e:
            logger.exception("[permissions.owner_scopes.get] %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    async def _permissions_owner_scopes_set(ws, req_id, params, session_id):
        from jiuwenswarm.common.config import update_permissions_owner_scopes_in_config

        if not isinstance(params, dict):
            await channel.send_response(ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST")
            return
        try:
            owner_scopes = params.get("owner_scopes", {})
            deny_guidance = params.get("deny_guidance_message")
            update_permissions_owner_scopes_in_config(owner_scopes, deny_guidance)
            await _notify_config_saved_once({}, ["permissions"], force=True)
            await channel.send_response(ws, req_id, ok=True, payload={"ok": True})
        except Exception as e:
            logger.exception("[permissions.owner_scopes.set] %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    channel.register_method("permissions.owner_scopes.get", _permissions_owner_scopes_get)
    channel.register_method("permissions.owner_scopes.set", _permissions_owner_scopes_set)

    async def _forward_permissions_to_agent(ws, req_id, params, session_id, *, req_method):
        """permissions.*：优先经 E2A 转发到 AgentServer；Agent 未就绪时本地执行（与 config_rpc 同源）。"""
        from jiuwenswarm.common.e2a.gateway_normalize import e2a_from_agent_fields
        from jiuwenswarm.common.schema.agent import AgentRequest
        from jiuwenswarm.common.schema.message import ReqMethod

        if not isinstance(req_method, ReqMethod):
            await channel.send_response(ws, req_id, ok=False, error="invalid req_method", code="INTERNAL_ERROR")
            return

        synthetic = AgentRequest(
            request_id=str(req_id) if req_id else "",
            channel_id="",
            session_id=session_id,
            req_method=req_method,
            params=dict(params) if isinstance(params, dict) else {},
        )

        ac = _resolve(agent_client)
        if ac is None or not getattr(ac, "server_ready", False):
            from jiuwenswarm.agents.harness.common.rails.permissions.permissions_config_rpc import \
                dispatch_permissions_config_request

            resp = dispatch_permissions_config_request(synthetic)
            if not resp.ok:
                pl = resp.payload if isinstance(resp.payload, dict) else {}
                await channel.send_response(
                    ws,
                    req_id,
                    ok=False,
                    error=str(pl.get("error") or "request failed"),
                    code=str(pl.get("code") or "BAD_REQUEST"),
                )
                return
            out = resp.payload if isinstance(resp.payload, dict) else {}
            if resp.ok and req_method not in (
                ReqMethod.PERMISSIONS_TOOLS_GET,
                ReqMethod.PERMISSIONS_RULES_GET,
                ReqMethod.PERMISSIONS_APPROVAL_OVERRIDES_GET,
            ):
                await _notify_config_saved_once({}, ["permissions"], force=True)
            await channel.send_response(ws, req_id, ok=True, payload=out)
            return

        env = e2a_from_agent_fields(
            request_id=str(req_id) if req_id else "",
            channel_id="",
            session_id=session_id,
            req_method=req_method,
            params=dict(params) if isinstance(params, dict) else {},
        )
        try:
            resp = await ac.send_request(env)
        except Exception as e:
            logger.exception("[permissions] forward to agent failed: %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")
            return
        if not resp.ok:
            pl = resp.payload if isinstance(resp.payload, dict) else {}
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error=str(pl.get("error") or "request failed"),
                code=str(pl.get("code") or "BAD_REQUEST"),
            )
            return
        out = resp.payload if isinstance(resp.payload, dict) else {}
        await channel.send_response(ws, req_id, ok=True, payload=out)

    from jiuwenswarm.common.schema.message import ReqMethod as _PermReq

    def _register_perm(method_name: str, rm: Any) -> None:
        async def _handler(ws, req_id, params, session_id):
            await _forward_permissions_to_agent(ws, req_id, params, session_id, req_method=rm)

        channel.register_method(method_name, _handler)

    _register_perm("permissions.tools.get", _PermReq.PERMISSIONS_TOOLS_GET)
    _register_perm("permissions.tools.set", _PermReq.PERMISSIONS_TOOLS_SET)
    _register_perm("permissions.tools.update", _PermReq.PERMISSIONS_TOOLS_UPDATE)
    _register_perm("permissions.tools.delete", _PermReq.PERMISSIONS_TOOLS_DELETE)
    _register_perm("permissions.rules.get", _PermReq.PERMISSIONS_RULES_GET)
    _register_perm("permissions.rules.create", _PermReq.PERMISSIONS_RULES_CREATE)
    _register_perm("permissions.rules.update", _PermReq.PERMISSIONS_RULES_UPDATE)
    _register_perm("permissions.rules.delete", _PermReq.PERMISSIONS_RULES_DELETE)
    _register_perm("permissions.approval_overrides.get", _PermReq.PERMISSIONS_APPROVAL_OVERRIDES_GET)
    _register_perm("permissions.approval_overrides.delete", _PermReq.PERMISSIONS_APPROVAL_OVERRIDES_DELETE)

    async def _memory_forbidden_get(ws, req_id, params, session_id):
        try:
            cfg = get_config() or {}
            payload = cfg.get("memory", {}).get("forbidden_memory_definition", {})
            await channel.send_response(ws, req_id, ok=True, payload=payload)
        except Exception as e:
            logger.exception("[memory.forbidden.get] %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    async def _memory_forbidden_set(ws, req_id, params, session_id):
        from jiuwenswarm.common.config import update_memory_forbidden_in_config
        if not isinstance(params, dict):
            await channel.send_response(ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST")
            return
        try:
            update_memory_forbidden_in_config(params)
            await channel.send_response(ws, req_id, ok=True, payload={"ok": True})
        except Exception as e:
            logger.exception("[memory.forbidden.set] %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    channel.register_method("memory.forbidden.get", _memory_forbidden_get)
    channel.register_method("memory.forbidden.set", _memory_forbidden_set)

    async def _forward_harness_to_agent(ws, req_id, params, session_id, *, req_method):
        """harness.*：优先经 E2A 转发到 AgentServer；Agent 未就绪时本地执行（无 agent 实例）。"""
        from jiuwenswarm.common.e2a.gateway_normalize import e2a_from_agent_fields
        from jiuwenswarm.common.schema.agent import AgentRequest
        from jiuwenswarm.common.schema.message import ReqMethod

        if not isinstance(req_method, ReqMethod):
            await channel.send_response(ws, req_id, ok=False, error="invalid req_method", code="INTERNAL_ERROR")
            return

        synthetic = AgentRequest(
            request_id=str(req_id) if req_id else "",
            channel_id="",
            session_id=session_id,
            req_method=req_method,
            params=dict(params) if isinstance(params, dict) else {},
        )

        ac = _resolve(agent_client)
        if ac is None or not getattr(ac, "server_ready", False):
            # Agent 未就绪时本地处理（无 agent 实例可用）
            from jiuwenswarm.agents.harness.common.auto_harness import (
                _HARNESS_PACKAGES_FILE,
                AutoHarnessService,
            )
            from pathlib import Path

            try:
                if req_method == ReqMethod.HARNESS_PACKAGES_GET:
                    packages_file = Path(_HARNESS_PACKAGES_FILE)
                    if await asyncio.to_thread(packages_file.exists):
                        raw_text = await asyncio.to_thread(packages_file.read_text, encoding="utf-8")
                        data = await asyncio.to_thread(json.loads, raw_text)
                    else:
                        service = AutoHarnessService(rail=None, agent=None)
                        data = await asyncio.to_thread(service.scan_runtime_extensions)
                        await asyncio.to_thread(service.save_packages, data)
                    await channel.send_response(ws, req_id, ok=True, payload=data)
                    return
                elif req_method == ReqMethod.HARNESS_PACKAGES_SCAN:
                    service = AutoHarnessService(rail=None, agent=None)
                    data = await asyncio.to_thread(service.scan_runtime_extensions)
                    await asyncio.to_thread(service.save_packages, data)
                    await channel.send_response(ws, req_id, ok=True, payload=data)
                    return
                elif req_method == ReqMethod.HARNESS_PACKAGES_DELETE:
                    package_id = params.get("package_id")
                    if package_id == "native":
                        await channel.send_response(
                            ws, req_id, ok=False, error="Cannot delete native agent version", code="BAD_REQUEST")
                        return
                    service = AutoHarnessService(rail=None, agent=None)
                    payload = await service.delete_package(package_id)
                    await channel.send_response(ws, req_id, ok=True, payload=payload)
                    return
                else:
                    await channel.send_response(
                        ws, req_id, ok=False,
                        error="Agent not ready for this operation",
                        code="SERVICE_UNAVAILABLE"
                    )
                    return
            except ValueError as exc:
                await channel.send_response(ws, req_id, ok=False, error=str(exc), code="BAD_REQUEST")
                return
            except Exception as exc:
                logger.exception("[harness] local fallback failed: %s", exc)
                await channel.send_response(ws, req_id, ok=False, error=str(exc), code="INTERNAL_ERROR")
                return

        env = e2a_from_agent_fields(
            request_id=str(req_id) if req_id else "",
            channel_id="",
            session_id=session_id,
            req_method=req_method,
            params=dict(params) if isinstance(params, dict) else {},
        )
        try:
            resp = await ac.send_request(env)
        except Exception as e:
            logger.exception("[harness] forward to agent failed: %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")
            return
        if not resp.ok:
            pl = resp.payload if isinstance(resp.payload, dict) else {}
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error=str(pl.get("error") or "request failed"),
                code=str(pl.get("code") or "BAD_REQUEST"),
            )
            return
        out = resp.payload if isinstance(resp.payload, dict) else {}
        await channel.send_response(ws, req_id, ok=True, payload=out)

    from jiuwenswarm.common.schema.message import ReqMethod as _HarnessReq

    def _register_harness(method_name: str, rm: Any) -> None:
        async def _handler(ws, req_id, params, session_id):
            await _forward_harness_to_agent(ws, req_id, params, session_id, req_method=rm)

        channel.register_method(method_name, _handler)

    _register_harness("harness.packages", _HarnessReq.HARNESS_PACKAGES_GET)
    _register_harness("harness.packages.scan", _HarnessReq.HARNESS_PACKAGES_SCAN)
    _register_harness("harness.activate", _HarnessReq.HARNESS_PACKAGES_ACTIVATE)
    _register_harness("harness.deactivate", _HarnessReq.HARNESS_PACKAGES_DEACTIVATE)
    _register_harness("harness.delete", _HarnessReq.HARNESS_PACKAGES_DELETE)

    async def _harness_import_handler(ws, req_id, params, session_id):
        """Import a harness package via WebSocket (base64 encoded zip content)."""
        # Get base64 encoded file content
        file_content_b64 = params.get("file_content")
        if not file_content_b64:
            await channel.send_response(ws, req_id, ok=False, error="Missing file_content", code="BAD_REQUEST")
            return

        # Decode base64 content
        try:
            file_content = base64.b64decode(file_content_b64)
        except Exception as e:
            await channel.send_response(ws, req_id, ok=False, error=f"Invalid base64 content: {e}", code="BAD_REQUEST")
            return

        # Check file size (100MB limit)
        max_size = 50 * 1024 * 1024
        if len(file_content) > max_size:
            await channel.send_response(ws, req_id, ok=False, error="File exceeds 100MB limit", code="BAD_REQUEST")
            return

        # Save to temp directory
        temp_dir = get_user_workspace_dir() / "auto-harness" / "temp" / "uploads"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_zip_path = temp_dir / f"upload_{uuid.uuid4().hex[:8]}.zip"

        try:
            temp_zip_path.write_bytes(file_content)
            service = AutoHarnessService(rail=None, agent=None)
            package_info = service.import_package(temp_zip_path)
            await channel.send_response(ws, req_id, ok=True, payload={
                "ok": True,
                "package": package_info,
                "message": "Package imported successfully",
            })
        except ValueError as exc:
            msg = str(exc)
            if "already exists" in msg.lower():
                await channel.send_response(ws, req_id, ok=False, error=msg, code="CONFLICT")
            elif "invalid" in msg.lower() or "must contain" in msg.lower():
                await channel.send_response(ws, req_id, ok=False, error=msg, code="BAD_REQUEST")
            else:
                await channel.send_response(ws, req_id, ok=False, error=msg, code="BAD_REQUEST")
        except Exception as exc:
            logger.exception("[harness.import] failed: %s", exc)
            await channel.send_response(ws, req_id, ok=False, error=f"Import failed: {exc}", code="INTERNAL_ERROR")
        finally:
            # Cleanup temp file
            try:
                temp_zip_path.unlink(missing_ok=True)
            except Exception:
                pass

    channel.register_method("harness.import", _harness_import_handler)

    async def _harness_export_handler(ws, req_id, params, session_id):
        """Export a harness package - returns download URL instead of base64 content.

        Uses HTTP download endpoint to avoid WebSocket message size limits.
        The temporary zip file will be cleaned up after download or token expiry.
        """
        package_id = params.get("package_id")
        if not package_id:
            await channel.send_response(ws, req_id, ok=False, error="Missing package_id", code="BAD_REQUEST")
            return

        try:
            service = AutoHarnessService(rail=None, agent=None)
            zip_path = service.export_package(package_id)

            download_info = build_file_download_info(
                str(zip_path),
                zip_path.name,
                session_id,
                expires_in=600,  # 10 minutes
            )

            await channel.send_response(ws, req_id, ok=True, payload={
                "ok": True,
                "download_url": download_info["download_url"],
                "download_token": download_info["download_token"],
                "filename": download_info["name"],
                "file_size": download_info["size"],
                "message": "Package exported successfully",
            })
            # No cleanup here - file will be served via HTTP download endpoint
            # and cleaned up after download or when token expires
        except ValueError as exc:
            msg = str(exc)
            if "not found" in msg.lower():
                await channel.send_response(ws, req_id, ok=False, error=msg, code="NOT_FOUND")
            elif "native" in msg.lower():
                await channel.send_response(ws, req_id, ok=False, error=msg, code="BAD_REQUEST")
            else:
                await channel.send_response(ws, req_id, ok=False, error=msg, code="BAD_REQUEST")
        except Exception as exc:
            logger.exception("[harness.export] failed: %s", exc)
            await channel.send_response(ws, req_id, ok=False, error=f"Export failed: {exc}", code="INTERNAL_ERROR")

    channel.register_method("harness.export", _harness_export_handler)
