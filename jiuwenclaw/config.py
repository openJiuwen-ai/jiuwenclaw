# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import copy
import logging
import os
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Optional
from ruamel.yaml import YAML
import yaml
from jiuwenclaw.local_env_config import get_bound_agent_env_ns, get_local_config, is_task_env_overlay_bound


from jiuwenclaw.utils import (
    USER_WORKSPACE_DIR,
    get_config_file,
    load_yaml_dict,
    merge_template_with_override,
    resolve_env_vars,
    resolve_shipped_template_config_path,
)

_logger = logging.getLogger(__name__)
_logged_template_config_path = False

_CONFIG_MODULE_DIR = Path(__file__).parent
_CONFIG_YAML_PATH = get_config_file()


def _current_config_yaml_path() -> Path:
    """Return the active config path at call time."""
    return get_config_file()

# Check if user workspace exists and use it if configured via env
_user_config = os.getenv("JIUWENCLAW_CONFIG_DIR")
if _user_config:
    _CONFIG_MODULE_DIR = Path(_user_config)
elif (USER_WORKSPACE_DIR / "config").exists():
    _CONFIG_MODULE_DIR = USER_WORKSPACE_DIR / "config"

# Ensure config directory is in sys.path
if str(_CONFIG_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(_CONFIG_MODULE_DIR))


def resolve_template_config_path() -> Path:
    """包内 shipped 模板：jiuwenclaw/resources/config.yaml。"""
    global _logged_template_config_path
    path = resolve_shipped_template_config_path()
    if not _logged_template_config_path:
        _logged_template_config_path = True
        _logger.debug("jiuwenclaw template config (shipped): %s", path.resolve())
    return path


def get_merged_config_dict() -> dict[str, Any]:
    """模板与用户 override 合并后的字典（不解析环境变量）。"""
    template = load_yaml_dict(resolve_template_config_path())
    override = load_yaml_dict(_current_config_yaml_path())
    return merge_template_with_override(template, override)


# Resolved-config cache keyed by bound (service_id, agent_id).
# ``None`` key = unbound ContextVar (typically default tip / process-shared reads).
# Expired by TTL or ``clear_config_cache()``. Tip mutations clear the affected slot.
_ConfigNsKey = tuple[str, str] | None
_resolved_config_by_ns: dict[_ConfigNsKey, tuple[float, dict[str, Any]]] = {}
_CONFIG_CACHE_TTL_SECONDS: float = 20.0
_config_lock = threading.Lock()
_config_version: int = 0


def get_config():
    """Return the merged, env-var-resolved config dict.

    Results are cached per bound ``(service_id, agent_id)`` (from
    ``bind_agent_env_ns``) with a TTL of ``_CONFIG_CACHE_TTL_SECONDS``
    (default 20s). Unbound callers share a separate ``None`` slot.

    When a task env overlay is bound (seal), the cache is bypassed so the
    overlay is always reflected.

    The cache is invalidated by ``clear_config_cache()`` (config reload /
    set_config / yaml write, and tip mutations for the affected ns).

    **WARNING**: The returned dict is a shared reference. Callers MUST NOT
    mutate it (including nested dicts/lists), as changes will affect all
    other callers within the TTL window. Use ``copy.deepcopy()`` if you
    need to modify the result.
    """
    global _config_version
    ns: _ConfigNsKey = get_bound_agent_env_ns()
    skip_cache = is_task_env_overlay_bound()
    now = time.monotonic()

    if not skip_cache:
        with _config_lock:
            entry = _resolved_config_by_ns.get(ns)
            if entry is not None and (now - entry[0]) < _CONFIG_CACHE_TTL_SECONDS:
                return entry[1]
            read_version = _config_version
    else:
        read_version = -1

    # Slow path: cache miss / expired / overlay seal (lock released during I/O)
    config_base = get_merged_config_dict()
    new_cache = resolve_env_vars(config_base)

    if not skip_cache:
        with _config_lock:
            if _config_version == read_version:
                _resolved_config_by_ns[ns] = (time.monotonic(), new_cache)

    return new_cache


def clear_config_cache(
    service_id: str | None = None,
    agent_id: str | None = None,
) -> None:
    """Invalidate the resolved-config cache.

    With no args: clear all ns slots (yaml write / full reload).
    With ``service_id`` / ``agent_id``: clear that slot only (tip mutation).
    Clearing ``default``/``default`` also clears the unbound (``None``) slot.
    """
    global _config_version
    with _config_lock:
        _config_version += 1
        if service_id is None and agent_id is None:
            _resolved_config_by_ns.clear()
            return
        from jiuwenclaw.local_env_config import normalize_env_ns_id

        sid = normalize_env_ns_id(service_id, default="default")
        aid = normalize_env_ns_id(agent_id, default="default")
        _resolved_config_by_ns.pop((sid, aid), None)
        if sid == "default" and aid == "default":
            _resolved_config_by_ns.pop(None, None)


def get_config_raw():
    """合并包内模板与用户 override 后的快照（不解析环境变量）。

    磁盘上的用户文件可能仅为稀疏 override；本函数返回有效配置树。
    局部写回 override 请使用 ``_load_yaml_round_trip(get_config_file())``。
    """
    return get_merged_config_dict()


