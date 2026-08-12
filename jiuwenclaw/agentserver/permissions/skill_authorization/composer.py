# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Skill overlay 安全合成与请求级授权 Context。"""

from __future__ import annotations

import contextvars
import copy
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: Skill 声明可提升的原档位（``guard`` 为无 baseline 语义，等价于未放宽）。
_RAISABLE_TOOL_LEVELS = frozenset({"ask", "guard"})

_TOOL_LEVELS = frozenset({"allow", "ask", "deny"})
_FILE_GUARD_AXES = frozenset({"read", "write", "exec"})
_RULE_ACTIONS = frozenset({"allow", "deny"})


# ---------- tools ----------


def _normalize_level(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    level = value.strip().lower()
    return level or None


def _base_tool_level(base: dict[str, Any], tool_name: str) -> str | None:
    """原档位：显式 ``tools.<name>`` 优先，未显式配置时继承 ``defaults``。"""
    tools = base.get("tools")
    if isinstance(tools, dict) and tool_name in tools:
        return _normalize_level(tools[tool_name])
    return _normalize_level(base.get("defaults"))


def _compose_tools(merged: dict[str, Any], base: dict[str, Any], overlay_tools: dict[Any, Any]) -> None:
    tools = merged.setdefault("tools", {})
    for tool_name, raw_level in overlay_tools.items():
        if not isinstance(tool_name, str) or not tool_name.strip():
            logger.warning("[skill_authorization] compose.tools.skip invalid tool name: %r", tool_name)
            continue
        overlay_level = _normalize_level(raw_level)
        if overlay_level not in _TOOL_LEVELS:
            logger.warning(
                "[skill_authorization] compose.tools.skip tool=%s invalid level=%r",
                tool_name, raw_level,
            )
            continue
        base_level = _base_tool_level(base, tool_name)
        if base_level == "deny":
            # 显式或默认 DENY 不可被任何声明改变。
            logger.info(
                "[skill_authorization] compose.tools.keep_deny tool=%s overlay=%s",
                tool_name, overlay_level,
            )
            continue
        if overlay_level == "allow":
            if base_level in _RAISABLE_TOOL_LEVELS:
                tools[tool_name] = "allow"
            elif base_level == "allow":
                continue
            else:
                # 原档位缺失 / 非法：不按 skill 声明放宽（fail-closed）。
                logger.warning(
                    "[skill_authorization] compose.tools.skip_raise tool=%s base_level=%r",
                    tool_name, base_level,
                )
        else:
            # ask / deny 直接收紧生效。
            tools[tool_name] = overlay_level


# ---------- rules ----------


def _compose_rules(merged: dict[str, Any], overlay_rules: list[Any]) -> None:
    base_rules = merged.get("rules")
    if not isinstance(base_rules, list):
        base_rules = []
        merged["rules"] = base_rules
    for rule in overlay_rules:
        if not isinstance(rule, dict):
            logger.warning("[skill_authorization] compose.rules.skip non-dict rule: %r", rule)
            continue
        action = _normalize_level(rule.get("action"))
        pattern = rule.get("pattern")
        if action not in _RULE_ACTIONS or not isinstance(pattern, str) or not pattern.strip():
            logger.warning("[skill_authorization] compose.rules.skip malformed rule: %r", rule)
            continue
        # allow / deny 均只追加；deny 不改变既有 deny、只能新增。
        base_rules.append(copy.deepcopy(rule))


# ---------- file_guard.global ----------


def _normalize_guard_path(path: str) -> str:
    """合成期路径规范化：统一斜杠、去尾部斜杠（不触碰文件系统）。"""
    normalized = path.replace("\\", "/").strip()
    while len(normalized) > 1 and normalized.endswith("/"):
        normalized = normalized[:-1]
    return normalized


def _is_same_or_ancestor(ancestor: str, path: str) -> bool:
    """``ancestor`` 是否为 ``path`` 的同级或祖先路径（均为规范化后的字符串）。"""
    if ancestor == path:
        return True
    if ancestor == "/":
        return path.startswith("/")
    return path.startswith(ancestor + "/")


def _collect_overlay_denies(overlay_global: dict[Any, Any]) -> set[tuple[str, str]]:
    """本 overlay 自声明的 ``(规范化路径, 轴)`` deny 集合（与遍历顺序无关）。

    同一 overlay 先 deny 父路径再 allow/ask 子路径时，子路径声明同样不得突破；
    该集合让祖先检查把 overlay 自加 deny 与 base deny 一视同仁。
    """
    denies: set[tuple[str, str]] = set()
    for raw_path, entry in overlay_global.items():
        if not isinstance(raw_path, str) or not raw_path.strip() or not isinstance(entry, dict):
            continue
        normalized = _normalize_guard_path(raw_path)
        for axis, raw_level in entry.items():
            if axis in _FILE_GUARD_AXES and _normalize_level(raw_level) == "deny":
                denies.add((normalized, axis))
    return denies


def _axis_denied_by_ancestor(
    base_global: dict[str, Any],
    overlay_denies: set[tuple[str, str]],
    path: str,
    axis: str,
) -> bool:
    """base 或本 overlay 中同级/祖先路径是否已对该轴声明 ``deny``（不可被覆盖）。"""
    for base_path, entry in base_global.items():
        if not isinstance(base_path, str) or not isinstance(entry, dict):
            continue
        if _normalize_level(entry.get(axis)) != "deny":
            continue
        if _is_same_or_ancestor(_normalize_guard_path(base_path), path):
            return True
    return any(
        deny_axis == axis and _is_same_or_ancestor(deny_path, path)
        for deny_path, deny_axis in overlay_denies
    )


def _compose_file_guard_global(
    merged: dict[str, Any],
    base: dict[str, Any],
    overlay_global: dict[Any, Any],
) -> None:
    fg = merged.setdefault("file_guard", {})
    if not isinstance(fg, dict):
        fg = {}
        merged["file_guard"] = fg
    global_map = fg.setdefault("global", {})
    if not isinstance(global_map, dict):
        global_map = {}
        fg["global"] = global_map

    base_fg = base.get("file_guard")
    base_global = base_fg.get("global") if isinstance(base_fg, dict) else None
    if not isinstance(base_global, dict):
        base_global = {}
    overlay_denies = _collect_overlay_denies(overlay_global)

    for raw_path, entry in overlay_global.items():
        if not isinstance(raw_path, str) or not raw_path.strip() or not isinstance(entry, dict):
            logger.warning(
                "[skill_authorization] compose.file_guard.skip path=%r entry=%r",
                raw_path, entry,
            )
            continue
        path = _normalize_guard_path(raw_path)
        base_entry = global_map.get(path)
        if not isinstance(base_entry, dict):
            base_entry = {}
        new_entry = dict(base_entry)
        for axis, raw_level in entry.items():
            if axis not in _FILE_GUARD_AXES:
                logger.warning(
                    "[skill_authorization] compose.file_guard.skip_axis path=%s axis=%r",
                    path, axis,
                )
                continue
            overlay_level = _normalize_level(raw_level)
            if overlay_level not in _TOOL_LEVELS:
                logger.warning(
                    "[skill_authorization] compose.file_guard.skip_axis path=%s axis=%s level=%r",
                    path, axis, raw_level,
                )
                continue
            base_level = _normalize_level(base_entry.get(axis))
            if overlay_level == "deny":
                # deny 直接收紧，只增不改（base 已是 deny 时为无害 no-op）。
                new_entry[axis] = "deny"
                continue
            # allow / ask：任何同级或祖先 deny 均不可突破（含本路径显式 deny、
            # 以及同一 overlay 自声明的祖先 deny）。
            if _axis_denied_by_ancestor(base_global, overlay_denies, path, axis):
                logger.info(
                    "[skill_authorization] compose.file_guard.keep_deny path=%s axis=%s overlay=%s",
                    path, axis, overlay_level,
                )
                continue
            if overlay_level == "ask":
                if base_level == "allow":
                    new_entry[axis] = "ask"
                continue
            # overlay allow：仅提升 ask（缺省轴按 file_guard 语义视为 ask）。
            if base_level in (None, "ask"):
                new_entry[axis] = "allow"
        # 继承基线中同级或祖先路径的 deny 轴：裁决端按单条最长前缀条目取档、
        # 缺轴兜底为 ask（workspace 内甚至被短路为 allow），新条目若不携带
        # 祖先 deny 轴会静默降级祖先 deny。
        for axis in _FILE_GUARD_AXES:
            if axis in new_entry:
                continue
            if _axis_denied_by_ancestor(base_global, overlay_denies, path, axis):
                new_entry[axis] = "deny"
        if new_entry:
            global_map[path] = new_entry


def effective_file_guard_axis_level(
    global_map: dict[str, Any],
    raw_path: str,
    axis: str,
    *,
    workspace_root: Any,
    rw_enabled: bool,
) -> str | None:
    """按裁决语义计算 ``raw_path`` 在 ``axis`` 上的生效档位（allow/ask/deny）。

    与 ``FileGuardChecker._check_one`` 逐步对齐：最长前缀单条命中、deny 穿透
    workspace、workspace 内 rw_enabled 时 read/write 短路为 allow、缺省 ask。
    供审批差分展示使用，确保卡上的 before/after 与真实裁决一致；路径无法解析
    或运行环境缺依赖时返回 ``None``，调用方应回退到简化口径。
    """
    try:
        from jiuwenclaw.agentserver.permissions.file_guard import (
            _action_mode,
            _longest_prefix_match,
            _posix_str,
            _resolve_path_str,
            contains_path,
        )
    except ImportError:
        return None
    resolved = _resolve_path_str(raw_path, workspace_root)
    if resolved is None:
        return None
    entry = _longest_prefix_match(_posix_str(resolved), global_map, workspace_root)
    mode = _action_mode(entry, axis) if isinstance(entry, dict) else None
    if mode == "deny":
        return "deny"
    if axis in ("read", "write") and rw_enabled and contains_path(workspace_root, resolved):
        return "allow"
    return mode or "ask"


# ---------- 合成入口 ----------


def compose_skill_permissions(
    base_effective: dict[str, Any],
    skill_overlay: dict[str, Any] | None,
) -> dict[str, Any]:
    """把 Skill overlay 安全合成到当前生效权限配置上。

    输入均视为只读；返回新的合成配置。任何异常（含非法输入）返回
    ``base_effective`` 深拷贝（fail-closed，仅按原权限流程裁决）。
    """
    base = base_effective if isinstance(base_effective, dict) else {}
    if not skill_overlay or not isinstance(skill_overlay, dict):
        return copy.deepcopy(base)
    try:
        merged = copy.deepcopy(base)

        overlay_tools = skill_overlay.get("tools")
        if isinstance(overlay_tools, dict) and overlay_tools:
            _compose_tools(merged, base, overlay_tools)

        overlay_rules = skill_overlay.get("rules")
        if isinstance(overlay_rules, list) and overlay_rules:
            _compose_rules(merged, overlay_rules)

        overlay_fg = skill_overlay.get("file_guard")
        if isinstance(overlay_fg, dict):
            overlay_global = overlay_fg.get("global")
            if isinstance(overlay_global, dict) and overlay_global:
                _compose_file_guard_global(merged, base, overlay_global)

        return merged
    except Exception:  # noqa: BLE001 — 合成异常时仅使用原有权限（fail-closed）
        logger.warning(
            "[skill_authorization] compose.failed fallback=base_effective",
            exc_info=True,
        )
        return copy.deepcopy(base)


# ---------- 请求级授权 Context ----------


@dataclass(frozen=True)
class SkillAuthorizationContext:
    """请求级授权上下文：``PermissionEngine`` 据此查询当前作用域的 ``ACTIVE`` Grant。"""

    session_id: str
    agent_scope_id: str
    request_id: str = ""


_SKILL_AUTHORIZATION_CONTEXT: contextvars.ContextVar[SkillAuthorizationContext | None] = (
    contextvars.ContextVar("jiuwenclaw_skill_authorization_context", default=None)
)


def setup_skill_authorization_context(
    session_id: str | None,
    agent_scope_id: str | None,
    request_id: str | None = None,
) -> contextvars.Token:
    """在请求入口绑定授权 Context（finally 中用返回的 token reset）。"""
    ctx = SkillAuthorizationContext(
        session_id=(session_id or "").strip(),
        agent_scope_id=(agent_scope_id or "").strip(),
        request_id=(request_id or "").strip(),
    )
    return _SKILL_AUTHORIZATION_CONTEXT.set(ctx)


def reset_skill_authorization_context(token: contextvars.Token) -> None:
    _SKILL_AUTHORIZATION_CONTEXT.reset(token)


def get_skill_authorization_context() -> SkillAuthorizationContext | None:
    """读取当前授权 Context；缺失时调用方不得应用 Skill overlay。"""
    return _SKILL_AUTHORIZATION_CONTEXT.get()
