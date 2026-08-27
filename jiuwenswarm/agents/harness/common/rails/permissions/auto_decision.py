# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Auto-permission guard helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from jsonschema import ValidationError as JsonSchemaValidationError
from jsonschema import validate as validate_json_schema
from openjiuwen.harness.prompts.tools.filesystem import (
    get_grep_input_params,
    get_list_dir_input_params,
    get_read_file_input_params,
)
from openjiuwen.harness.tools.lsp_tool import LspToolInput
from pydantic import TypeAdapter, ValidationError

from jiuwenswarm.agents.harness.common.rails.permissions.root_context import (
    OriginalUserIntentEvidence,
)
from jiuwenswarm.agents.harness.common.rails.permissions.tool_decision_facts import (
    DecisionRoute,
    ToolDecisionFacts,
)
from jiuwenswarm.agents.harness.common.rails.permissions.url_safety import (
    RecentUrlSource,
    absolute_uri_hard_block_reason,
    evaluate_reviewable_url_scope,
    inspect_network_scope,
)

ALLOW_LEVEL = "allow"
ASK_LEVEL = "ask"
DENY_LEVEL = "deny"
SEARCH_SKILL_TOOL = "search_skill"
SEARCH_SKILL_SENSITIVE_QUERY_PATTERN = re.compile(
    r"(?i)(?:api[_-]?key|private[_-]?key|access[_-]?token|secret[_-]?key|password|token)\b(?:\s*[:=]\s*|\s+)\S+",
)
EGRESS_SECRET_KEY_PATTERN = re.compile(
    r"(?i)(?:^|[._-])(?:access[_-]?token|api[_-]?key|authorization|"
    r"credentials?|password|private[_-]?key|secret|token)(?:$|[._-])"
)
EGRESS_SECRET_VALUE_PATTERN = re.compile(
    r"(?i)(?:AKIA[A-Z0-9]{12,}|sk-[A-Za-z0-9_-]{8,}|"
    r"gh[pousr]_[A-Za-z0-9_]{8,}|(?:access[_-]?token|api[_-]?key|"
    r"authorization|bearer|credentials?|password|private[_-]?key|secret|token)"
    r"\s*[:=]\s*\S+)"
)
GENERIC_MCP_UPLOAD_KEYS = frozenset(
    {
        "abs_file_path",
        "abs_file_path_list",
        "abs_file_paths",
        "attachment",
        "attachments",
        "file",
        "file_path",
        "file_path_list",
        "file_paths",
        "files",
    }
)
HIGH_EFFECT_SIDE_EFFECTS = frozenset(
    {
        "browser_action",
        "delegation",
        "device_action",
        "dynamic_exec",
        "external_send",
        "memory_write",
        "schedule",
        "skill_change",
    }
)
BROWSER_INSPECTION_OPERATIONS = (
    "snapshot",
    "screenshot",
    "inspect",
    "probe",
    "recall",
    "health",
    "wait_for",
)
_LSP_INPUT_ADAPTER = TypeAdapter(LspToolInput)
_STRUCTURED_WORKSPACE_READ_SCHEMAS = {
    "grep": get_grep_input_params,
    "list_files": get_list_dir_input_params,
    "read_file": get_read_file_input_params,
}
_STRUCTURED_WORKSPACE_READ_TOOLS = frozenset(
    {*_STRUCTURED_WORKSPACE_READ_SCHEMAS, "lsp"}
)


def deterministic_guard_route(
    facts: ToolDecisionFacts,
) -> DecisionRoute | None:
    """Return a hard deny decision for non-overridable findings."""
    if facts.tool_name == SEARCH_SKILL_TOOL:
        return _evaluate_search_skill_egress_guard(facts)
    return None