def set_config(config):
    with open(_current_config_yaml_path(), "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)
    clear_config_cache()  # set_config bypasses _dump_yaml_round_trip, so clear explicitly


def _load_yaml_round_trip(config_path: Path):
    """ruamel 加载 config，保留注释与格式。"""
    rt = YAML()
    rt.preserve_quotes = True
    with open(config_path, "r", encoding="utf-8") as f:
        return rt.load(f)


def _dump_yaml_round_trip(config_path: Path, data: Any) -> None:
    """ruamel 写回 config，保留注释与格式。"""
    rt = YAML()
    rt.preserve_quotes = True
    rt.default_flow_style = False
    # mapping 2 空格；list 用 sequence=4 + offset=2 保证 dash 前有 2 空格（tools: 下 - todo），否则 list 会变成无缩进
    rt.indent(mapping=2, sequence=4, offset=2)
    rt.width = 4096
    with open(config_path, "w", encoding="utf-8") as f:
        rt.dump(data, f)
    # Any write to a config file invalidates the get_config() cache
    clear_config_cache()


def update_heartbeat_in_config(payload: dict[str, Any]) -> None:
    """只更新 heartbeat 段并写回。"""
    data = _load_yaml_round_trip(_current_config_yaml_path())
    if "heartbeat" not in data:
        data["heartbeat"] = {}
    hb = data["heartbeat"]
    if "every" in payload:
        hb["every"] = payload["every"]
    if "target" in payload:
        hb["target"] = payload["target"]
    if "active_hours" in payload:
        hb["active_hours"] = payload["active_hours"]
    _dump_yaml_round_trip(_current_config_yaml_path(), data)


def update_channel_in_config(channel_id: str, conf: dict[str, Any]) -> None:
    """只更新 channels[channel_id] 并写回。"""
    data = _load_yaml_round_trip(_current_config_yaml_path())
    if "channels" not in data:
        data["channels"] = {}
    channels = data["channels"]
    if channel_id not in channels:
        channels[channel_id] = {}
    section = channels[channel_id]
    for k, v in conf.items():
        section[k] = v
    _dump_yaml_round_trip(_current_config_yaml_path(), data)


def replace_channel_in_config(channel_id: str, conf: dict[str, Any]) -> None:
    """整段替换 channels[channel_id] 并写回。"""
    data = _load_yaml_round_trip(_current_config_yaml_path())
    if "channels" not in data:
        data["channels"] = {}
    channels = data["channels"]
    channels[channel_id] = dict(conf)
    _dump_yaml_round_trip(_current_config_yaml_path(), data)


def update_channel_subsection_in_config(
    channel_id: str,
    subsection_id: str,
    conf: dict[str, Any],
) -> None:
    """更新 channels[channel_id][subsection_id] 并写回。"""
    data = _load_yaml_round_trip(_current_config_yaml_path())
    if "channels" not in data:
        data["channels"] = {}
    channels = data["channels"]
    if channel_id not in channels:
        channels[channel_id] = {}
    section = channels[channel_id]
    if subsection_id not in section:
        section[subsection_id] = {}
    subsection = section[subsection_id]
    for k, v in conf.items():
        subsection[k] = v
    _dump_yaml_round_trip(_current_config_yaml_path(), data)


def update_preferred_language_in_config(lang: str) -> None:
    """只更新顶层 preferred_language 并写回。非法值回退为 zh，与 set_preferred_language_in_config_file 一致。"""
    normalized = str(lang or "zh").strip().lower()
    if normalized not in ("zh", "en"):
        normalized = "zh"
    data = _load_yaml_round_trip(_current_config_yaml_path())
    data["preferred_language"] = normalized
    _dump_yaml_round_trip(_current_config_yaml_path(), data)


def set_preferred_language_in_config_file(config_path: Path, lang: str) -> None:
    """将 preferred_language 写入指定 config.yaml（用于 init 等尚未绑定全局路径的场景）。"""
    lang = str(lang or "zh").strip().lower()
    if lang not in ("zh", "en"):
        lang = "zh"
    if not config_path.exists():
        return
    data = _load_yaml_round_trip(config_path)
    data["preferred_language"] = lang
    _dump_yaml_round_trip(config_path, data)


def update_browser_in_config(updates: dict[str, Any]) -> None:
    """只更新 browser 段（如 chrome_path）并写回。"""
    data = _load_yaml_round_trip(_current_config_yaml_path())
    if "browser" not in data:
        data["browser"] = {}
    section = data["browser"]
    for k, v in updates.items():
        section[k] = v
    _dump_yaml_round_trip(_current_config_yaml_path(), data)


def update_context_engine_enabled_in_config(value: bool) -> None:
    """更新 react.context_engine_config.enabled（上下文压缩开关）并写回。"""
    data = _load_yaml_round_trip(_current_config_yaml_path())
    if "react" not in data:
        data["react"] = {}
    react = data["react"]
    if "context_engine_config" not in react:
        react["context_engine_config"] = {}
    react["context_engine_config"]["enabled"] = value
    _dump_yaml_round_trip(_current_config_yaml_path(), data)


def update_kv_cache_affinity_enabled_in_config(value: bool) -> None:
    """更新 react.context_engine_config.enable_kv_cache_release（算力/KV 亲和释放）并写回。"""
    data = _load_yaml_round_trip(_current_config_yaml_path())
    if "react" not in data:
        data["react"] = {}
    react = data["react"]
    if "context_engine_config" not in react:
        react["context_engine_config"] = {}
    react["context_engine_config"]["enable_kv_cache_release"] = value
    _dump_yaml_round_trip(_current_config_yaml_path(), data)


def update_permissions_enabled_in_config(value: bool) -> None:
    """更新 permissions.enabled（工具安全护栏开关）并写回。"""
    data = _load_yaml_round_trip(_current_config_yaml_path())
    if "permissions" not in data:
        data["permissions"] = {}
    data["permissions"]["enabled"] = value
    _dump_yaml_round_trip(_current_config_yaml_path(), data)


def get_permissions_file_guard_workspace_rw_enabled() -> bool:
    """读取 ``permissions.file_guard.workspace.rw_enabled``（缺省为 True，与 Phase-1 默认一致）。"""
    cfg = get_config() or {}
    fg = (cfg.get("permissions") or {}).get("file_guard")
    if not isinstance(fg, dict):
        return True
    ws = fg.get("workspace")
    if not isinstance(ws, dict):
        return True
    return bool(ws.get("rw_enabled", True))


def update_permissions_file_guard_workspace_rw_enabled_in_config(value: bool) -> None:
    """更新 ``permissions.file_guard.workspace.rw_enabled`` 并写回 config.yaml。"""
    data = _load_yaml_round_trip(_current_config_yaml_path())
    perms = data.setdefault("permissions", {})
    fg = perms.get("file_guard")
    if not isinstance(fg, dict):
        fg = {}
        perms["file_guard"] = fg
    ws = fg.get("workspace")
    if not isinstance(ws, dict):
        ws = {}
        fg["workspace"] = ws
    ws["rw_enabled"] = bool(value)
    _dump_yaml_round_trip(_current_config_yaml_path(), data)


def update_disabled_tools_in_config(disabled_tools: list[str]) -> None:
    """更新 react.disabled_tools 数组并写回 config.yaml。

    Args:
        disabled_tools: 禁用的工具名列表，如 ["bash", "read_file"]
    """
    data = _load_yaml_round_trip(_current_config_yaml_path())
    if "react" not in data:
        data["react"] = {}
    data["react"]["disabled_tools"] = disabled_tools
    _dump_yaml_round_trip(_current_config_yaml_path(), data)


def update_disabled_skills_in_config(disabled_skills: list[str]) -> None:
    """更新 react.disabled_skills 数组并写回 config.yaml。

    Args:
        disabled_skills: 禁用的技能名列表，如 ["dangerous_skill", "deprecated_skill"]
    """
    data = _load_yaml_round_trip(_current_config_yaml_path())
    if "react" not in data:
        data["react"] = {}
    data["react"]["disabled_skills"] = disabled_skills
    _dump_yaml_round_trip(_current_config_yaml_path(), data)


def update_updater_in_config(updates: dict[str, Any]) -> None:
    """只更新 updater 段并写回。"""
    data = _load_yaml_round_trip(_current_config_yaml_path())
    if "updater" not in data:
        data["updater"] = {}
    section = data["updater"]
    for key, value in updates.items():
        section[key] = value
    _dump_yaml_round_trip(_current_config_yaml_path(), data)


def update_memory_enabled_in_config(mode: str, value: bool) -> None:
    """更新 memory.enabled（记忆系统开关）并写回。"""
    _update_memory_in_modes_config(mode, "enabled", value)


def update_proactive_memory_in_config(mode: str, value: bool) -> None:
    """更新 memory.proactive_memory（主动记忆开关）并写回。"""
    _update_memory_in_modes_config(mode, "is_proactive", value)


def _update_memory_in_modes_config(mode: str, item: str, value: bool) -> None:
    data = _load_yaml_round_trip(_current_config_yaml_path())
    if "modes" not in data:
        data["modes"] = {}
    if "claw" not in data["modes"]:
        data["modes"]["claw"] = {}
    if mode not in data["modes"]["claw"]:
        data["modes"]["claw"][mode] = {}
    if "memory" not in data["modes"]["claw"][mode]:
        data["modes"]["claw"][mode]["memory"] = {}
    data["modes"]["claw"][mode]["memory"][item] = value
    _dump_yaml_round_trip(_current_config_yaml_path(), data)


# ---------- 数字分身相关配置 ----------

def get_permissions_owner_scopes() -> dict[str, Any]:
    """读取 permissions.owner_scopes 及 deny_guidance_message."""
    cfg = get_config() or {}
    perm = cfg.get("permissions", {})
    return {
        "owner_scopes": perm.get("owner_scopes", {}),
        "deny_guidance_message": perm.get("deny_guidance_message", ""),
    }


def update_permissions_owner_scopes_in_config(
    owner_scopes: dict[str, Any],
    deny_guidance_message: str | None = None,
) -> None:
    """更新 permissions.owner_scopes（及可选 deny_guidance_message）并写回。"""
    data = _load_yaml_round_trip(_current_config_yaml_path())
    if "permissions" not in data:
        data["permissions"] = {}
    data["permissions"]["owner_scopes"] = owner_scopes
    if deny_guidance_message is not None:
        data["permissions"]["deny_guidance_message"] = deny_guidance_message
    _dump_yaml_round_trip(_current_config_yaml_path(), data)


def get_permissions_deny_guidance() -> str:
    """读取 permissions.deny_guidance_message."""
    cfg = get_config() or {}
    return cfg.get("permissions", {}).get("deny_guidance_message", "")


def update_permissions_deny_guidance_in_config(msg: str) -> None:
    """更新 permissions.deny_guidance_message 并写回。"""
    data = _load_yaml_round_trip(_current_config_yaml_path())
    if "permissions" not in data:
        data["permissions"] = {}
    data["permissions"]["deny_guidance_message"] = msg
    _dump_yaml_round_trip(_current_config_yaml_path(), data)


# ---------- Web UI：permissions.tools / rules / approval_overrides ----------

_VALID_PERM_LEVEL = frozenset({"allow", "ask", "deny"})
_VALID_RULE_ACTION = frozenset({"allow", "ask", "deny"})
_RULE_MUTABLE_KEYS = frozenset({"pattern", "action", "description"})


def get_permissions_tools() -> dict[str, Any]:
    """返回 ``permissions.tools``（原始结构，可能含 legacy dict）。"""
    cfg = get_config() or {}
    tools = (cfg.get("permissions") or {}).get("tools")
    if not isinstance(tools, dict):
        return {"tools": {}}
    return {"tools": dict(tools)}


def get_permissions_defaults_level() -> str:
    """Return UI-facing default tool level from ``permissions.defaults``."""
    cfg = get_config() or {}
    raw = (cfg.get("permissions") or {}).get("defaults", "guard")
    return normalize_permissions_tool_level(raw) or "ask"


def normalize_permissions_tool_level(raw: Any) -> str | None:
    """Normalize a configured tool level to ``allow|ask|deny`` for UI display."""
    if isinstance(raw, str):
        level = raw.strip().lower()
        if level in ("guard", "ask"):
            return "ask"
        if level in _VALID_PERM_LEVEL:
            return level
        return None
    if isinstance(raw, dict):
        star = raw.get("*")
        if star is not None:
            return normalize_permissions_tool_level(star)
    return None


def _resolve_ui_language() -> str:
    """Resolve the translator language from jiuwenclaw's global config.

    ``preferred_language`` is stored as ``zh``/``en`` (this branch's convention),
    while the upstream ``openjiuwen`` translator expects ``cn``/``en``. Map
    ``zh``-family values to ``cn`` and default to ``cn``, mirroring the existing
    mapping in ``agentserver/swarm/config_specs.py``.
    """
    cfg = get_config() or {}
    lang = str(cfg.get("preferred_language", "zh") or "zh").strip().lower()
    if lang in ("en", "english"):
        return "en"
    return "cn"


@cache
def _get_team_tool_translator(lang: str | None = None) -> Any:
    """Lazily build the agent-team tool description translator (cached).

    Config-only agent-team tools (``build_team``, ``spawn_teammate``, ...) are
    registered to ``ability_manager`` only after ``build_team`` completes, so
    they are absent from the runtime catalog until then. Their descriptions
    are sourced from the upstream ``openjiuwen.agent_teams.tools.locales``
    translator so they stay in sync with upstream changes without a
    hand-maintained static mapping. Returns ``None`` when the upstream package
    is unavailable (e.g. a jiuwenclaw runtime without agent-team support).

    ``lang`` is the translator language (``cn``/``en``); when omitted it is
    resolved from the global ``preferred_language`` config so the tool list
    stays consistent with the runtime's language. A future request-level
    language can be threaded through by passing ``lang`` explicitly.
    """
    try:
        from openjiuwen.agent_teams.tools.locales import make_translator

        return make_translator(lang or _resolve_ui_language())
    except Exception:
        return None


def _resolve_config_only_tool_description(tool_name: str) -> str:
    """Resolve description for a config-only tool absent from the runtime catalog.

    Agent-team tools resolve via the upstream translator; tools whose
    descriptions live elsewhere (e.g. ``office_claw_*`` MCP tools described by
    the relay-claw MCP server) return an empty string so the UI can show a
    placeholder.
    """
    translator = _get_team_tool_translator(_resolve_ui_language())
    if translator is None:
        return ""
    try:
        return str(translator(tool_name)) or ""
    except (FileNotFoundError, KeyError):
        return ""
    except Exception:
        return ""


def build_permissions_tools_list_view(
    catalog_by_name: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Build permissions UI list merging runtime catalog and config-only tools.

    Tools are drawn from two sources, merged by name:

    1. ``catalog_by_name`` — tools currently registered in the runtime
       ``ability_manager`` (``registered=True``).
    2. ``permissions.tools`` config keys absent from the runtime catalog
       (``registered=False``). These are tools configured in advance but not
       yet registered — e.g. agent-team tools only registered after
       ``build_team`` completes. Surfacing them lets users pre-configure
       approval levels before the tool is ever invoked; the level takes effect
       once the tool registers and is called, because ``PermissionEngine``
       reads ``config.tools[name]`` independent of registration state.

    Descriptions for config-only agent-team tools are resolved via the
    ``openjiuwen.agent_teams.tools.locales`` translator so they stay in sync
    with upstream tool definitions without a hand-maintained mapping.
    """
    from jiuwenclaw.agentserver.tool_catalog import ui_list_short_description

    catalog = dict(catalog_by_name or {})
    explicit = get_permissions_tools().get("tools")
    if not isinstance(explicit, dict):
        explicit = {}
    default_level = get_permissions_defaults_level()

    seen: set[str] = set()
    tools_out: list[dict[str, Any]] = []

    # 1. 已注册工具（runtime catalog）：registered=True
    for name in sorted(catalog.keys()):
        seen.add(name)
        entry = catalog.get(name, {})
        raw_explicit = explicit.get(name)
        if raw_explicit is not None:
            level = normalize_permissions_tool_level(raw_explicit) or default_level
            configured = True
        else:
            level = default_level
            configured = False

        short = ui_list_short_description(
            name,
            description=str(entry.get("description", "") or ""),
            short_description=str(entry.get("short_description", "") or ""),
        )

        tools_out.append(
            {
                "name": name,
                "short_description": short,
                "level": level,
                "configured": configured,
                "registered": True,
            }
        )

    # 2. config 独有键（permissions.tools 里配过、但不在 runtime catalog）：
    #    典型场景是 agent-team 工具——只在 build_team 成功后才注册到
    #    ability_manager。提前暴露它们让用户可预配审批档位；档位在该工具
    #    注册并被调用时即生效（PermissionEngine 读 config.tools[name]，
    #    与注册状态无关）。
    for name in sorted(explicit.keys()):
        if name in seen:
            continue
        raw_explicit = explicit.get(name)
        level = normalize_permissions_tool_level(raw_explicit) or default_level
        description = _resolve_config_only_tool_description(name)
        short = ui_list_short_description(
            name,
            description=description,
            short_description="",
        )
        tools_out.append(
            {
                "name": name,
                "short_description": short,
                "level": level,
                "configured": True,
                "registered": False,
            }
        )

    return {"default_level": default_level, "tools": tools_out}


def replace_permissions_tools_in_config(tools: Any) -> None:
    """整表替换 ``permissions.tools``；值仅允许 ``allow|ask|deny``。"""
    normalized = _validate_tools_map(tools)
    data = _load_yaml_round_trip(_current_config_yaml_path())
    if "permissions" not in data:
        data["permissions"] = {}
    data["permissions"]["tools"] = normalized
    _dump_yaml_round_trip(_current_config_yaml_path(), data)


def update_permissions_tool_in_config(tool_name: str, level: Any) -> dict[str, Any]:
    """合并单条工具级别到 ``permissions.tools`` 并写回 YAML。

    Args:
        tool_name: 工具名（如 ``mcp_exec_command``），与 ``permissions.tools`` 键一致。
        level: ``allow`` / ``ask`` / ``deny`` 字符串。

    Returns:
        ``{\"tools\": {...}}`` 更新后的完整 tools 映射（便于前端刷新）。
    """
    name = str(tool_name).strip()
    if not name:
        raise ValueError("tool name must be non-empty")
    piece = _validate_tools_map({name: level})
    data = _load_yaml_round_trip(_current_config_yaml_path())
    if "permissions" not in data:
        data["permissions"] = {}
    existing = data["permissions"].get("tools")
    if not isinstance(existing, dict):
        existing = {}
    merged = {str(k): v for k, v in existing.items()}
    merged[name] = piece[name]
    data["permissions"]["tools"] = merged
    _dump_yaml_round_trip(_current_config_yaml_path(), data)
    return {"tools": dict(merged)}


def delete_permissions_tool_in_config(tool_name: str) -> bool:
    """从 ``permissions.tools`` 中删除一个键；不存在则返回 False。"""
    name = str(tool_name).strip()
    if not name:
        raise ValueError("tool name must be non-empty")
    data = _load_yaml_round_trip(_current_config_yaml_path())
    if "permissions" not in data:
        return False
    tools = data["permissions"].get("tools")
    if not isinstance(tools, dict):
        return False
    key_to_drop = None
    for k in tools:
        if str(k).strip() == name:
            key_to_drop = k
            break
    if key_to_drop is None:
        return False
    new_tools = {k: v for k, v in tools.items() if k != key_to_drop}
    data["permissions"]["tools"] = new_tools
    _dump_yaml_round_trip(_current_config_yaml_path(), data)
    return True


def _validate_tools_map(tools: Any) -> dict[str, str]:
    if not isinstance(tools, dict):
        raise ValueError("tools must be an object")
    out: dict[str, str] = {}
    for k, v in tools.items():
        name = str(k).strip()
        if not name:
            raise ValueError("tool name must be non-empty")
        if isinstance(v, str):
            level = v.strip().lower()
        else:
            raise ValueError(f"tools[{name!r}]: value must be allow|ask|deny")
        if level not in _VALID_PERM_LEVEL:
            raise ValueError(f"tools[{name!r}]: invalid level {level!r}")
        out[name] = level
    return out


def get_permissions_rules() -> dict[str, Any]:
    """返回 ``permissions.rules`` 列表（仅 dict 项）。"""
    cfg = get_config() or {}
    rules = (cfg.get("permissions") or {}).get("rules")
    if not isinstance(rules, list):
        return {"rules": []}
    return {"rules": [r for r in rules if isinstance(r, dict)]}


def get_permissions_approval_overrides() -> dict[str, Any]:
    """返回 ``permissions.approval_overrides`` 列表（仅 dict 项）。"""
    cfg = get_config() or {}
    raw = (cfg.get("permissions") or {}).get("approval_overrides")
    if not isinstance(raw, list):
        return {"approval_overrides": []}
    return {"approval_overrides": [x for x in raw if isinstance(x, dict)]}


def create_permissions_rule_in_config(rule: dict[str, Any]) -> dict[str, Any]:
    """追加一条 ``permissions.rules`` 项，返回落盘后的规则（含 ``id``）。"""
    if not isinstance(rule, dict):
        raise ValueError("rule must be an object")
    rid = str(rule.get("id") or "").strip() or f"ui_rule_{uuid.uuid4().hex[:12]}"
    stored: dict[str, Any] = {"id": rid}
    for key in _RULE_MUTABLE_KEYS:
        if key in rule and rule[key] is not None:
            stored[key] = rule[key]
    if "pattern" not in stored:
        raise ValueError("pattern is required")
    stored["pattern"] = str(stored["pattern"]).strip()
    if not stored["pattern"]:
        raise ValueError("pattern must be non-empty")
    _normalize_rule_action(stored)

    data = _load_yaml_round_trip(_current_config_yaml_path())
    if "permissions" not in data:
        data["permissions"] = {}
    rules = data["permissions"].get("rules")
    if not isinstance(rules, list):
        rules = []
    if any(isinstance(r, dict) and str(r.get("id") or "").strip() == rid for r in rules):
        raise ValueError(f"rule id already exists: {rid}")
    rules.append(stored)
    data["permissions"]["rules"] = rules
    _dump_yaml_round_trip(_current_config_yaml_path(), data)
    return stored


def update_permissions_rule_in_config(rule_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """按 ``id`` 合并更新一条 rule。"""
    rid = str(rule_id or "").strip()
    if not rid:
        raise ValueError("id is required")
    if not isinstance(patch, dict):
        raise ValueError("patch must be an object")

    data = _load_yaml_round_trip(_current_config_yaml_path())
    if "permissions" not in data:
        data["permissions"] = {}
    rules = data["permissions"].get("rules")
    if not isinstance(rules, list):
        rules = []
    idx: int | None = None
    for i, r in enumerate(rules):
        if isinstance(r, dict) and str(r.get("id") or "").strip() == rid:
            idx = i
            break
    if idx is None:
        raise ValueError(f"rule not found: {rid}")

    merged: dict[str, Any] = dict(rules[idx])
    for k, v in patch.items():
        if k == "id":
            continue
        if k not in _RULE_MUTABLE_KEYS:
            continue
        if v is None:
            merged.pop(k, None)
        else:
            merged[k] = v
    merged["id"] = rid
    if "pattern" in merged:
        merged["pattern"] = str(merged["pattern"]).strip()
    if not merged.get("pattern"):
        raise ValueError("pattern must be non-empty")
    _normalize_rule_action(merged)
    rules[idx] = merged
    data["permissions"]["rules"] = rules
    _dump_yaml_round_trip(_current_config_yaml_path(), data)
    return merged


def delete_permissions_rule_in_config(rule_id: str) -> bool:
    """删除 ``permissions.rules`` 中指定 ``id``；若未找到返回 False。"""
    rid = str(rule_id or "").strip()
    if not rid:
        raise ValueError("id is required")
    data = _load_yaml_round_trip(_current_config_yaml_path())
    if "permissions" not in data:
        return False
    rules = data["permissions"].get("rules")
    if not isinstance(rules, list):
        return False
    new_rules = [r for r in rules if not (isinstance(r, dict) and str(r.get("id") or "").strip() == rid)]
    if len(new_rules) == len(rules):
        return False
    data["permissions"]["rules"] = new_rules
    _dump_yaml_round_trip(_current_config_yaml_path(), data)
    return True


def delete_permissions_approval_override_in_config(override_id: str) -> bool:
    """按 ``id`` 删除 ``approval_overrides`` 中一项；若未找到返回 False。"""
    oid = str(override_id or "").strip()
    if not oid:
        raise ValueError("id is required")
    data = _load_yaml_round_trip(_current_config_yaml_path())
    if "permissions" not in data:
        return False
    ov = data["permissions"].get("approval_overrides")
    if not isinstance(ov, list):
        return False
    new_ov = [x for x in ov if not (isinstance(x, dict) and str(x.get("id") or "").strip() == oid)]
    if len(new_ov) == len(ov):
        return False
    data["permissions"]["approval_overrides"] = new_ov
    _dump_yaml_round_trip(_current_config_yaml_path(), data)
    return True


def _normalize_rule_action(rule: dict[str, Any]) -> None:
    act = str(rule.get("action") or "").strip().lower()
    if act not in _VALID_RULE_ACTION:
        raise ValueError("action must be allow, ask, or deny")
    rule["action"] = act


def _decrypt_model_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """解密模型条目中的 api_key 字段，返回深拷贝不改变原始数据。"""
    reg_mod = sys.modules.get("jiuwenclaw.extensions.registry")
    if reg_mod is None or not hasattr(reg_mod, "ExtensionRegistry"):
        return copy.deepcopy(entries)
    try:
        crypto = reg_mod.ExtensionRegistry.get_instance().get_crypto_provider()
    except Exception:
        return copy.deepcopy(entries)

    result = copy.deepcopy(entries)
    if not crypto:
        return result

    for entry in result:
        mcc = entry.get("model_client_config")
        if isinstance(mcc, dict) and mcc.get("api_key"):
            try:
                mcc["api_key"] = crypto.decrypt(mcc["api_key"])
            except Exception:
                pass
    return result


_ENV_TO_MODEL_CLIENT_CONFIG: dict[str, str] = {
    "MODEL_NAME": "model_name",
    "API_KEY": "api_key",
    "API_BASE": "api_base",
    "MODEL_PROVIDER": "client_provider",
}


def patch_model_config_from_env(
    config: dict[str, Any],
    env_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Patch models from keys present in this reload's env_overrides only.

    Aligns with reload contract: env is incremental; keys omitted are unchanged.
    Does not read os.environ or ENV_CONFIG_DICT — only the current reload payload.
    """
    if not isinstance(env_overrides, dict) or not env_overrides:
        return config

    patches: dict[str, str] = {}
    for env_key, mcc_key in _ENV_TO_MODEL_CLIENT_CONFIG.items():
        if env_key not in env_overrides:
            continue
        raw = env_overrides[env_key]
        if raw is None:
            continue
        value = str(raw).strip()
        if value:
            patches[mcc_key] = value

    if not patches:
        return config

    patched = copy.deepcopy(config)
    models = patched.setdefault("models", {})
    if not isinstance(models, dict):
        return patched

    default_entry = models.get("default")
    if isinstance(default_entry, dict):
        mcc = default_entry.setdefault("model_client_config", {})
        if isinstance(mcc, dict):
            mcc.update(patches)

    defaults = models.get("defaults")
    if isinstance(defaults, list):
        for entry in defaults:
            if not isinstance(entry, dict):
                continue
            mcc = entry.setdefault("model_client_config", {})
            if isinstance(mcc, dict):
                mcc.update(patches)

    model_name = patches.get("model_name")
    if model_name:
        react = patched.get("react")
        if isinstance(react, dict):
            react["model_name"] = model_name
        elif react is None:
            patched["react"] = {"model_name": model_name}

    return patched


def get_default_models(config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """获取默认模型列表，兼容新旧格式。

    优先级：models.defaults（列表） > models.default（单对象） > 环境变量回退
    返回的 api_key 已解密。
    """
    if config is None:
        config = get_config()
    models = config.get("models", {})

    # 新格式：已有 defaults 列表
    if "defaults" in models and isinstance(models["defaults"], list) and models["defaults"]:
        return _decrypt_model_entries(models["defaults"])

    # 旧格式：单个 default 对象 → 包装为列表
    if "default" in models and isinstance(models["default"], dict):
        return _decrypt_model_entries([models["default"]])

    # 回退：从环境变量构造（env var 已在 resolve_env_vars 中解密）
    return [{
        "model_client_config": {
            "api_base": get_local_config("API_BASE", ""),
            "api_key": get_local_config("API_KEY", ""),
            "model_name": get_local_config("MODEL_NAME", ""),
            "client_provider": get_local_config("MODEL_PROVIDER", ""),
            "timeout": 1800,
            "max_retries": 1,
            "verify_ssl": False,
        },
        "model_config_obj": {"temperature": 0.95},
    }]


def update_default_models_in_config(models_list: list[dict[str, Any]]) -> None:
    """将默认模型列表写入 config.yaml 的 models.defaults 段。"""
    data = _load_yaml_round_trip(_current_config_yaml_path())
    if "models" not in data:
        data["models"] = {}
    data["models"]["defaults"] = models_list
    # 同步 models.default 为第一个条目（兼容旧读取方）
    if models_list:
        data["models"]["default"] = models_list[0]
    _dump_yaml_round_trip(_current_config_yaml_path(), data)


def update_memory_forbidden_enabled_in_config(value: bool) -> None:
    """更新 memory.forbidden_memory_definition.enabled（记忆系统敏感信息过滤开关）并写回。"""
    data = _load_yaml_round_trip(_current_config_yaml_path())
    if "memory" not in data:
        data["memory"] = {}
    if "forbidden_memory_definition" not in data["memory"]:
        data["memory"]["forbidden_memory_definition"] = {}
    data["memory"]["forbidden_memory_definition"]["enabled"] = value
    _dump_yaml_round_trip(_current_config_yaml_path(), data)


def update_memory_forbidden_description_in_config(description: dict[str, str]) -> None:
    """更新 memory.forbidden_memory_definition.description（禁止记忆内容描述）并写回。"""
    data = _load_yaml_round_trip(_current_config_yaml_path())
    if "memory" not in data:
        data["memory"] = {}
    if "forbidden_memory_definition" not in data["memory"]:
        data["memory"]["forbidden_memory_definition"] = {}
    if "description" not in data["memory"]["forbidden_memory_definition"]:
        data["memory"]["forbidden_memory_definition"]["description"] = {}
    # 合并描述，保留其他语言的描述
    current_desc = data["memory"]["forbidden_memory_definition"]["description"] or {}
    if isinstance(current_desc, dict):
        data["memory"]["forbidden_memory_definition"]["description"] = {**current_desc, **description}
    else:
        data["memory"]["forbidden_memory_definition"]["description"] = description
    _dump_yaml_round_trip(_current_config_yaml_path(), data)


def update_memory_forbidden_in_config(updates: dict[str, Any]) -> None:
    """更新 memory.forbidden_memory_definition 并写回。"""
    data = _load_yaml_round_trip(_current_config_yaml_path())
    if "memory" not in data:
        data["memory"] = {}
    if "forbidden_memory_definition" not in data["memory"]:
        data["memory"]["forbidden_memory_definition"] = {}
    section = data["memory"]["forbidden_memory_definition"]
    for k, v in updates.items():
        if k == "description" and isinstance(v, dict) and isinstance(section.get("description"), dict):
            section["description"] = {**section["description"], **v}
        else:
            section[k] = v
    _dump_yaml_round_trip(_current_config_yaml_path(), data)


def _deep_merge(
    template: dict[str, Any],
    user: dict[str, Any],
    depth: int = 0,
) -> dict[str, Any]:
    """Recursively merge template with user config, cleaning deprecated fields.

    Rules:
    - Add: fields only in template (new config options)
    - Keep: user values for fields that exist in template (preserve user settings)
    - Remove: fields only in user (deprecated config, cleanup)
    - Max recursion depth: 4 (covers deep nested config like context_engine_config)

    Args:
        template: Template config dict with default values
        user: User config dict
        depth: Current recursion depth

    Returns:
        Merged dict synced with template structure, preserving user values.
    """
    if depth >= 4:
        return user

    result: dict[str, Any] = {}

    for key, template_value in template.items():
        if key not in user:
            result[key] = template_value
        elif isinstance(template_value, dict) and isinstance(user.get(key), dict):
            result[key] = _deep_merge(template_value, user[key], depth + 1)
        else:
            result[key] = user[key]

    return result


def migrate_config_from_template(
    template_path: Path,
    user_config_path: Path,
) -> bool:
    """Sync user config with template structure, preserving user values.

    Three-way merge:
    - Add: new fields from template (new config options)
    - Keep: user values for fields that exist in template
    - Remove: deprecated fields not in template (cleanup)

    This preserves user settings like:
    - models.*.model_config_obj.temperature
    - react.context_engine_config.enabled
    - react.context_engine_config.message_summary_offloader_config.*

    Args:
        template_path: Path to template config.yaml
        user_config_path: Path to user config.yaml

    Returns:
        True if migration was performed, False otherwise.
    """
    if not user_config_path.exists():
        return False

    if not template_path.exists():
        return False

    template_data = _load_yaml_round_trip(template_path)
    user_data = _load_yaml_round_trip(user_config_path)

    if not isinstance(template_data, dict):
        return False

    if user_data is None:
        user_data = {}

    # Deep merge: template provides defaults, user values preserved
    merged_data = _deep_merge(template_data, user_data)

    # Guard against empty merged_data overwriting valid user config
    if merged_data is None or not merged_data:
        return False

    # Only write if there are actual changes
    if merged_data != user_data:
        _dump_yaml_round_trip(user_config_path, merged_data)
        return True

    return False


# ---------- 模型配置管理 ----------
def get_model_names() -> list[str]:
    """获取可切换的模型名称列表。优先从 models.defaults 列表读取"""
    data = get_config_raw()
    models = data.get("models", {})
    defaults_list = models.get("defaults")
    if isinstance(defaults_list, list) and defaults_list:
        names = []
        for entry in defaults_list:
            if not isinstance(entry, dict):
                continue
            name = (entry.get("model_client_config") or {}).get("model_name", "")
            if name:
                names.append(resolve_env_vars(str(name)))
        return names
    skip = {"default", "defaults"}
    return [k for k, v in models.items() if isinstance(v, dict) and k not in skip]


def add_or_update_model_in_config(name: str, model_config: dict[str, Any]) -> None:
    """新增或更新一个模型配置，写入 config.yaml 的 models.<name> 节点。"""
    data = _load_yaml_round_trip(_current_config_yaml_path())
    if "models" not in data:
        data["models"] = {}
    if name not in data["models"]:
        data["models"][name] = model_config
    else:
        existing = data["models"][name]
        for k, v in model_config.items():
            if v is None and k in existing:
                del existing[k]
            else:
                existing[k] = v
    _dump_yaml_round_trip(_current_config_yaml_path(), data)


def get_model_config(name: str) -> dict[str, Any] | None:
    """获取指定模型的原始配置（不解析环境变量）。优先从 models.defaults 列表中按 model_name 查找。"""
    data = get_config_raw()
    models = data.get("models", {})
    defaults_list = models.get("defaults")
    if isinstance(defaults_list, list):
        for entry in defaults_list:
            if not isinstance(entry, dict):
                continue
            entry_name = (entry.get("model_client_config") or {}).get("model_name", "")
            if resolve_env_vars(str(entry_name)) == name:
                return entry
    return models.get(name) if name in models else None


# ===========================================================================
# 文件传输配置
# ===========================================================================

@dataclass
class FileTransferConfig:
    """文件传输配置模型.

    Attributes:
        enabled: 是否启用分布式模式（false=本地模式，true=分布式模式）
        chunk_size: 分片大小（字节），默认 64KB
        max_file_size: 最大文件大小（字节），默认 100MB，0=不限制
        transfer_timeout: 传输超时时间（秒），默认 300 秒
        max_retries: 单分片最大重试次数，默认 3 次
        received_files_dir: 接收文件存储目录，默认 "agent/workspace/received_files"
        cleanup_interval: 临时文件清理间隔（秒），默认 3600 秒
        cleanup_age: 清理超过 N 秒的临时文件，默认 86400 秒（24小时）
        max_concurrent_transfers: 最大并发传输数，默认 5
    """
    enabled: bool = False
    chunk_size: int = 65536  # 64KB
    max_file_size: int = 104857600  # 100MB
    transfer_timeout: int = 300
    max_retries: int = 3
    received_files_dir: str = "agent/workspace/received_files"
    cleanup_interval: int = 3600
    cleanup_age: int = 86400
    max_concurrent_transfers: int = 5

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "FileTransferConfig":
        """从字典创建配置实例."""
        if not data:
            return cls()
        return cls(
            enabled=bool(data.get("enabled", False)),
            chunk_size=int(data.get("chunk_size", 65536)),
            max_file_size=int(data.get("max_file_size", 104857600)),
            transfer_timeout=int(data.get("transfer_timeout", 300)),
            max_retries=int(data.get("max_retries", 3)),
            received_files_dir=str(data.get("received_files_dir", "agent/workspace/received_files")),
            cleanup_interval=int(data.get("cleanup_interval", 3600)),
            cleanup_age=int(data.get("cleanup_age", 86400)),
            max_concurrent_transfers=int(data.get("max_concurrent_transfers", 5)),
        )

    def to_dict(self) -> dict[str, Any]:
        """转换为字典."""
        return {
            "enabled": self.enabled,
            "chunk_size": self.chunk_size,
            "max_file_size": self.max_file_size,
            "transfer_timeout": self.transfer_timeout,
            "max_retries": self.max_retries,
            "received_files_dir": self.received_files_dir,
            "cleanup_interval": self.cleanup_interval,
            "cleanup_age": self.cleanup_age,
            "max_concurrent_transfers": self.max_concurrent_transfers,
        }


# 文件传输配置缓存
_file_transfer_config: FileTransferConfig | None = None


def get_file_transfer_config() -> FileTransferConfig:
    """获取文件传输配置（带缓存）."""
    global _file_transfer_config
    if _file_transfer_config is not None:
        return _file_transfer_config
    config = get_config()
    ft_config = config.get("file_transfer", {}) if isinstance(config, dict) else {}
    _file_transfer_config = FileTransferConfig.from_dict(ft_config)
    return _file_transfer_config


def clear_file_transfer_config_cache() -> None:
    """清除文件传输配置缓存."""
    global _file_transfer_config
    _file_transfer_config = None


def update_file_transfer_in_config(updates: dict[str, Any]) -> None:
    """更新 file_transfer 段并写回."""
    data = _load_yaml_round_trip(_current_config_yaml_path())
    if "file_transfer" not in data:
        data["file_transfer"] = {}
    section = data["file_transfer"]
    for key, value in updates.items():
        section[key] = value
    _dump_yaml_round_trip(_current_config_yaml_path(), data)
    clear_file_transfer_config_cache()

# =====================================================================
# Sandbox 配置读取（环境变量驱动；不再读取 config.yaml::sandbox）
#
# 历史上 claw2b 的 sandbox 段挂在 ``config.yaml::sandbox`` 顶层。出于
# enterprise / K8s 部署场景 (operator 通过 ConfigMap / Deployment env
# 注入), 现已**整段切换到环境变量**, ``config.yaml::sandbox`` 内容**完全
# 不再被读取**——保留这一段 yaml 不会影响行为, 但也不再生效;
# 想启用 sandbox 必须设置对应的 ``JIUWENCLAW_SANDBOX_*`` 环境变量。
#
# 环境变量映射 (前缀全部 ``JIUWENCLAW_SANDBOX_``):
#
#   ENABLED                       (bool: ``1/0/true/false/yes/no/on/off``,
#                                   缺省/空 = false)
#   URL                           (str, 例如 ``http://127.0.0.1:8321``)
#   TYPE                          (str, sandbox provider 名; 缺省/空回落到
#                                   ``jiuwenbox`` —— 这是项目里唯一注册的
#                                   provider, 显式覆盖只有自定义 provider 时才用)
#   STARTUP_MODE                  (str, 仅接受 ``external``; 非法抛 ValueError)
#   PRESERVE_FILE_SHARING_MODE    (str, 仅接受 ``mount``; 缺省回落 ``mount``)
#   EXCLUDED_COMMANDS             (str: JSON 数组 ``["ls", "cat"]`` 或
#                                   :func:`os.pathsep` (linux ``:``) 分隔
#                                   ``ls:cat``; 空值 = 空 list)
#   FILES_ALLOW                   (str: JSON 数组, 元素可以是 path 字符串
#                                   或 ``{path, permissions}`` 对象;
#                                   也接受 :func:`os.pathsep` 分隔的
#                                   纯字符串路径列表)
#   FILES_DENY                    (str: 同 FILES_ALLOW 格式)
#   IDLE_TTL_SECONDS              (int 秒数; ``<= 0`` / 空 = None = 禁用淘汰)
#   IDLE_CHECK_INTERVAL           (int 秒数; ``<= 0`` / 空 = None = 让
#                                   jiuwenbox 服务端用自身默认值)
#
# 注意:
#   - ``startup_mode`` 仅接受 ``external``，``internal`` 之类抛 ``ValueError``。
#     ``external`` 表示 “使用 ``URL`` 端点连接由外部启动的 jiuwenbox”
#     (claw2b 不负责拉起 jiuwenbox; 在 K8s / 企业部署里 jiuwenbox-server 由
#     Deployment / sidecar 等独立托管)。
#   - 不引入 ``policy_file`` 字段 (jiuwenbox 自管 policy)。
#   - 不引入任何 ``update_sandbox_*`` 写回函数 (无 ``/sandbox`` 命令调用方)。
#   - ``IDLE_TTL_SECONDS`` / ``IDLE_CHECK_INTERVAL`` 经
#     :func:`create_sandbox_sysop_card` 透传给 jiuwenbox provider, 由其
#     ``PUT /api/v1/timeout`` 写到 jiuwenbox server 根 policy 上 (per-sandbox
#     policy 的 ``timeout`` 子段不驱动 reaper, 必须改根 policy 才能生效)。
# =====================================================================

_SANDBOX_ENV_PREFIX: str = "JIUWENCLAW_SANDBOX_"

_VALID_SANDBOX_STARTUP_MODES: tuple[str, ...] = ("external",)
_DEFAULT_SANDBOX_STARTUP_MODE: str = "external"

_VALID_PRESERVE_FILE_SHARING_MODES: tuple[str, ...] = ("mount",)
_DEFAULT_PRESERVE_FILE_SHARING_MODE: str = "mount"

# 唯一已注册的 sandbox provider 名。 历史上 ``JIUWENCLAW_SANDBOX_TYPE`` 必填,
# 用户漏配就会让 ``_create_sys_operation`` 静默回落到 local 模式 (现象: "我开了
# sandbox 怎么没连"); 改为空时回落到 ``jiuwenbox``。
_DEFAULT_SANDBOX_TYPE: str = "jiuwenbox"

_SANDBOX_RUNTIME_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "excluded_commands": [],
    "files": {"allow": [], "deny": []},
    "idle_ttl_seconds": None,
    "idle_check_interval": None,
}
_SANDBOX_RUNTIME_KEYS: tuple[str, ...] = tuple(_SANDBOX_RUNTIME_DEFAULTS.keys())


def _read_sandbox_env(suffix: str) -> str | None:
    """读取 ``JIUWENCLAW_SANDBOX_<suffix>`` 环境变量.

    优先经 :func:`get_local_config` (走 agentserver 进程内 ENV_CONFIG_DICT
    + os.environ + 解密钩子), 与项目内其它 env 读取保持一致。
    返回 ``None`` 表示未设置或空字符串。
    """
    name = _SANDBOX_ENV_PREFIX + suffix
    value = get_local_config(name)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


_TRUE_VALUES: frozenset[str] = frozenset({"1", "true", "yes", "y", "on", "t"})
_FALSE_VALUES: frozenset[str] = frozenset({"0", "false", "no", "n", "off", "f"})


def _coerce_bool_env(raw: str | None, *, env_name: str, default: bool = False) -> bool:
    """把环境变量字符串解析为 bool. 未设置 / 空 → ``default``; 非法值抛 ValueError."""
    if raw is None:
        return default
    text = raw.strip().lower()
    if not text:
        return default
    if text in _TRUE_VALUES:
        return True
    if text in _FALSE_VALUES:
        return False
    raise ValueError(
        f"{env_name} must be a boolean string ({sorted(_TRUE_VALUES | _FALSE_VALUES)}), "
        f"got {raw!r}",
    )


def _coerce_optional_positive_int(
    value: Any, *, env_name: str, allow_zero: bool = False,
) -> Optional[int]:
    """把 env / yaml 来的 idle 配置值归一化为 ``Optional[int]``.

    - ``None`` / 缺失 / 空字符串 → ``None``。
    - ``int`` / 数字字符串 → ``int(value)``; ``allow_zero=False`` 时 ``<= 0``
      也归一化为 ``None`` (避免后续负值流到 jiuwenbox 端引发歧义)。
    - 其它 (``bool`` / 不可解析字符串等) → ``ValueError``。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        # bool 是 int 子类, 必须先排掉; ``True`` -> 1 这种隐式转换在配置文件里
        # 几乎肯定是误写, 不要静默放行。
        raise ValueError(f"{env_name} must be a number, not a boolean")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = int(text)
        except ValueError:
            try:
                number = int(float(text))
            except ValueError as exc:
                raise ValueError(
                    f"{env_name} must parse as an integer of seconds, got {value!r}"
                ) from exc
    elif isinstance(value, (int, float)):
        number = int(value)
    else:
        raise ValueError(
            f"{env_name} must be number or string, got {type(value).__name__}"
        )
    if not allow_zero and number <= 0:
        return None
    return number


def _parse_list_env(raw: str | None, *, env_name: str) -> list[Any]:
    """把 env 里的列表型 sandbox 配置解析成 Python list.

    支持两种形态 (优先级):

    1. **JSON 数组** (推荐): 字符串首字符为 ``[``, 整体 ``json.loads`` 解析.
       元素可以是 ``str`` 或 ``{"path": "...", "permissions": "..."}`` 这种
       与 ``files.allow`` / ``files.deny`` 对应的 dict。
    2. **:os.pathsep 分隔的纯字符串**: 例如 ``/etc/foo:/etc/bar``。 适合
       ``EXCLUDED_COMMANDS`` 与简单路径场景, dict 形态的 ``permissions``
       不便表达。

    空字符串 / ``None`` → 返回空 list。 JSON 解析失败时回退到分隔符模式;
    若 JSON 形态明显 (以 ``[`` 开头) 但解析失败, 抛 ``ValueError`` 让用户
    知道是 JSON 写错而不是悄悄变成单字符串。
    """
    if raw is None:
        return []
    text = raw.strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{env_name} starts with '[' but is not valid JSON: {exc}"
            ) from exc
        if not isinstance(parsed, list):
            raise ValueError(f"{env_name} JSON must be an array, got {type(parsed).__name__}")
        return parsed
    return [piece for piece in (p.strip() for p in text.split(os.pathsep)) if piece]


def _normalize_sandbox_startup_mode(value: Any) -> str:
    """归一化 ``sandbox.startup_mode``.

    - ``None`` / 空字符串 → 返回默认 ``external``；
    - ``"external"`` (大小写不敏感, 前后空格) → 返回 ``"external"``；
    - 其它任何取值 (含 ``internal``) → 抛 ``ValueError``。

    显式拒绝 ``internal``: claw2b 在 K8s / 企业部署中, jiuwenbox-server 由
    Deployment / sidecar 等外部进程独立托管, agent-server 完全不 spawn 它,
    只通过 ``JIUWENCLAW_SANDBOX_URL`` 健康检查 + HTTP 调用。 这就是 jiuwenbox
    README 中 ``external`` 的定义; ``internal`` (agent-server 自动拉起
    jiuwenbox 子进程) 在本工程内不实现, 留下名字徒增歧义, 故 schema 收窄。
    """
    if value is None:
        return _DEFAULT_SANDBOX_STARTUP_MODE
    text = str(value).strip().lower()
    if not text:
        return _DEFAULT_SANDBOX_STARTUP_MODE
    if text not in _VALID_SANDBOX_STARTUP_MODES:
        raise ValueError(
            f"JIUWENCLAW_SANDBOX_STARTUP_MODE must be one of "
            f"{_VALID_SANDBOX_STARTUP_MODES}, got {value!r}",
        )
    return text


def _normalize_preserve_file_sharing_mode(value: Any) -> str | None:
    """归一化 ``sandbox.preserve_file_sharing_mode``.

    - ``None`` / 空字符串 → 返回 ``None`` (表示 “未配置”, 调用方按默认 ``mount`` 处理);
    - ``"mount"`` (大小写不敏感, 前后空格) → 返回 ``"mount"``;
    - 其它任何取值 → 抛 ``ValueError``。
    """
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text not in _VALID_PRESERVE_FILE_SHARING_MODES:
        raise ValueError(
            f"JIUWENCLAW_SANDBOX_PRESERVE_FILE_SHARING_MODE must be one of "
            f"{_VALID_PRESERVE_FILE_SHARING_MODES}, got {value!r}",
        )
    return text


def _ensure_sandbox_runtime_shape(runtime: Any) -> dict[str, Any]:
    """填充 sandbox runtime 缺省字段，返回归一化后的 dict（不写盘）。

    ``runtime`` 由 :func:`get_sandbox_runtime` 从 env var 拼装而成；保留
    ``Any`` 入参类型只是让本函数对历史 yaml 输入也能容错 (传 ``None`` /
    非 dict 时返回纯默认值)。
    """
    base = {
        k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
        for k, v in _SANDBOX_RUNTIME_DEFAULTS.items()
    }
    if not isinstance(runtime, dict):
        return base
    out = dict(base)
    if "enabled" in runtime:
        out["enabled"] = bool(runtime["enabled"])
    raw_excluded = runtime.get("excluded_commands")
    if isinstance(raw_excluded, list):
        out["excluded_commands"] = [str(p) for p in raw_excluded if str(p).strip()]
    files = runtime.get("files")
    if isinstance(files, dict):
        allow = files.get("allow")
        deny = files.get("deny")
        out["files"] = {
            "allow": list(allow) if isinstance(allow, list) else [],
            "deny": list(deny) if isinstance(deny, list) else [],
        }
    if "idle_ttl_seconds" in runtime:
        # ``<= 0`` 归一化成 ``None`` (= 禁用淘汰), 与 jiuwenbox server 端
        # ``TimeoutPolicy.idle_timeout`` 的语义对齐。
        out["idle_ttl_seconds"] = _coerce_optional_positive_int(
            runtime["idle_ttl_seconds"],
            env_name="JIUWENCLAW_SANDBOX_IDLE_TTL_SECONDS",
        )
    if "idle_check_interval" in runtime:
        out["idle_check_interval"] = _coerce_optional_positive_int(
            runtime["idle_check_interval"],
            env_name="JIUWENCLAW_SANDBOX_IDLE_CHECK_INTERVAL",
        )
    return out


def get_sandbox_endpoint() -> dict[str, Any]:
    """从 env var 返回 sandbox 接入端点配置.

    Reads:
        - ``JIUWENCLAW_SANDBOX_URL``
        - ``JIUWENCLAW_SANDBOX_TYPE`` (default ``jiuwenbox`` —— 项目内唯一
          已注册的 provider; 不必显式设)
        - ``JIUWENCLAW_SANDBOX_STARTUP_MODE`` (default ``external``,
          非法值抛 ``ValueError``)
        - ``JIUWENCLAW_SANDBOX_PRESERVE_FILE_SHARING_MODE`` (default
          ``mount``)

    返回 key 与历史调用方契约保持一致 (``url`` / ``type`` / ``startup_mode`` /
    ``preserve_file_sharing_mode``), 让 ``interface_deep.py`` 等调用方
    无需改动。
    """
    mode = _normalize_preserve_file_sharing_mode(_read_sandbox_env("PRESERVE_FILE_SHARING_MODE"))
    return {
        "url": _read_sandbox_env("URL") or "",
        "type": _read_sandbox_env("TYPE") or _DEFAULT_SANDBOX_TYPE,
        "startup_mode": _normalize_sandbox_startup_mode(_read_sandbox_env("STARTUP_MODE")),
        "preserve_file_sharing_mode": mode or _DEFAULT_PRESERVE_FILE_SHARING_MODE,
    }


def get_sandbox_runtime() -> dict[str, Any]:
    """从 env var 返回 sandbox runtime 配置 (含缺省字段填充)。

    Reads:
        - ``JIUWENCLAW_SANDBOX_ENABLED`` (bool, 默认 false)
        - ``JIUWENCLAW_SANDBOX_EXCLUDED_COMMANDS`` (JSON 数组 / 分隔列表)
        - ``JIUWENCLAW_SANDBOX_FILES_ALLOW``  (JSON 数组 / 分隔列表)
        - ``JIUWENCLAW_SANDBOX_FILES_DENY``   (JSON 数组 / 分隔列表)
        - ``JIUWENCLAW_SANDBOX_IDLE_TTL_SECONDS`` (int seconds; ``<=0`` 视作禁用)
        - ``JIUWENCLAW_SANDBOX_IDLE_CHECK_INTERVAL`` (int seconds; ``<=0`` 视作未配置)

    返回结构与历史 ``config.yaml::sandbox`` runtime 完全相同 (含
    ``files: {allow, deny}`` 子结构), 让下游 ``interface_deep.py`` /
    ``sysop_builder.py`` 不感知配置源切换。
    """
    raw: dict[str, Any] = {
        "enabled": _coerce_bool_env(
            _read_sandbox_env("ENABLED"),
            env_name="JIUWENCLAW_SANDBOX_ENABLED",
            default=False,
        ),
        "excluded_commands": _parse_list_env(
            _read_sandbox_env("EXCLUDED_COMMANDS"),
            env_name="JIUWENCLAW_SANDBOX_EXCLUDED_COMMANDS",
        ),
        "files": {
            "allow": _parse_list_env(
                _read_sandbox_env("FILES_ALLOW"),
                env_name="JIUWENCLAW_SANDBOX_FILES_ALLOW",
            ),
            "deny": _parse_list_env(
                _read_sandbox_env("FILES_DENY"),
                env_name="JIUWENCLAW_SANDBOX_FILES_DENY",
            ),
        },
        "idle_ttl_seconds": _read_sandbox_env("IDLE_TTL_SECONDS"),
        "idle_check_interval": _read_sandbox_env("IDLE_CHECK_INTERVAL"),
    }
    return _ensure_sandbox_runtime_shape(raw)


_SANDBOX_YAML_TO_ENV: dict[str, str] = {
    "url": "JIUWENCLAW_SANDBOX_URL",
    "type": "JIUWENCLAW_SANDBOX_TYPE",
    "enabled": "JIUWENCLAW_SANDBOX_ENABLED",
}


def _sandbox_yaml_to_env_overlay(sandbox: Any) -> dict[str, str]:
    """从 ``config_base['sandbox']`` 抽 url/type/enabled, 翻译成 env overlay key。

    缺失字段不进 overlay, 让 env var fallback 生效。``enabled`` 归一化为
    ``'true'`` / ``'false'``。非法 bool 抛 ``ValueError``, 让 reload 整体失败。

    放在本模块 (config.py) 是因为 lazy-path 首次 ``_create_sys_operation``
    在 ``reload_agent_config`` overlay 绑定之前就执行：必须在 active env
    (``ENV_CONFIG_DICT``) 写入, 让 :func:`get_sandbox_endpoint` /
    :func:`get_sandbox_runtime` 在首个请求来时就读到 yaml 的值。
    """
    if not isinstance(sandbox, dict):
        return {}
    out: dict[str, str] = {}
    for yaml_key, env_key in _SANDBOX_YAML_TO_ENV.items():
        if yaml_key not in sandbox or sandbox[yaml_key] is None:
            continue
        value = sandbox[yaml_key]
        if yaml_key == "enabled":
            if isinstance(value, bool):
                out[env_key] = "true" if value else "false"
            else:
                text = str(value).strip().lower()
                if text in _TRUE_VALUES:
                    out[env_key] = "true"
                elif text in _FALSE_VALUES:
                    out[env_key] = "false"
                else:
                    raise ValueError(
                        f"sandbox.{yaml_key} must be a boolean string, got {value!r}"
                    )
        else:
            text = str(value).strip()
            if text:
                out[env_key] = text
    return out


# ---- Relay / front team config sync helpers ----
def _require_dict(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object")
    return value


def _require_non_empty_string(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be non-empty")
    return text


def _transform_front_team_model_config(model_raw: dict[str, Any]) -> dict[str, Any]:
    model_client_config: dict[str, Any] = {}
    model_request_config: dict[str, Any] = {}

    # 从 models.defaults 列表中按 #index 查找完整配置
    model_value = model_raw.get("model")
    if model_value and isinstance(model_value, str) and "#" in model_value:
        sep = model_value.rfind("#")
        model_name_part = model_value[:sep]
        index_part = model_value[sep + 1:]
        try:
            target_index = int(index_part)
        except ValueError:
            target_index = None
        if target_index is not None:
            defaults_list = get_config_raw().get("models", {}).get("defaults")
            if isinstance(defaults_list, list) and 0 <= target_index < len(defaults_list):
                entry = defaults_list[target_index]
                if isinstance(entry, dict):
                    mcc = entry.get("model_client_config")
                    if isinstance(mcc, dict):
                        model_client_config.update({
                            k: v for k, v in mcc.items()
                            if k not in ("model_name",) and v is not None
                        })
                        model_request_config["model"] = resolve_env_vars(str(mcc.get("model_name", model_name_part)))
                    mco = entry.get("model_config_obj")
                    if isinstance(mco, dict):
                        model_request_config.update(mco)

    # 前端字段覆盖（优先级高于 #index 解析）
    if "provider" in model_raw and model_raw["provider"] is not None:
        model_client_config["client_provider"] = model_raw["provider"]
    if "api_base" in model_raw and model_raw["api_base"] is not None:
        model_client_config["api_base"] = model_raw["api_base"]
    if "api_key" in model_raw and model_raw["api_key"] is not None:
        model_client_config["api_key"] = model_raw["api_key"]
    if "model" in model_raw and model_raw["model"] is not None:
        # 若包含 #index，提取纯 model_name
        raw_model = model_raw["model"]
        if isinstance(raw_model, str) and "#" in raw_model:
            model_request_config["model"] = raw_model[:raw_model.rfind("#")]
        else:
            model_request_config["model"] = raw_model

    transformed: dict[str, Any] = {}
    if model_client_config:
        model_client_config.setdefault("timeout", 1800)
        model_client_config.setdefault("verify_ssl", False)
        model_client_config.setdefault("custom_headers", {})
        transformed["model_client_config"] = model_client_config
    if model_request_config:
        transformed["model_request_config"] = model_request_config
    return transformed


def _transform_front_team_agent_spec(agent_key: str, agent_raw: Any) -> dict[str, Any]:
    agent_config = _require_dict(agent_raw, f"agents.{agent_key}")
    transformed: dict[str, Any] = {}

    if "model" in agent_config:
        model_raw = _require_dict(agent_config.get("model"), f"agents.{agent_key}.model")
        transformed_model = _transform_front_team_model_config(model_raw)
        if transformed_model:
            transformed["model"] = transformed_model

    for field_name in ("skills", "workspace", "max_iterations", "completion_timeout"):
        if field_name in agent_config:
            transformed[field_name] = copy.deepcopy(agent_config[field_name])

    return transformed


def _resolve_front_team_agent_spec(
    agents_raw: dict[str, Any],
    agent_key: Any,
    *,
    field_name: str,
) -> dict[str, Any]:
    resolved_key = _require_non_empty_string(agent_key, field_name)
    if resolved_key not in agents_raw:
        raise ValueError(f"{field_name} references unknown agent_key: {resolved_key}")
    return _transform_front_team_agent_spec(resolved_key, agents_raw[resolved_key])


def _build_modes_team_mapping(front_payload: dict[str, Any]) -> dict[str, Any]:
    agents_raw = _require_dict(front_payload.get("agents"), "agents")
    teams_raw = front_payload.get("team")
    if teams_raw is None:
        return {}
    if not isinstance(teams_raw, list):
        raise ValueError("team must be an array")

    team_mapping: dict[str, Any] = {}
    seen_team_names: set[str] = set()

    for team_index, team_item in enumerate(teams_raw):
        team_raw = _require_dict(team_item, f"team[{team_index}]")
        team_name = _require_non_empty_string(team_raw.get("team_name"), f"team[{team_index}].team_name")
        if team_name in seen_team_names:
            raise ValueError(f"duplicate team_name: {team_name}")
        seen_team_names.add(team_name)

        transformed_team: dict[str, Any] = {}
        for key, value in team_raw.items():
            if key in {"leader", "teammate", "predefined_members"}:
                continue
            transformed_team[key] = value
        transformed_team["team_name"] = team_name
        transformed_team.setdefault("enable_swarmflow", False)

        leader_raw = _require_dict(team_raw.get("leader"), f"team[{team_index}].leader")
        transformed_team["leader"] = {
            key: leader_raw[key]
            for key in ("member_name", "display_name", "persona")
            if key in leader_raw
        }
        transformed_team["leader"]["agent_key"] = leader_raw.get("agent_key", "")
        leader_name = _require_non_empty_string(
            leader_raw.get("member_name"),
            f"team[{team_index}].leader.member_name",
        )
        transformed_team["leader"]["member_name"] = leader_name
        leader_agent_spec = _resolve_front_team_agent_spec(
            agents_raw,
            leader_raw.get("agent_key"),
            field_name=f"team[{team_index}].leader.agent_key",
        )

        teammate_raw = team_raw.get("teammate")
        teammate_agent_spec: dict[str, Any] | None = None
        if teammate_raw is not None:
            teammate_raw = _require_dict(teammate_raw, f"team[{team_index}].teammate")
            teammate_agent_spec = _resolve_front_team_agent_spec(
                agents_raw,
                teammate_raw.get("agent_key"),
                field_name=f"team[{team_index}].teammate.agent_key",
            )
            transformed_team["teammate"] = {"agent_key": teammate_raw.get("agent_key", "")}

        predefined_members_raw = team_raw.get("predefined_members", [])
        if predefined_members_raw is None:
            predefined_members_raw = []
        if not isinstance(predefined_members_raw, list):
            raise ValueError(f"team[{team_index}].predefined_members must be an array")

        transformed_members: list[dict[str, Any]] = []
        transformed_agents: dict[str, Any] = {"leader": leader_agent_spec}
        seen_member_names: set[str] = {leader_name}

        for member_index, member_item in enumerate(predefined_members_raw):
            member = _require_dict(
                member_item,
                f"team[{team_index}].predefined_members[{member_index}]",
            )
            member_name = _require_non_empty_string(
                member.get("member_name"),
                f"team[{team_index}].predefined_members[{member_index}].member_name",
            )
            if member_name in seen_member_names:
                raise ValueError(
                    f"duplicate member_name in team[{team_index}]: {member_name}"
                )
            seen_member_names.add(member_name)
            transformed_member = {
                key: member[key]
                for key in ("member_name", "display_name", "role_type", "persona", "prompt_hint", "agent_key")
                if key in member
            }
            transformed_member["member_name"] = member_name
            transformed_members.append(transformed_member)

            member_agent_spec = _resolve_front_team_agent_spec(
                agents_raw,
                member.get("agent_key"),
                field_name=f"team[{team_index}].predefined_members[{member_index}].agent_key",
            )
            transformed_agents[member_name] = member_agent_spec

        if transformed_members:
            transformed_team["predefined_members"] = transformed_members

        if teammate_agent_spec is not None:
            transformed_agents["teammate"] = copy.deepcopy(teammate_agent_spec)

        transformed_team["agents"] = transformed_agents
        team_mapping[team_name] = transformed_team

    return team_mapping


def _build_front_agent_registry(front_payload: dict[str, Any]) -> dict[str, Any]:
    agents_raw = _require_dict(front_payload.get("agents"), "agents")
    registry: dict[str, Any] = {}
    for agent_key, agent_raw in agents_raw.items():
        resolved_key = _require_non_empty_string(agent_key, f"agents.{agent_key}")
        registry[resolved_key] = _transform_front_team_agent_spec(resolved_key, agent_raw)
    return registry


def replace_teams_in_config(front_payload: dict[str, Any]) -> None:
    """Replace ``modes.team`` using the frontend team-editor payload.

    If team array is empty, delete ``modes.team`` from config.
    """
    if not isinstance(front_payload, dict):
        raise ValueError("payload must be an object")

    teams_raw = front_payload.get("team")
    data = _load_yaml_round_trip(_current_config_yaml_path())

    panel_cfg_modified = False
    agent_registry = None
    if "agents" in front_payload:
        agent_registry = _build_front_agent_registry(front_payload)
        panel_cfg = data.get("web_config_panel")
        if not isinstance(panel_cfg, dict):
            panel_cfg = {}
            data["web_config_panel"] = panel_cfg
        if agent_registry:
            panel_cfg["agent_team_agents"] = agent_registry
        else:
            panel_cfg.pop("agent_team_agents", None)
        panel_cfg_modified = True

    # 空数组：删除 modes.team 配置项
    if isinstance(teams_raw, list) and not teams_raw:
        if "modes" in data and isinstance(data["modes"], dict) and "team" in data["modes"]:
            del data["modes"]["team"]
        _dump_yaml_round_trip(_current_config_yaml_path(), data)
        return

    # 非空数组：正常构建并保存
    team_mapping = _build_modes_team_mapping(front_payload)

    data = _load_yaml_round_trip(_current_config_yaml_path())
    # Merge web_config_panel back into reloaded data (it was written earlier in this function)
    if panel_cfg_modified:
        if "web_config_panel" not in data or not isinstance(data.get("web_config_panel"), dict):
            data["web_config_panel"] = {}
        if agent_registry:
            data["web_config_panel"]["agent_team_agents"] = agent_registry
        else:
            data.get("web_config_panel", {}).pop("agent_team_agents", None)
    if "modes" not in data or not isinstance(data["modes"], dict):
        data["modes"] = {}
    data["modes"]["team"] = team_mapping
    _dump_yaml_round_trip(_current_config_yaml_path(), data)


def _ensure_config_object(parent: dict[str, Any], key: str, path: str) -> dict[str, Any]:
    value = parent.get(key)
    if value is None:
        value = {}
        parent[key] = value
    if not isinstance(value, dict):
        raise ValueError(f"{path} config must be an object")
    return value


def update_swarmflow_enabled_in_config(enabled: bool) -> None:
    """Update ``modes.team.jiuwen_team.enable_swarmflow`` in config.yaml."""
    data = _load_yaml_round_trip(_current_config_yaml_path())
    current = data
    path_so_far: list[str] = []
    for segment in SWARMFLOW_ENABLED_CONFIG_PATH[:-1]:
        path_so_far.append(segment)
        current = _ensure_config_object(current, segment, ".".join(path_so_far))
    current[SWARMFLOW_ENABLED_CONFIG_PATH[-1]] = bool(enabled)
    _dump_yaml_round_trip(_current_config_yaml_path(), data)


def get_mcp_servers() -> list[dict[str, Any]]:
    """读取 config.yaml 中的 mcp.servers（原始结构，不解析环境变量）。"""
    data = get_config_raw()
    mcp_cfg = data.get("mcp", {})
    if not isinstance(mcp_cfg, dict):
        return []
    servers = mcp_cfg.get("servers", [])
    if not isinstance(servers, list):
        return []
    return [item for item in servers if isinstance(item, dict)]


def upsert_mcp_server_in_config(server: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """新增或更新 mcp.servers 条目，返回（条目, 是否创建）。"""
    name = str(server.get("name", "")).strip()
    if not name:
        raise ValueError("MCP server name is required")
    data = _load_yaml_round_trip(_current_config_yaml_path())
    if "mcp" not in data or not isinstance(data["mcp"], dict):
        data["mcp"] = {}
    mcp_cfg = data["mcp"]
    servers = mcp_cfg.get("servers")
    if not isinstance(servers, list):
        servers = []
        mcp_cfg["servers"] = servers

    created = True
    for idx, item in enumerate(servers):
        if not isinstance(item, dict):
            continue
        if str(item.get("name", "")).strip() != name:
            continue
        servers[idx] = server
        created = False
        break
    else:
        servers.append(server)
    _dump_yaml_round_trip(_current_config_yaml_path(), data)
    return server, created


def set_mcp_server_enabled_in_config(name: str, enabled: bool) -> dict[str, Any]:
    """切换 mcp.servers 指定 name 的 enabled 状态并返回更新后的条目。"""
    target = str(name or "").strip()
    if not target:
        raise ValueError("MCP server name is required")
    data = _load_yaml_round_trip(_current_config_yaml_path())
    if "mcp" not in data or not isinstance(data["mcp"], dict):
        raise KeyError(f"MCP server '{target}' not found")
    servers = data["mcp"].get("servers", [])
    if not isinstance(servers, list):
        raise KeyError(f"MCP server '{target}' not found")
    for item in servers:
        if not isinstance(item, dict):
            continue
        if str(item.get("name", "")).strip() != target:
            continue
        item["enabled"] = bool(enabled)
        _dump_yaml_round_trip(_current_config_yaml_path(), data)
        return dict(item)
    raise KeyError(f"MCP server '{target}' not found")


def get_mcp_server_config(name: str) -> dict[str, Any] | None:
    """按名称读取单个 mcp server 配置（原始结构）。"""
    target = str(name or "").strip()
    if not target:
        return None
    for item in get_mcp_servers():
        if str(item.get("name", "")).strip() == target:
            return item
    return None


def remove_mcp_server_in_config(name: str) -> dict[str, Any]:
    """删除指定 mcp server 配置并返回被删除的条目。"""
    target = str(name or "").strip()
    if not target:
        raise ValueError("MCP server name is required")
    data = _load_yaml_round_trip(_current_config_yaml_path())
    mcp_cfg = data.get("mcp")
    if not isinstance(mcp_cfg, dict):
        raise KeyError(f"MCP server '{target}' not found")
    servers = mcp_cfg.get("servers", [])
    if not isinstance(servers, list):
        raise KeyError(f"MCP server '{target}' not found")
    for idx, item in enumerate(servers):
        if not isinstance(item, dict):
            continue
        if str(item.get("name", "")).strip() != target:
            continue
        removed = dict(item)
        del servers[idx]
        _dump_yaml_round_trip(_current_config_yaml_path(), data)
        return removed
    raise KeyError(f"MCP server '{target}' not found")


# ---------------------------------------------------------------------------
# react.subagents 段操作
# ---------------------------------------------------------------------------


def upsert_subagent_in_config(name: str, enabled: bool = True) -> None:
    """在 react.subagents.<name> 中添加或更新 agent 启用状态。

    自动创建不存在的 react / subagents 段。
    保留已有的其他 subagent 配置键（如 max_iterations 等）。
    """
    target = str(name or "").strip()
    if not target:
        raise ValueError("subagent name is required")
    data = _load_yaml_round_trip(_current_config_yaml_path())
    if "react" not in data or not isinstance(data["react"], dict):
        data["react"] = {}
    react = data["react"]
    if "subagents" not in react or not isinstance(react["subagents"], dict):
        react["subagents"] = {}
    subagents = react["subagents"]
    if target not in subagents or not isinstance(subagents[target], dict):
        subagents[target] = {}
    subagents[target]["enabled"] = bool(enabled)
    _dump_yaml_round_trip(_current_config_yaml_path(), data)


def remove_subagent_from_config(name: str) -> bool:
    """从 react.subagents.<name> 中删除 agent 条目。

    Returns:
        True: 找到并删除
        False: 条目不存在
    """
    target = str(name or "").strip()
    if not target:
        raise ValueError("subagent name is required")
    data = _load_yaml_round_trip(_current_config_yaml_path())
    react = data.get("react")
    if not isinstance(react, dict):
        return False
    subagents = react.get("subagents")
    if not isinstance(subagents, dict):
        return False
    if target not in subagents:
        return False
    del subagents[target]
    _dump_yaml_round_trip(_current_config_yaml_path(), data)
    return True



