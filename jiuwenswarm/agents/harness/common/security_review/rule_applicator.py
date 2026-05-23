# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Apply approved security review rule candidates to user permission rules."""
from __future__ import annotations

import re
from typing import Any

from openjiuwen.harness.security.tiered_policy import rule_tools_category_consistent
from jiuwenswarm.common.config import create_permissions_rule_in_config


_VALID_SEVERITIES = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
_SHELL_TOOLS = {"bash", "mcp_exec_command", "create_terminal"}
_PATH_TOOLS = {
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
}
_NETWORK_TOOLS = {"mcp_fetch_webpage", "mcp_free_search", "mcp_paid_search"}


class SecurityRuleApplicationError(ValueError):
    """Raised when an approved security rule candidate cannot be applied."""


def security_rule_candidate_to_permission_rule(candidate: dict[str, Any]) -> dict[str, Any]:
    if candidate.get("type") != "security_rule":
        raise SecurityRuleApplicationError("candidate type must be security_rule")
    if candidate.get("requires_approval") is not True:
        raise SecurityRuleApplicationError("candidate requires approval")

    tools = candidate.get("tools")
    if isinstance(tools, str):
        tools = [tools]
    if not isinstance(tools, list):
        tools = []
    tools = [str(tool).strip() for tool in tools if str(tool).strip()]
    if not tools:
        raise SecurityRuleApplicationError("tools must be non-empty")
    if not rule_tools_category_consistent(tools):
        raise SecurityRuleApplicationError("tools must belong to the same tool category")

    pattern = str(candidate.get("pattern") or "").strip()
    if not pattern:
        raise SecurityRuleApplicationError("pattern must be non-empty")

    severity = str(candidate.get("severity") or "HIGH").strip().upper()
    if severity not in _VALID_SEVERITIES:
        raise SecurityRuleApplicationError(f"invalid severity: {severity}")

    rule_id = _rule_id(candidate)
    return {
        "id": rule_id,
        "description": str(candidate.get("rationale") or candidate.get("description") or rule_id),
        "tools": tools,
        "match_type": _match_type_for_tools(tools),
        "pattern": pattern,
        "severity": severity,
    }


def apply_security_rule_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    rule = security_rule_candidate_to_permission_rule(candidate)
    stored = create_permissions_rule_in_config(rule)
    return {
        "applied": True,
        "target": "permissions.rules",
        "rule_id": stored["id"],
        "rule": rule,
    }


def _rule_id(candidate: dict[str, Any]) -> str:
    raw = str(candidate.get("rule_id") or candidate.get("id") or "security-rule").strip()
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", raw).strip("-") or "security-rule"
    if slug.startswith("security_review_"):
        return slug
    return f"security_review_{slug}"


def _match_type_for_tools(tools: list[str]) -> str:
    tool_set = set(tools)
    if tool_set <= _SHELL_TOOLS:
        return "command"
    if tool_set <= _PATH_TOOLS:
        return "path"
    if tool_set <= _NETWORK_TOOLS:
        return "url"
    raise SecurityRuleApplicationError("unsupported tool category")
