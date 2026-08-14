# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""skill_permissions.json 子 schema 校验与摘要计算。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any

from jiuwenclaw.agentserver.permissions.shell_tools import SHELL_PERMISSION_TOOLS

SKILL_PERMISSION_FILENAME = "skill_permissions.json"
SKILL_AUTHORIZATION_ENABLED_ENV = "SKILL_AUTHORIZATION_ENABLED"

logger = logging.getLogger(__name__)

_LEVEL_VALUES = frozenset({"allow", "ask", "deny"})
_RISK_LEVEL_VALUES = frozenset({"low", "medium", "high"})

#: 顶层结构固定为 ``risk + permissions``；不兼容扁平权限声明。
_ALLOWED_TOP_KEYS = frozenset({"risk", "permissions"})

#: ``permissions.enabled`` 是既有格式元数据；其余三项构成 overlay。
_ALLOWED_PERMISSIONS_KEYS = frozenset({"enabled", "tools", "rules", "file_guard"})

#: 单独成集合以便命中时报更具体的错误。
_FORBIDDEN_TOP_KEYS = frozenset({
    "enabled",
    "defaults",
    "approval_overrides",
    "owner_scopes",
    "command_intent",
    "tools",
    "rules",
    "file_guard",
})

#: ``file_guard`` 下仅允许 ``global``。
_FORBIDDEN_FILE_GUARD_KEYS = frozenset({"workspace", "tool_bindings"})

_ALLOWED_RULE_KEYS = frozenset({"id", "pattern", "action", "scope", "description"})
_RULE_ACTIONS = frozenset({"allow", "deny"})
_RULE_SCOPES = frozenset({"exact", "head", "regex", "wildcard"})

_FILE_GUARD_AXES = frozenset({"read", "write", "exec"})


class SkillPermissionValidationError(ValueError):
    """``skill_permissions.json`` 校验失败;``errors`` 为全部失败原因列表。"""

    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


def _validate_tools(tools: Any, errors: list[str]) -> None:
    if not isinstance(tools, dict):
        errors.append(f"tools 必须是对象(tool_name -> allow|ask|deny),实际为 {type(tools).__name__}")
        return
    for tool_name, level in tools.items():
        if not isinstance(tool_name, str) or not tool_name.strip():
            errors.append(f"tools 存在非法工具名: {tool_name!r}")
            continue
        if not isinstance(level, str) or level.strip().lower() not in _LEVEL_VALUES:
            errors.append(f"tools.{tool_name} 值域必须为 allow|ask|deny,实际为 {level!r}")
            continue
        if tool_name in SHELL_PERMISSION_TOOLS and level.strip().lower() == "allow":
            errors.append(
                f"tools.{tool_name} 禁止 shell 类工具整工具 allow;"
                "请改用 rules[*].action=allow 按命令模式申请",
            )


def _validate_rules(rules: Any, errors: list[str]) -> None:
    if not isinstance(rules, list):
        errors.append(f"rules 必须是数组,实际为 {type(rules).__name__}")
        return
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            errors.append(f"rules[{index}] 必须是对象,实际为 {type(rule).__name__}")
            continue
        unknown = sorted(set(rule) - _ALLOWED_RULE_KEYS)
        if unknown:
            errors.append(f"rules[{index}] 存在不允许的键: {', '.join(unknown)}")
        pattern = rule.get("pattern")
        if not isinstance(pattern, str) or not pattern.strip():
            errors.append(f"rules[{index}].pattern 必须是非空字符串")
        action = rule.get("action")
        if not isinstance(action, str) or action.strip().lower() not in _RULE_ACTIONS:
            errors.append(f"rules[{index}].action 必须为 allow|deny,实际为 {action!r}")
        scope = rule.get("scope")
        if scope is not None and (
            not isinstance(scope, str) or scope.strip().lower() not in _RULE_SCOPES
        ):
            errors.append(f"rules[{index}].scope 必须为 exact|head|regex|wildcard,实际为 {scope!r}")
        for key in ("id", "description"):
            value = rule.get(key)
            if value is not None and not isinstance(value, str):
                errors.append(f"rules[{index}].{key} 必须是字符串,实际为 {type(value).__name__}")


