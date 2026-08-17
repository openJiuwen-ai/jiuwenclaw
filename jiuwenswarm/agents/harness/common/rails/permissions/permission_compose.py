# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""JiuwenSwarm 产品权限合成：三档 preset + Global/User/Session。

agent-core 的 PermissionEngine 只消费本模块产出的烘焙配置。
"""

from __future__ import annotations

import logging
from copy import deepcopy
from types import SimpleNamespace
from typing import Any, Literal

from jiuwenswarm.agents.harness.common.rails.permissions.mode_presets import get_mode_preset

logger = logging.getLogger(__name__)

PermissionMode = Literal["full_access", "auto", "strict"]
SandboxIntent = Literal["optional", "required"]
SeverityMap = Literal["normal", "strict"]
VALID_PERMISSION_MODES: frozenset[str] = frozenset({"full_access", "auto", "strict"})
_DEFAULT_MODE: PermissionMode = "auto"
_LEVEL_RANK = {"allow": 0, "ask": 1, "deny": 2}
_PATH_TOOLS_FALLBACK = frozenset({
    "read_file",
    "write_file",
    "edit_file",
    "read_text_file",
    "write_text_file",
    "write",
    "read",
    "glob_file_search",
    "glob",
    "list_dir",
    "list_files",
    "grep",
    "search_replace",
    "send_file_to_user",
})

# Product floor when agent-core builtin_rules.yaml / sensitive_paths is missing.
# Keep glob forms (``**/.ssh/**``) so YAML overlays with the same pattern cannot
# widen a builtin deny just because core expanded ``~/.ssh/**`` to $HOME.
_BUILTIN_SENSITIVE_PATH_FALLBACK: list[dict[str, Any]] = [
    {
        "id": "any_ssh",
        "path": "**/.ssh/**",
        "match": "glob",
        "read": "deny",
        "write": "deny",
        "exec": "deny",
        "layer": "builtin",
    },
    {
        "id": "home_ssh",
        "path": "~/.ssh/**",
        "match": "glob",
        "read": "deny",
        "write": "deny",
        "exec": "deny",
        "layer": "builtin",
    },
    {
        "id": "any_env",
        "path": "**/.env*",
        "match": "glob",
        "read": "deny",
        "write": "deny",
        "exec": "deny",
        "layer": "builtin",
    },
    {
        "id": "any_id_rsa",
        "path": "**/id_rsa",
        "match": "glob",
        "read": "deny",
        "write": "deny",
        "exec": "deny",
        "layer": "builtin",
    },
    {
        "id": "any_id_rsa_ext",
        "path": "**/id_rsa.*",
        "match": "glob",
        "read": "deny",
        "write": "deny",
        "exec": "deny",
        "layer": "builtin",
    },
    {
        "id": "any_id_ed25519",
        "path": "**/id_ed25519",
        "match": "glob",
        "read": "deny",
        "write": "deny",
        "exec": "deny",
        "layer": "builtin",
    },
    {
        "id": "any_id_ed25519_ext",
        "path": "**/id_ed25519.*",
        "match": "glob",
        "read": "deny",
        "write": "deny",
        "exec": "deny",
        "layer": "builtin",
    },
    {
        "id": "any_pem",
        "path": "**/*.pem",
        "match": "glob",
        "read": "deny",
        "write": "deny",
        "exec": "deny",
        "layer": "builtin",
    },
    {
        "id": "any_pfx",
        "path": "**/*.pfx",
        "match": "glob",
        "read": "deny",
        "write": "deny",
        "exec": "deny",
        "layer": "builtin",
    },
    {
        "id": "any_key",
        "path": "**/*.key",
        "match": "glob",
        "read": "deny",
        "write": "deny",
        "exec": "deny",
        "layer": "builtin",
    },
]


def _expand_path_pattern(raw: str) -> str:
    from pathlib import Path

    text = (raw or "").strip().replace("\\", "/")
    if not text.startswith("~/") and text != "~":
        return text
    root = Path.home().resolve()
    rest = "" if text == "~" else text[2:]
    if not rest:
        return root.as_posix().rstrip("/") + "/**"
    return f"{root.as_posix().rstrip('/')}/{rest.lstrip('/')}"


def _builtin_sensitive_path_entries() -> list[dict[str, Any]]:
    core: list[dict[str, Any]] = []
    try:
        from openjiuwen.harness.security.fileguard.sensitive_paths import get_builtin_sensitive_path_entries

        core = list(get_builtin_sensitive_path_entries() or [])
    except ImportError:
        logger.debug("[PermissionEngine] builtin_sensitive_paths.unavailable; using product fallback")
    except Exception:
        logger.warning(
            "[PermissionEngine] builtin_sensitive_paths.load_failed; using product fallback",
            exc_info=True,
        )
    if not core:
        return [deepcopy(item) for item in _BUILTIN_SENSITIVE_PATH_FALLBACK]
    return _merge_builtin_sensitive_paths(_BUILTIN_SENSITIVE_PATH_FALLBACK, core)


def _as_dict(raw: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return deepcopy(raw)


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            name = item.strip()
            if name not in out:
                out.append(name)
    return out


def _normalize_tools_to_lists(cfg: dict[str, Any]) -> None:
    """旧 ``tools: {name: ask|deny|allow}`` → ask_tools / deny_tools / allow_tools。"""
    tools = cfg.get("tools")
    if not isinstance(tools, dict):
        return
    ask = list(_as_str_list(cfg.get("ask_tools")))
    deny = list(_as_str_list(cfg.get("deny_tools")))
    allow = list(_as_str_list(cfg.get("allow_tools")))
    for name, level in tools.items():
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(level, str):
            continue
        lv = level.strip().lower()
        tool = name.strip()
        if lv == "deny":
            if tool not in deny:
                deny.append(tool)
            if tool in ask:
                ask.remove(tool)
            if tool in allow:
                allow.remove(tool)
        elif lv == "ask":
            if tool not in deny and tool not in ask:
                ask.append(tool)
            if tool in allow:
                allow.remove(tool)
        elif lv == "allow":
            if tool not in deny and tool not in ask and tool not in allow:
                allow.append(tool)
    cfg.pop("tools", None)
    if ask:
        cfg["ask_tools"] = ask
    else:
        cfg.pop("ask_tools", None)
    if deny:
        cfg["deny_tools"] = deny
    else:
        cfg.pop("deny_tools", None)
    if allow:
        cfg["allow_tools"] = allow
    else:
        cfg.pop("allow_tools", None)


def _tag_rules(raw: Any, layer: str) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        tagged = deepcopy(item)
        tagged["_config_layer"] = layer
        out.append(tagged)
    return out


def _merge_rule_lists(*lists: Any) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw in lists:
        if not isinstance(raw, list):
            continue
        for item in raw:
            if not isinstance(item, dict):
                continue
            rid = item.get("id")
            if isinstance(rid, str) and rid:
                if rid in seen_ids:
                    continue
                seen_ids.add(rid)
            merged.append(deepcopy(item))
    return merged


def _merge_file_guard(
    base: dict[str, Any] | None,
    *overlays: dict[str, Any] | None,
    force_enabled: bool | None,
) -> dict[str, Any]:
    fg: dict[str, Any] = deepcopy(base) if isinstance(base, dict) else {}
    for overlay in overlays:
        if not isinstance(overlay, dict):
            continue
        for key, value in overlay.items():
            if key == "paths":
                continue
            if key in ("defaults", "workspace") and isinstance(value, dict):
                existing = fg.get(key) if isinstance(fg.get(key), dict) else {}
                merged_axis = dict(existing)
                merged_axis.update(value)
                fg[key] = merged_axis
            else:
                fg[key] = deepcopy(value)
        paths = _merge_rule_lists(fg.get("paths"), overlay.get("paths"))
        if paths:
            fg["paths"] = paths
    if force_enabled is not None:
        fg["enabled"] = force_enabled
    return fg


def _path_pattern_key(entry: dict[str, Any]) -> str:
    path = _expand_path_pattern(str(entry.get("path") or "")).lower()
    return f"{str(entry.get('match') or 'glob').lower()}|{path}"


def _axis_level(entry: dict[str, Any], axis: str) -> str:
    return str(entry.get(axis) or "ask").strip().lower()


def _strictest_axis(a: str, b: str) -> str:
    return a if _LEVEL_RANK.get(a, 1) >= _LEVEL_RANK.get(b, 1) else b


def _merge_builtin_sensitive_paths(
    paths: list[Any] | None,
    builtins: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge overlays with builtins; builtin levels win when stricter (non-relaxable floor)."""
    merged: dict[str, dict[str, Any]] = {}
    for item in list(paths or []) + list(builtins):
        if not isinstance(item, dict):
            continue
        normalized = deepcopy(item)
        normalized["path"] = _expand_path_pattern(str(normalized.get("path") or ""))
        key = _path_pattern_key(normalized)
        cur = merged.get(key)
        if cur is None:
            merged[key] = normalized
            continue
        for axis in ("read", "write", "exec"):
            cur[axis] = _strictest_axis(
                _axis_level(cur, axis),
                _axis_level(normalized, axis),
            )
        if normalized.get("layer") == "builtin":
            cur["layer"] = "builtin"
            if normalized.get("id"):
                cur["id"] = normalized["id"]
        # Prefer expanded absolute path over raw ~/ overlay form
        cur["path"] = normalized["path"]
        merged[key] = cur
    return list(merged.values())