def generic_mcp_egress_evidence(
    facts: ToolDecisionFacts,
) -> tuple[str, ...]:
    """Return redacted risk labels for generic MCP Reviewer evidence."""

    network = inspect_network_scope(facts)
    evidence: list[str] = []
    if (
        network.upload_like
        or "external_send" in facts.capability.static_side_effects
        or _has_generic_mcp_upload_field(facts.untrusted_args)
    ):
        evidence.append("generic_mcp_upload_like_payload")
    for uri in network.absolute_uris:
        unsafe_reason = absolute_uri_hard_block_reason(uri)
        if unsafe_reason:
            evidence.append(unsafe_reason)
        elif uri.parse_failure_reason:
            evidence.append(uri.parse_failure_reason)
        elif uri.file_resolution_reason:
            evidence.append(uri.file_resolution_reason)
        elif uri.scheme == "http":
            evidence.append("network_scheme_not_https")
        elif uri.scheme == "file":
            evidence.append("generic_mcp_file_uri")
        elif uri.scheme == "ftp":
            evidence.append("generic_mcp_uri_provider_support_unproven")
        elif uri.scheme != "https":
            evidence.append("generic_mcp_uri_scheme_unsupported")
    if _has_secret_like_egress_payload(facts.untrusted_args):
        evidence.append("generic_mcp_sensitive_egress")
    return tuple(dict.fromkeys(evidence))