def _validate_file_guard(file_guard: Any, errors: list[str]) -> None:
    if not isinstance(file_guard, dict):
        errors.append(f"file_guard 必须是对象,实际为 {type(file_guard).__name__}")
        return
    for key in file_guard:
        if key in _FORBIDDEN_FILE_GUARD_KEYS:
            errors.append(f"file_guard.{key} 禁止在 skill_permissions.json 中声明")
        elif key != "global":
            errors.append(f"file_guard 存在不允许的键: {key!r}(仅允许 global)")
    if "global" not in file_guard:
        return
    global_map = file_guard["global"]
    if not isinstance(global_map, dict):
        errors.append(f"file_guard.global 必须是对象(path -> 轴配置),实际为 {type(global_map).__name__}")
        return
    for path, entry in global_map.items():
        if not isinstance(path, str) or not path.strip():
            errors.append(f"file_guard.global 存在非法路径键: {path!r}")
            continue
        if not isinstance(entry, dict):
            errors.append(f"file_guard.global[{path!r}] 必须是对象,实际为 {type(entry).__name__}")
            continue
        for axis, level in entry.items():
            if axis not in _FILE_GUARD_AXES:
                errors.append(f"file_guard.global[{path!r}] 存在非法轴: {axis!r}(仅允许 read|write|exec)")
                continue
            if not isinstance(level, str) or level.strip().lower() not in _LEVEL_VALUES:
                errors.append(
                    f"file_guard.global[{path!r}].{axis} 值域必须为 allow|ask|deny,实际为 {level!r}",
                )


