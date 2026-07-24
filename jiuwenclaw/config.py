# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import copy
import logging
import os
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from ruamel.yaml import YAML
import yaml
from jiuwenclaw.local_env_config import get_local_config


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


# Module-level cache for get_config() to avoid repeated YAML disk reads.
# Expired by TTL (_CONFIG_CACHE_TTL_SECONDS) or explicitly via clear_config_cache().
_config_cache: dict[str, Any] | None = None
_config_cache_time: float = 0.0
_CONFIG_CACHE_TTL_SECONDS: float = 20.0
_config_lock = threading.Lock()
_config_version: int = 0


def get_config():
    """Return the merged, env-var-resolved config dict.

    Results are cached at module level with a TTL of
    ``_CONFIG_CACHE_TTL_SECONDS`` (default 20s).  The cache is also
    explicitly invalidated by ``clear_config_cache()`` (called on config
    reload, set_config, and any _dump_yaml_round_trip write).

    **WARNING**: The returned dict is a shared reference. Callers MUST NOT
    mutate it (including nested dicts/lists), as changes will affect all
    other callers within the TTL window. Use ``copy.deepcopy()`` if you
    need to modify the result.
    """
    global _config_cache, _config_cache_time, _config_version
    now = time.monotonic()

    # Fast path: check cache validity under lock
    with _config_lock:
        if _config_cache is not None and (now - _config_cache_time) < _CONFIG_CACHE_TTL_SECONDS:
            return _config_cache
        read_version = _config_version

    # Slow path: cache expired or missing, recompute (lock released during I/O)
    config_base = get_merged_config_dict()
    new_cache = resolve_env_vars(config_base)

    # Update cache only if no invalidation occurred during the read
    with _config_lock:
        if _config_version == read_version:
            _config_cache = new_cache
            _config_cache_time = time.monotonic()

    return new_cache


def clear_config_cache() -> None:
    """Invalidate the module-level config cache.

    The next ``get_config()`` call will re-read from disk.
    """
    global _config_cache, _config_cache_time, _config_version
    with _config_lock:
        _config_cache = None
        _config_cache_time = 0.0
        _config_version += 1


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


def build_permissions_tools_list_view(
    catalog_by_name: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Build permissions UI list: runtime catalog tools with explicit/default levels."""
    from jiuwenclaw.agentserver.tool_catalog import ui_list_short_description

    catalog = dict(catalog_by_name or {})
    explicit = get_permissions_tools().get("tools")
    if not isinstance(explicit, dict):
        explicit = {}
    default_level = get_permissions_defaults_level()

    tools_out: list[dict[str, Any]] = []
    for name in sorted(catalog.keys()):
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
#   STARTUP_MODE                  (str, 接受 ``internal`` / ``external``;
#                                   缺省/空回落 ``internal``。``internal`` =
#                                   agent-server 自动 spawn jiuwenbox-server
#                                   子进程; ``external`` = 外部独立托管, 仅健康
#                                   检查 + HTTP 调用)
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
#   - ``startup_mode`` 接受 ``internal`` 与 ``external``, 默认 ``internal``。
#     ``internal``: agent-server 启动时自动 spawn ``jiuwenbox-server`` 子进程
#     (由 ``jiuwenclaw/agentserver/jiuwenbox_runner.py`` 的 ``JiuwenBoxRunner``
#     管理), 适合桌面 / 单机部署; ``external``: jiuwenbox-server 由 K8s /
#     企业部署的 Deployment / sidecar 等独立托管, agent-server 只健康检查 +
#     HTTP 调用 (运维显式设 ``STARTUP_MODE=external``)。
#   - 不引入 ``policy_file`` 字段 (jiuwenbox 自管 policy)。
#   - 不引入任何 ``update_sandbox_*`` 写回函数 (无 ``/sandbox`` 命令调用方)。
#   - ``IDLE_TTL_SECONDS`` / ``IDLE_CHECK_INTERVAL`` 经
#     :func:`create_sandbox_sysop_card` 透传给 jiuwenbox provider, 由其
#     ``PUT /api/v1/timeout`` 写到 jiuwenbox server 根 policy 上 (per-sandbox
#     policy 的 ``timeout`` 子段不驱动 reaper, 必须改根 policy 才能生效)。
# =====================================================================

_SANDBOX_ENV_PREFIX: str = "JIUWENCLAW_SANDBOX_"

_VALID_SANDBOX_STARTUP_MODES: tuple[str, ...] = ("external", "internal")
_DEFAULT_SANDBOX_STARTUP_MODE: str = "internal"

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

    - ``None`` / 空字符串 → 返回默认 ``internal``；
    - ``"internal"`` / ``"external"`` (大小写不敏感, 前后空格) → 原样返回；
    - 其它任何取值 → 抛 ``ValueError``。

    ``internal``: agent-server 启动时自动 spawn ``jiuwenbox-server`` 子进程
    (由 ``jiuwenclaw/agentserver/jiuwenbox_runner.py`` 的 ``JiuwenBoxRunner``
    管理), 适合桌面 / 单机部署 —— 用户无感, 无需手动起 jiuwenbox-server。
    ``external``: jiuwenbox-server 由 Deployment / sidecar 等外部进程独立
    托管, agent-server 只通过 ``JIUWENCLAW_SANDBOX_URL`` 健康检查 + HTTP 调用,
    适合 K8s / 企业部署 (运维显式设 ``JIUWENCLAW_SANDBOX_STARTUP_MODE=external``)。
    默认 ``internal`` 与 develop (jiuwenswarm) 一致。
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