def deterministic_domain_route(
    facts: ToolDecisionFacts,
    *,
    original_user_intent: OriginalUserIntentEvidence | None,
    recent_url_sources: tuple[RecentUrlSource, ...] = (),
    browser_runtime_security_profile: Any | None = None,
) -> DecisionRoute | None:
    """Return the fixed in-repository domain route without a plugin registry."""

    category = facts.tool_category
    operation = _domain_operation(facts)
    network = inspect_network_scope(facts)
    if category == "mcp":
        return DecisionRoute(ASK_LEVEL, "domain_policy_generic_mcp_manual", "manual_only")
    if category == "browser":
        for uri in network.absolute_uris:
            reason = absolute_uri_hard_block_reason(uri)
            if reason:
                return DecisionRoute(DENY_LEVEL, reason, "hard_guard")
        if "http" in network.schemes:
            return DecisionRoute(DENY_LEVEL, "network_scheme_not_https", "hard_guard")
        if any(uri.parse_failure_reason for uri in network.absolute_uris):
            return DecisionRoute(ASK_LEVEL, "browser_uri_parse_failure", "manual_only")
        if any(uri.file_resolution_reason for uri in network.absolute_uris):
            reason = (
                "browser_file_uri_nonlocal"
                if any(
                    uri.file_resolution_reason == "file_uri_nonlocal_host"
                    for uri in network.absolute_uris
                )
                else "browser_file_uri_unresolved"
            )
            return DecisionRoute(ASK_LEVEL, reason, "manual_only")
        if "ftp" in network.schemes:
            return DecisionRoute(ASK_LEVEL, "browser_uri_provider_support_unproven", "manual_only")
        if any(
            scheme not in {"file", "ftp", "http", "https"}
            for scheme in network.schemes
        ):
            return DecisionRoute(ASK_LEVEL, "browser_uri_scheme_unsupported", "manual_only")
        if len(network.absolute_uris) > 1:
            return DecisionRoute(ASK_LEVEL, "browser_uri_target_ambiguous", "manual_only")
        browser_reasons = {
            "credential_form": "domain_policy_browser_credential_form",
            "upload": "domain_policy_browser_file_transfer",
            "external_submit": "domain_policy_browser_external_submit",
            "control_cancel": "domain_policy_browser_cancel_control_not_model_authorized",
            "route_mutation": "domain_policy_browser_route_mutation_not_model_authorized",
            "inspect": "domain_policy_browser_readonly",
            "click": "domain_policy_browser_interactive",
            "type": "domain_policy_browser_interactive",
            "download": "domain_policy_browser_interactive",
        }
        if operation == "navigate":
            if network.absolute_uris and network.absolute_uris[0].scheme == "file":
                return DecisionRoute(ASK_LEVEL, "domain_policy_browser_local_file_uri", "semantic_reviewer")
            url_route = evaluate_reviewable_url_scope(
                facts,
                evidence=original_user_intent,
                recent_url_sources=recent_url_sources,
            )
            if url_route.hard_block:
                return DecisionRoute(DENY_LEVEL, url_route.reason, "hard_guard")
            if not url_route.accepted:
                return DecisionRoute(ASK_LEVEL, url_route.reason, "manual_only")
            if url_route.semantic_review_required and not bool(
                browser_runtime_security_profile
                and browser_runtime_security_profile.network_guard_enforced
            ):
                return DecisionRoute(ASK_LEVEL, "browser_network_guard_unverified", "manual_only")
            return DecisionRoute(
                ASK_LEVEL,
                "domain_policy_browser_navigation_url_reviewable",
                "semantic_reviewer",
            )
        if operation in browser_reasons:
            return DecisionRoute(ASK_LEVEL, browser_reasons[operation], "semantic_reviewer")
        return DecisionRoute(ASK_LEVEL, "domain_policy_browser_unknown_payload", "manual_only")
    if category == "cron":
        reasons = {
            "preview": "domain_policy_cron_preview",
            "create": "domain_policy_cron_mutation_manual",
            "update": "domain_policy_cron_mutation_manual",
            "run_now": "domain_policy_cron_mutation_manual",
            "delete": "domain_policy_cron_destructive",
            "toggle": "domain_policy_cron_destructive",
        }
        reason = reasons.get(operation)
        return DecisionRoute(
            ASK_LEVEL,
            reason or "domain_policy_cron_unknown_payload",
            "semantic_reviewer" if reason else "manual_only",
        )
    if category == "memory" and facts.capability.operation_family == "memory_write":
        namespace = _text_arg(facts.untrusted_args, "namespace", "space", "profile")
        scope = _text_arg(facts.untrusted_args, "scope", "visibility", "target_scope")
        retention = _text_arg(facts.untrusted_args, "retention", "ttl", "expires_in", "expires")
        missing = (
            "domain_policy_memory_namespace_missing"
            if not namespace
            else "domain_policy_memory_scope_missing"
            if not scope
            else "domain_policy_memory_retention_missing"
            if not retention
            else ""
        )
        return DecisionRoute(
            ASK_LEVEL,
            missing or "domain_policy_memory_bounded_write",
            "manual_only" if missing else "semantic_reviewer",
        )
    if category == "skill":
        if operation in {"read", "search", "list"}:
            return DecisionRoute(ASK_LEVEL, "domain_policy_skill_readonly", "semantic_reviewer")
        if operation in {"install", "update", "uninstall"}:
            return DecisionRoute(ASK_LEVEL, "domain_policy_skill_change", "semantic_reviewer")
        return DecisionRoute(ASK_LEVEL, "domain_policy_skill_unknown_payload", "manual_only")
    return None