def _extract_permissions_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Extract the canonical nested overlay and top-level risk metadata."""
    permissions = data.get("permissions")
    if not isinstance(permissions, dict):
        return {}
    payload = {
        key: permissions[key]
        for key in ("tools", "rules", "file_guard")
        if key in permissions
    }
    if "risk" in data:
        payload["risk"] = data["risk"]
    return payload


def validate_skill_permission(data: Any) -> None:
    """校验 ``skill_permissions.json`` 内容;非法时抛 ``SkillPermissionValidationError``。"""
    errors: list[str] = []
    if not isinstance(data, dict):
        raise SkillPermissionValidationError(
            [f"skill_permissions.json 顶层必须是 JSON 对象,实际为 {type(data).__name__}"],
        )

    for key in sorted(set(data) - _ALLOWED_TOP_KEYS):
        if key in _FORBIDDEN_TOP_KEYS:
            errors.append(f"{key} 禁止在 skill_permissions.json 中声明")
        else:
            errors.append(f"存在不允许的顶层键: {key!r}(仅允许 permissions/risk)")

    permissions = data.get("permissions")
    if not isinstance(permissions, dict):
        errors.append("permissions 必须是对象")
    else:
        unknown = sorted(set(permissions) - _ALLOWED_PERMISSIONS_KEYS)
        if unknown:
            errors.append("permissions 存在不允许的键: " + ", ".join(unknown))
        enabled = permissions.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            errors.append("permissions.enabled 必须是布尔值")

    payload = _extract_permissions_payload(data)

    if "tools" in payload:
        _validate_tools(payload["tools"], errors)
    if "rules" in payload:
        _validate_rules(payload["rules"], errors)
    if "file_guard" in payload:
        _validate_file_guard(payload["file_guard"], errors)

    if errors:
        raise SkillPermissionValidationError(errors)


def normalize_skill_permission(data: dict[str, Any]) -> dict[str, Any]:
    """产出规范化的 skill overlay(假定已通过 ``validate_skill_permission``)。

    统一小写值域、剥离无关空白与可选空键,仅保留参与合成与展示的键;
    输出可直接用于确定性 JSON 序列化与 ``Grant.overlay_snapshot``。
    """
    payload = _extract_permissions_payload(data)
    normalized: dict[str, Any] = {}

    tools = payload.get("tools")
    if isinstance(tools, dict) and tools:
        normalized["tools"] = {
            name.strip(): str(level).strip().lower() for name, level in tools.items()
        }

    rules = payload.get("rules")
    if isinstance(rules, list) and rules:
        out_rules: list[dict[str, Any]] = []
        for rule in rules:
            item: dict[str, Any] = {
                "pattern": str(rule["pattern"]).strip(),
                "action": str(rule["action"]).strip().lower(),
            }
            rid = rule.get("id")
            if isinstance(rid, str) and rid.strip():
                item["id"] = rid.strip()
            scope = rule.get("scope")
            if isinstance(scope, str) and scope.strip():
                item["scope"] = scope.strip().lower()
            description = rule.get("description")
            if isinstance(description, str) and description.strip():
                item["description"] = description.strip()
            out_rules.append(item)
        if out_rules:
            normalized["rules"] = out_rules

    file_guard = payload.get("file_guard")
    if isinstance(file_guard, dict):
        global_map = file_guard.get("global")
        if isinstance(global_map, dict) and global_map:
            normalized["file_guard"] = {
                "global": {
                    path.strip(): {axis: str(level).strip().lower() for axis, level in entry.items()}
                    for path, entry in global_map.items()
                },
            }

    risk_level, risk_status = normalize_skill_risk(data)
    if risk_status == "valid" and risk_level is not None:
        normalized["risk"] = {"level": risk_level}

    return normalized


def normalize_skill_risk(data: dict[str, Any]) -> tuple[str | None, str]:
    """返回 ``(level, status)`` 供 Skill 加载门禁决定是否自动批准。

    risk 合法性与权限声明合法性分离：非法 risk 只取消自动批准
    资格，不丢弃其他合法 overlay。
    """
    if "risk" not in data:
        return None, "missing"
    risk = data.get("risk")
    if not isinstance(risk, dict) or set(risk) != {"level"}:
        return None, "invalid"
    raw_level = risk.get("level")
    if not isinstance(raw_level, str):
        return None, "invalid"
    level = raw_level.strip().lower()
    if level not in _RISK_LEVEL_VALUES:
        return None, "invalid"
    return level, "valid"


def canonical_skill_permission_json(data: dict[str, Any]) -> str:
    """规范化后的确定性 JSON 序列化(``sort_keys`` + 紧凑分隔符)。"""
    return json.dumps(
        normalize_skill_permission(data),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def compute_permissions_hash(data: dict[str, Any]) -> str:
    """规范化 ``skill_permissions.json`` 的确定性 SHA-256 摘要(输入先经校验)。"""
    validate_skill_permission(data)
    payload = canonical_skill_permission_json(data)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_skill_md_hash(content: str | bytes) -> str:
    """计算 ``SKILL.md`` 正文的 SHA-256 摘要(用于激活前身份复核)。"""
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()


# ---------- 功能开关与审批响应协议 ----------


def is_skill_authorization_enabled(permissions_config: Any) -> bool:
    """读取动态授权开关；显式环境变量优先，未设置时回退现有配置。"""
    raw_override = os.getenv(SKILL_AUTHORIZATION_ENABLED_ENV)
    if raw_override is not None and raw_override.strip():
        normalized = raw_override.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        logger.warning(
            "[skill_authorization] invalid %s=%r; fail closed",
            SKILL_AUTHORIZATION_ENABLED_ENV,
            raw_override,
        )
        return False
    if not isinstance(permissions_config, dict):
        return False
    section = permissions_config.get("skill_authorization")
    if not isinstance(section, dict):
        return False
    return bool(section.get("enabled"))


#: Skill 审批卡的用户响应 schema(user -> backend;三动作协议,前后端同版本冻结)。
#: 无法识别的 action 由后端默认拒绝(fail-closed)。
SKILL_APPROVAL_PAYLOAD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["approve_once", "approve_session", "continue_without_overlay"],
        },
    },
    "required": ["action"],
}

#: ``InterruptRequest.payload_schema`` 中携带结构化审批卡的扩展键。
SKILL_APPROVAL_CARD_EXTENSION_KEY = "x-skill-approval-card"
