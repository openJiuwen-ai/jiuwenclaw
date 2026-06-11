"""权限配置落盘（宿主侧）。

openjiuwen 的 PermissionInterruptRail 在「总是允许」时会通过 ToolPermissionHost.persist_allow_rule
把合并后的整份 permissions 配置交给宿主写盘。与此同时，JiuWenSwarm 仍有 CLI/WS 的一些入口需要
“记住目录/外部路径”等能力。

这里集中提供这些“落盘 helper”，避免继续依赖 legacy 的 permissions 引擎实现。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

from openjiuwen.harness.security.patterns import (
    merge_external_directory_allow_into_permissions,
    merge_permission_allow_rule_into_permissions,
)

logger = logging.getLogger(__name__)

_DEFAULT_EXTERNAL_DIRECTORY: dict[str, str] = {"*": "ask"}


def _load_config_yaml_round_trip() -> tuple[Any, Any]:
    """Load config.yaml and return (data, yaml_path)."""
    from jiuwenswarm.common.config import _CONFIG_YAML_PATH, _load_yaml_round_trip

    data = _load_yaml_round_trip(_CONFIG_YAML_PATH)
    return data, _CONFIG_YAML_PATH


def _dump_config_yaml_round_trip(yaml_path: Any, data: Any) -> None:
    from jiuwenswarm.common.config import _dump_yaml_round_trip

    _dump_yaml_round_trip(yaml_path, data)


def _ensure_permissions_dict(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    permissions = data.get("permissions")
    if permissions is None:
        permissions = {}
        data["permissions"] = permissions
    if not isinstance(permissions, dict):
        permissions = {}
        data["permissions"] = permissions
    return permissions


def _ensure_external_directory_dict(permissions: dict[str, Any]) -> dict[str, Any]:
    ext_cfg = permissions.get("external_directory")
    if not isinstance(ext_cfg, dict):
        ext_cfg = dict(_DEFAULT_EXTERNAL_DIRECTORY)
        permissions["external_directory"] = ext_cfg
    return ext_cfg


def _ensure_approval_overrides_list(permissions: dict[str, Any]) -> list[dict[str, Any]]:
    overrides = permissions.get("approval_overrides")
    if not isinstance(overrides, list):
        overrides = []
        permissions["approval_overrides"] = overrides
    # keep only dict entries (defensive; YAML may contain junk)
    return [i for i in overrides if isinstance(i, dict)]


def _has_override_id(overrides: list[dict[str, Any]], oid: str) -> bool:
    return any(i.get("id") == oid for i in overrides)


def _append_override_if_missing(
    overrides: list[dict[str, Any]],
    *,
    oid: str,
    tools: list[str],
    match_type: str,
    pattern: str,
    action: str,
    source: str,
) -> None:
    if _has_override_id(overrides, oid):
        return
    overrides.append(
        {
            "id": oid,
            "tools": tools,
            "match_type": match_type,
            "pattern": pattern,
            "action": action,
            "source": source,
        }
    )


def build_command_allow_pattern(cmd: str) -> str:
    """构建匹配完整命令的通配符模式."""
    return cmd.strip() + " *"


def _normalize_tool_args(tool_args: Any) -> dict[str, Any]:
    """将工具入参规范化为 dict。

    - dict: 原样返回（仅保证类型）
    - str/bytes: 尝试解析 JSON；失败返回空 dict
    - 其它类型: 返回空 dict
    """
    if isinstance(tool_args, dict):
        return tool_args
    if isinstance(tool_args, bytes):
        try:
            tool_args = tool_args.decode("utf-8", errors="ignore")
        except Exception:
            return {}
    if isinstance(tool_args, str):
        s = tool_args.strip()
        if not s:
            return {}
        try:
            parsed = json.loads(s)
        except (ValueError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def persist_permission_allow_rule(tool_name: str, tool_args: dict | str) -> bool:
    """用户选择「总是允许」时，将 allow 规则写入 config.yaml 的 permissions 段。"""
    tool_args = _normalize_tool_args(tool_args)

    data, yaml_path = _load_config_yaml_round_trip()
    permissions = data.get("permissions")
    if not isinstance(permissions, dict):
        logger.warning(
            "[PermissionPersist] persist_permission_allow_rule.abort reason=no_permissions_section tool=%s",
            tool_name,
        )
        return False

    merged, ok = merge_permission_allow_rule_into_permissions(permissions, tool_name, tool_args)
    if not ok:
        return False
    data["permissions"] = merged
    _dump_config_yaml_round_trip(yaml_path, data)
    return True


def persist_external_directory_allow(paths: list[str]) -> None:
    """用户选择「总是允许」外部路径时，写入 external_directory 配置。"""
    if not paths:
        return

    data, yaml_path = _load_config_yaml_round_trip()
    permissions = data.get("permissions")
    if not isinstance(permissions, dict):
        permissions = {}
    merged, ok = merge_external_directory_allow_into_permissions(permissions, paths)
    if not ok:
        return
    data["permissions"] = merged
    _dump_config_yaml_round_trip(yaml_path, data)


def persist_cli_trusted_directory(raw_path: str) -> dict[str, Any]:
    """CLI `command.add_dir`：全局信任目录子树。

    写入：
    - `permissions.external_directory`：目录 allow
    """
    if not isinstance(raw_path, str) or not raw_path.strip():
        return {"ok": False, "error": "path is empty"}

    try:
        resolved = Path(raw_path.strip()).expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as e:
        return {"ok": False, "error": f"invalid path: {e}"}

    dir_norm = resolved.as_posix().rstrip("/")
    if not dir_norm:
        return {"ok": False, "error": "path resolves to empty"}

    from ruamel.yaml.scalarstring import DoubleQuotedScalarString

    data, yaml_path = _load_config_yaml_round_trip()
    permissions = _ensure_permissions_dict(data)
    ext_cfg = _ensure_external_directory_dict(permissions)
    ext_cfg[DoubleQuotedScalarString(dir_norm)] = DoubleQuotedScalarString("allow")

    _dump_config_yaml_round_trip(yaml_path, data)
    return {
        "ok": True,
        "normalized": dir_norm,
    }


def persist_cli_trusted_directory_with_overrides(raw_path: str) -> dict[str, Any]:
    """CLI `command.add_dir`：全局信任目录子树（包含覆盖规则）。

    写入：
    - `permissions.external_directory`：目录 allow
    - `permissions.approval_overrides`：追加 path/command 两条 allow override
    """
    if not isinstance(raw_path, str) or not raw_path.strip():
        return {"ok": False, "error": "path is empty"}

    try:
        resolved = Path(raw_path.strip()).expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as e:
        return {"ok": False, "error": f"invalid path: {e}"}

    dir_norm = resolved.as_posix().rstrip("/")
    if not dir_norm:
        return {"ok": False, "error": "path resolves to empty"}

    from ruamel.yaml.scalarstring import DoubleQuotedScalarString

    data, yaml_path = _load_config_yaml_round_trip()
    permissions = _ensure_permissions_dict(data)
    ext_cfg = _ensure_external_directory_dict(permissions)
    ext_cfg[DoubleQuotedScalarString(dir_norm)] = DoubleQuotedScalarString("allow")

    # regex patterns
    path_pattern = "re:^" + re.escape(dir_norm) + r"(?:$|/)"
    shell_pattern = "re:" + rf".*{re.escape(dir_norm)}.*"

    # 是否写 approval_overrides（沿用你们 config.yaml 的约定）
    schema_key = str(permissions.get("schema") or permissions.get("version") or "").strip().lower()
    tiered = schema_key in {"tiered_policy", "v_cc", "v4.2", ""}

    suffix = hashlib.sha256(dir_norm.encode("utf-8")).hexdigest()[:16]
    path_override_id = f"cli_trusted_path_{suffix}"
    shell_override_id = f"cli_trusted_shell_{suffix}"

    if tiered:
        overrides = _ensure_approval_overrides_list(permissions)

        path_tools = sorted({
            "read_file", "write_file", "edit_file",
            "read_text_file", "write_text_file",
            "write", "read",
            "glob_file_search", "glob", "list_dir", "list_files",
            "grep", "search_replace",
        })
        _append_override_if_missing(
            overrides,
            oid=path_override_id,
            tools=path_tools,
            match_type="path",
            pattern=path_pattern,
            action="allow",
            source="cli_add_dir",
        )

        shell_tools = sorted({"bash", "mcp_exec_command", "create_terminal"})
        _append_override_if_missing(
            overrides,
            oid=shell_override_id,
            tools=shell_tools,
            match_type="command",
            pattern=shell_pattern,
            action="allow",
            source="cli_add_dir",
        )

    _dump_config_yaml_round_trip(yaml_path, data)
    return {
        "ok": True,
        "normalized": dir_norm,
        "path_pattern": path_pattern,
        "shell_pattern": shell_pattern,
        "tiered_overrides": tiered,
    }


__all__ = [
    "build_command_allow_pattern",
    "persist_cli_trusted_directory",
    "persist_cli_trusted_directory_with_overrides",
    "persist_external_directory_allow",
    "persist_permission_allow_rule",
]