def _has_generic_mcp_upload_field(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            (
                str(key).strip().lower() in GENERIC_MCP_UPLOAD_KEYS
                and _has_truthy_egress_value(nested_value)
            )
            or _has_generic_mcp_upload_field(nested_value)
            for key, nested_value in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return any(_has_generic_mcp_upload_field(item) for item in value)
    return False


def _has_secret_like_egress_payload(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            (
                EGRESS_SECRET_KEY_PATTERN.search(str(key)) is not None
                and _has_truthy_egress_value(nested_value)
            )
            or _has_secret_like_egress_payload(nested_value)
            for key, nested_value in value.items()
        )
    if isinstance(value, str):
        return EGRESS_SECRET_VALUE_PATTERN.search(value) is not None
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return any(_has_secret_like_egress_payload(item) for item in value)
    return False


def _has_truthy_egress_value(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping | Sequence):
        return bool(value)
    return True


def _evaluate_search_skill_egress_guard(
    facts: ToolDecisionFacts,
) -> DecisionRoute | None:
    query = facts.untrusted_args.get("query")
    if isinstance(query, str) and SEARCH_SKILL_SENSITIVE_QUERY_PATTERN.search(query):
        return DecisionRoute(
            level=DENY_LEVEL,
            reason="search_skill_sensitive_query",
            source="deterministic_guard",
        )
    return None


def _domain_operation(facts: ToolDecisionFacts) -> str:
    """Classify only the fixed tool/operation names used by direct routes."""

    raw = _text_arg(facts.untrusted_args, "operation", "action", "mode", "op")
    operation = (raw or facts.tool_name).strip().lower().replace("-", "_")
    category = facts.tool_category
    if category == "browser":
        if "cancel" in operation:
            return "control_cancel"
        if "route" in operation or "unroute" in operation:
            return "route_mutation"
        if "upload" in operation:
            return "upload"
        if "download" in operation:
            return "download"
        if any(token in operation for token in BROWSER_INSPECTION_OPERATIONS):
            return "inspect"
        if any(token in operation for token in ("navigate", "open", "goto")):
            return "navigate"
        if any(token in operation for token in ("click", "press", "hover", "select")):
            return "click"
        if any(token in operation for token in ("type", "fill", "input")):
            return "type"
        if operation in {"post", "submit", "external_submit"}:
            return "external_submit"
        return "unknown"
    if category == "cron":
        for token, result in (
            ("preview", "preview"),
            ("delete", "delete"),
            ("remove", "delete"),
            ("toggle", "toggle"),
            ("enable", "toggle"),
            ("disable", "toggle"),
            ("update", "update"),
            ("edit", "update"),
            ("create", "create"),
            ("add", "create"),
            ("run_now", "run_now"),
        ):
            if token in operation:
                return result
        return "unknown"
    if category == "memory":
        if facts.capability.operation_family in {"memory_read", "memory_search"}:
            return "read" if facts.capability.operation_family == "memory_read" else "search"
        if "edit" in operation or "update" in operation:
            return "edit"
        if any(token in operation for token in ("write", "remember", "add", "conclude")):
            return "write"
        return "unknown"
    if category == "skill":
        if facts.capability.operation_family == "skill_read":
            return "read"
        if facts.capability.operation_family == "skill_discovery":
            return "search"
        for token in ("uninstall", "install", "update", "search", "read", "list"):
            if token in operation:
                return token
        return "unknown"
    return facts.capability.operation_family


def _text_arg(args: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _closed_internal_args_valid(facts: ToolDecisionFacts, network: Any) -> bool:
    family = facts.capability.operation_family
    args = facts.untrusted_args
    if family == "session_status":
        return not args
    if family == "skill_read":
        name = args.get("skill_name")
        path = args.get("relative_file_path")
        return (
            isinstance(name, str)
            and bool(name.strip())
            and "/" not in name
            and "\\" not in name
            and path in {None, "", "SKILL.md"}
        )
    if family == "memory_read":
        path = args.get("path")
        return isinstance(path, str) and bool(
            path in {"memory/USER.md", "memory/MEMORY.md"}
            or re.fullmatch(r"memory/\d{4}-\d{2}-\d{2}\.md", path)
        )
    if family in {"browser_session_observe", "browser_session_recall"}:
        if _domain_operation(facts) != "inspect" or network.absolute_uris:
            return False
        if family == "browser_session_recall":
            handle = args.get("handle")
            return isinstance(handle, str) and re.fullmatch(r"[0-9a-fA-F]{32}", handle) is not None
    return True


def terminal_low_risk_route(
    facts: ToolDecisionFacts,
    *,
    policy_level: str = ALLOW_LEVEL,
    default_ask: bool = False,
    structured_read_guard_level: str = "allow",
) -> DecisionRoute | None:
    """Return the closed low-risk path-read route before base ASK."""
    if deterministic_guard_route(facts) is not None:
        return None
    unsupported_facts = any(
        (
            facts.tool_name not in _STRUCTURED_WORKSPACE_READ_TOOLS,
            facts.capability.facts_source != "host_static",
            facts.capability.alias_conflict,
            not facts.arguments_valid_object,
            facts.capability.category != "path",
            facts.capability.operation_family != "workspace_read",
            facts.capability.risk_tier != "low",
            facts.capability.high_flex,
            facts.capability.static_side_effects,
            bool(facts.external_paths),
            bool(facts.write_paths),
            not facts.accesses_known,
            structured_read_guard_level not in {"allow", "neutral"},
        )
    )
    policy_allows = policy_level == ALLOW_LEVEL or (
        policy_level == ASK_LEVEL and default_ask
    )
    if unsupported_facts or not policy_allows:
        return None
    try:
        if facts.tool_name == "lsp":
            _LSP_INPUT_ADAPTER.validate_python(dict(facts.untrusted_args))
        else:
            schema_factory = _STRUCTURED_WORKSPACE_READ_SCHEMAS[facts.tool_name]
            validate_json_schema(
                instance=dict(facts.untrusted_args),
                schema=schema_factory("en"),
            )
    except (
        JsonSchemaValidationError,
        KeyError,
        TypeError,
        ValueError,
        ValidationError,
    ):
        return None
    if not _read_paths_within_workspace(facts):
        return None
    return DecisionRoute(
        ALLOW_LEVEL,
        "structured_workspace_read_allow",
        "deterministic_guard",
    )


def _read_paths_within_workspace(facts: ToolDecisionFacts) -> bool:
    """Prove every canonical structured read remains under primary workspace."""

    if not facts.workspace_root or not facts.read_paths:
        return False
    try:
        workspace = Path(facts.workspace_root).resolve(strict=False)
        return all(
            Path(path).resolve(strict=False).is_relative_to(workspace)
            for path in facts.read_paths
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        return False


def terminal_internal_route(facts: ToolDecisionFacts) -> DecisionRoute | None:
    """Return the allow route for closed canonical Host actions."""
    network = inspect_network_scope(facts)
    source_invalid = (
        facts.capability.facts_source != "host_static"
        or facts.capability.alias_conflict
        or not facts.arguments_valid_object
    )
    arguments_invalid = not _closed_internal_args_valid(facts, network)
    effects_invalid = (
        facts.capability.risk_tier != "low"
        or facts.capability.high_flex
    )
    path_access_present = bool(facts.external_paths) or bool(facts.write_paths)
    execution_access_present = bool(facts.command) or bool(network.absolute_uris)
    if source_invalid or arguments_invalid or effects_invalid:
        return None
    if path_access_present or execution_access_present:
        return None
    allowed_effects = {
        "browser_session_observe": frozenset({"session_cache_write"}),
        "browser_session_recall": frozenset(),
        "memory_read": frozenset(),
        "memory_search": frozenset(),
        "session_status": frozenset(),
        "skill_read": frozenset(),
        "task_state": frozenset(),
    }
    expected_effects = allowed_effects.get(facts.capability.operation_family)
    if expected_effects is None or facts.capability.static_side_effects != expected_effects:
        return None
    return DecisionRoute(ALLOW_LEVEL, "canonical_internal_action_allow", "internal")


def post_policy_route(
    facts: ToolDecisionFacts,
    *,
    policy_level: str,
) -> DecisionRoute | None:
    """Prevent high-flex and high-effect actions from becoming final ALLOW."""
    if policy_level != ALLOW_LEVEL:
        return None
    if HIGH_EFFECT_SIDE_EFFECTS.intersection(facts.capability.static_side_effects):
        return DecisionRoute(ASK_LEVEL, "high_effect_tool", "post_policy")
    if facts.capability.high_flex:
        return DecisionRoute(ASK_LEVEL, "high_flex_tool", "post_policy")
    return None