def _project_tool_lists(cfg: dict[str, Any]) -> None:
    """投影 deny/ask/allow → ``tools`` dict；优先级 deny > ask > allow。"""
    deny = set(_as_str_list(cfg.get("deny_tools")))
    ask = [t for t in _as_str_list(cfg.get("ask_tools")) if t not in deny]
    ask_set = set(ask)
    allow = [
        t for t in _as_str_list(cfg.get("allow_tools"))
        if t not in deny and t not in ask_set
    ]
    tools: dict[str, str] = {}
    for name in sorted(deny):
        tools[name] = "deny"
    for name in ask:
        tools[name] = "ask"
    for name in allow:
        tools[name] = "allow"
    if tools:
        cfg["tools"] = tools
    else:
        cfg.pop("tools", None)


def _inject_strict_path_tool_allows(cfg: dict[str, Any]) -> None:
    """Strict 下路径类工具改为 allow，由 file_guard 按目录轴决定。

    用户显式 ask/deny 名单优先，不覆盖。
    """
    try:
        from openjiuwen.harness.security.toolguard.tiered_policy import _PATH_TOOLS

        path_tools = _PATH_TOOLS
    except ImportError:
        path_tools = _PATH_TOOLS_FALLBACK

    deny = set(_as_str_list(cfg.get("deny_tools")))
    ask = set(_as_str_list(cfg.get("ask_tools")))
    allow = list(_as_str_list(cfg.get("allow_tools")))
    changed = False
    for name in sorted(path_tools):
        if name in deny or name in ask or name in allow:
            continue
        allow.append(name)
        changed = True
    if changed:
        cfg["allow_tools"] = allow


class PermissionModeController:
    """合成三层配置 + mode preset → EffectivePermissions。"""

    @staticmethod
    def migrate_legacy(
        raw: dict[str, Any] | None,
        *,
        is_overlay: bool = False,
    ) -> dict[str, Any]:
        """迁移旧字段到产品 ``mode`` / ask_tools / deny_tools / allow_tools。

        - ``enabled: false`` → ``enabled: true`` + ``mode: full_access``（仅主配置）
        - 主配置无 ``mode`` 时：``permission_mode: strict`` → ``mode: strict``；否则 → ``auto``
        - overlay（User/Session）不注入默认 mode，避免覆盖 Global
        - 已有 ``mode`` 时忽略 ``permission_mode``
        - ``tools: {name: ask|deny|allow}`` → ask_tools / deny_tools / allow_tools
        - 顶层产品 ``defaults`` 删除（由 mode 注入）
        """
        cfg = _as_dict(raw)
        if not cfg:
            return {"enabled": True} if not is_overlay else {}

        had_mode = isinstance(cfg.get("mode"), str) and bool(str(cfg.get("mode")).strip())
        legacy_pm = cfg.pop("permission_mode", None)

        enabled = cfg.get("enabled", True)
        if enabled is False and not is_overlay:
            cfg["enabled"] = True
            if not had_mode:
                cfg["mode"] = "full_access"

        if not had_mode:
            if isinstance(legacy_pm, str) and legacy_pm.strip().lower() == "strict":
                cfg["mode"] = "strict"
            elif not is_overlay and "mode" not in cfg:
                cfg["mode"] = _DEFAULT_MODE

        _normalize_tools_to_lists(cfg)
        # 产品 YAML 不写顶层 defaults；迁移期丢弃以免与 mode 冲突
        cfg.pop("defaults", None)
        return cfg

    def compose(
        self,
        global_cfg: dict[str, Any] | None,
        user_cfg: dict[str, Any] | None = None,
        session_cfg: dict[str, Any] | None = None,
    ) -> SimpleNamespace:
        """Global ⊕ User ⊕ Session ⊕ mode preset → EffectivePermissions。

        Session 的 deny_tools / ask_tools 忽略。失败时 Fail-Closed：enabled 保持，
        未知 mode 回退 auto。
        """
        try:
            return self._compose_impl(global_cfg, user_cfg, session_cfg)
        except Exception:
            logger.exception(
                "[PermissionEngine] permission.mode.compose_failed fallback=auto_ask",
            )
            # Fail-Closed：defaults ask，避免静默放行
            fallback = {
                "enabled": True,
                "mode": "auto",
                "permission_mode": "normal",
                "defaults": {"*": "ask"},
                "file_guard": {"enabled": True, "defaults": {"read": "ask", "write": "ask", "exec": "ask"}},
                "sandbox_intent": "required",
                "findings_escalate": True,
            }
            return SimpleNamespace(
                mode="auto",
                permissions=fallback,
                severity_map="normal",
                sandbox_intent="required",
            )

    def _compose_impl(
        self,
        global_cfg: dict[str, Any] | None,
        user_cfg: dict[str, Any] | None,
        session_cfg: dict[str, Any] | None,
    ) -> SimpleNamespace:
        g = self.migrate_legacy(global_cfg, is_overlay=False)
        u = self.migrate_legacy(user_cfg, is_overlay=True) if user_cfg else {}
        s = self.migrate_legacy(session_cfg, is_overlay=True) if session_cfg else {}

        # Session 不做整工具收紧
        s.pop("deny_tools", None)
        s.pop("ask_tools", None)
        s.pop("tools", None)

        mode = self._resolve_mode(g, u)
        preset = get_mode_preset(mode)
        severity_map: SeverityMap = preset["severity_map"]
        sandbox_intent: SandboxIntent = preset["sandbox_intent"]

        out: dict[str, Any] = {
            "enabled": True if g.get("enabled", True) is not False else False,
            "mode": mode,
        }
        # Global enabled false already migrated; keep explicit False only if somehow still false
        if g.get("enabled") is False:
            out["enabled"] = False

        for key in ("schema", "owner_scopes", "deny_guidance_message"):
            if key in g:
                out[key] = deepcopy(g[key])
            elif key in u:
                out[key] = deepcopy(u[key])

        # rules：按层打标后合并（评估时 Global 底线优先于 approval_overrides）
        rules = _merge_rule_lists(
            _tag_rules(g.get("rules"), "global"),
            _tag_rules(u.get("rules"), "user"),
            _tag_rules(s.get("rules"), "session"),
        )
        if rules:
            out["rules"] = rules

        # approval_overrides：仅 User + Session（Global 若残留则并入，P0 兼容）
        overrides = _merge_rule_lists(
            g.get("approval_overrides"),
            u.get("approval_overrides"),
            s.get("approval_overrides"),
        )
        # 剔除整工具伪规则（无 pattern / pattern=* / match_type=tool）
        overrides = [
            entry for entry in overrides if self._is_pattern_allow_override(entry)
        ]
        if overrides:
            out["approval_overrides"] = overrides

        # network：mode 注入 defaults；Full Access 忽略用户 host ask/deny
        out["network"] = self._compose_network(mode, preset, g, u, s)

        # tool lists：deny/ask 来自 Global+User；allow 以 User/Session 为主。
        # 保留 g.allow_tools：若 Host 把已烘焙结果再当 Global 合成，必须幂等，
        # 否则会剥掉 User∪Session 的 allow。磁盘 Global YAML 仍由 migrate_and_write 剔除。
        # Session deny/ask 已剔除
        deny = _as_str_list(g.get("deny_tools")) + _as_str_list(u.get("deny_tools"))
        ask = _as_str_list(g.get("ask_tools")) + _as_str_list(u.get("ask_tools"))
        allow = (
            _as_str_list(g.get("allow_tools"))
            + _as_str_list(u.get("allow_tools"))
            + _as_str_list(s.get("allow_tools"))
        )
        deny_u = list(dict.fromkeys(deny))
        deny_set = set(deny_u)
        ask_u = [t for t in dict.fromkeys(ask) if t not in deny_set]
        ask_set = set(ask_u)
        allow_u = [t for t in dict.fromkeys(allow) if t not in deny_set and t not in ask_set]
        if deny_u:
            out["deny_tools"] = deny_u
        if ask_u:
            out["ask_tools"] = ask_u
        if allow_u:
            out["allow_tools"] = allow_u
        if mode == "strict":
            _inject_strict_path_tool_allows(out)

        # file_guard：mode 强制 on/off；未匹配 defaults/workspace 由 preset 主导；
        # 各层只合并增量 paths（及非 defaults 轴字段）。
        if mode == "full_access":
            out["file_guard"] = _merge_file_guard(
                preset.get("file_guard"),
                force_enabled=False,
            )
        else:
            path_only_layers = []
            for layer in (g, u, s):
                fg_layer = layer.get("file_guard") if isinstance(layer.get("file_guard"), dict) else None
                if not isinstance(fg_layer, dict):
                    continue
                # 不让各层 defaults/workspace 冲掉 mode 的未匹配语义
                trimmed = {k: v for k, v in fg_layer.items() if k not in ("defaults", "workspace", "enabled")}
                if trimmed:
                    path_only_layers.append(trimmed)
            out["file_guard"] = _merge_file_guard(
                preset.get("file_guard"),
                *path_only_layers,
                force_enabled=True,
            )
            # Auto/Strict：注入 package builtin 敏感路径；YAML 不可放宽底线
            builtin_paths = _builtin_sensitive_path_entries()
            fg = out["file_guard"]
            fg["paths"] = _merge_builtin_sensitive_paths(fg.get("paths"), builtin_paths)

        # mode 注入内部 defaults + severity（引擎仍读 permission_mode）
        out["defaults"] = deepcopy(preset["defaults"])
        out["permission_mode"] = severity_map
        out["sandbox_intent"] = sandbox_intent
        out["findings_escalate"] = bool(preset.get("findings_escalate", True))

        _project_tool_lists(out)
        return SimpleNamespace(
            mode=mode,
            permissions=out,
            severity_map=severity_map,
            sandbox_intent=sandbox_intent,
        )

    @staticmethod
    def _resolve_mode(global_cfg: dict[str, Any], user_cfg: dict[str, Any]) -> PermissionMode:
        raw = user_cfg.get("mode") if isinstance(user_cfg.get("mode"), str) else None
        if not raw:
            raw = global_cfg.get("mode") if isinstance(global_cfg.get("mode"), str) else None
        mode = (raw or _DEFAULT_MODE).strip().lower()
        if mode not in VALID_PERMISSION_MODES:
            logger.warning(
                "[PermissionEngine] permission.mode.unknown mode=%r fallback=%s",
                mode,
                _DEFAULT_MODE,
            )
            return _DEFAULT_MODE
        return mode  # type: ignore[return-value]

    @staticmethod
    def _is_pattern_allow_override(entry: dict[str, Any]) -> bool:
        action = str(entry.get("action") or "allow").strip().lower()
        if action != "allow":
            return False
        match_type = str(entry.get("match_type") or "").strip().lower()
        if match_type == "tool":
            return False
        pattern = entry.get("pattern")
        if not isinstance(pattern, str) or not pattern.strip():
            return False
        if pattern.strip() == "*":
            return False
        return True

    @staticmethod
    def _compose_network(
        mode: PermissionMode,
        preset: dict[str, Any],
        global_cfg: dict[str, Any],
        user_cfg: dict[str, Any],
        session_cfg: dict[str, Any],
    ) -> dict[str, Any]:
        net_preset = preset.get("network") if isinstance(preset.get("network"), dict) else {}
        network: dict[str, Any] = {
            "enabled": True,
            "defaults": deepcopy(net_preset.get("defaults") or "allow"),
            "ignore_user_host_rules": bool(net_preset.get("ignore_user_host_rules", False)),
            "hosts": [],
        }
        if mode == "full_access":
            network["ignore_user_host_rules"] = True
            network["defaults"] = "allow"
            return network

        hosts: list[dict[str, Any]] = []
        for layer in (global_cfg, user_cfg, session_cfg):
            raw = layer.get("network") if isinstance(layer.get("network"), dict) else {}
            layer_hosts = raw.get("hosts") if isinstance(raw, dict) else None
            if not isinstance(layer_hosts, list):
                continue
            for item in layer_hosts:
                if isinstance(item, dict) and isinstance(item.get("pattern"), str):
                    hosts.append(deepcopy(item))
        if hosts:
            network["hosts"] = hosts
        # 允许 Global 收紧 defaults（ask/deny）；不得把 mode 的 ask 放宽为 allow（strict）
        for layer in (global_cfg, user_cfg):
            raw = layer.get("network") if isinstance(layer.get("network"), dict) else None
            if not isinstance(raw, dict):
                continue
            d = raw.get("defaults")
            if isinstance(d, str) and d.strip().lower() in ("ask", "deny", "allow"):
                cand = d.strip().lower()
                if mode == "strict" and cand == "allow":
                    continue
                network["defaults"] = cand
        return network


__all__ = ["PermissionModeController"]
