"""Current contracts for compact deterministic routes."""

from __future__ import annotations

# TEST ONLY: URL fixtures use reserved domains or blocked security-test addresses;
# these policy tests perform no external network I/O.

from pathlib import Path
from types import SimpleNamespace

import pytest

from jiuwenswarm.agents.harness.common.rails.permissions.auto_decision import (
    deterministic_domain_route,
    deterministic_guard_route,
    generic_mcp_egress_evidence,
    post_policy_route,
    terminal_internal_route,
    terminal_low_risk_route,
)
from jiuwenswarm.agents.harness.common.rails.permissions.root_context import (
    OriginalUserIntentEvidence,
    UserIntentSource,
)
from jiuwenswarm.agents.harness.common.rails.permissions.tool_decision_facts import (
    build_tool_decision_facts,
)
from jiuwenswarm.agents.harness.common.rails.permissions.tool_capabilities import (
    install_permission_file_semantics,
)


def _facts(tool_name: str, args: dict[str, object], root: Path, **kwargs: object):
    return build_tool_decision_facts(
        tool_name,
        args,
        workspace_root=root,
        original_args_were_valid_object=True,
        **kwargs,
    )


def test_generic_mcp_uri_risks_are_evidence_not_terminal_authority(tmp_path: Path) -> None:
    facts = _facts(
        "mcp_untrusted_provider_call",
        {
            "callback_url": "custom://127.0.0.1/private",
            "archive": "ftp://files.example.invalid/archive",
        },
        tmp_path,
    )

    evidence = generic_mcp_egress_evidence(facts)

    assert "network_host_not_public" in evidence
    assert "generic_mcp_uri_provider_support_unproven" in evidence
    assert deterministic_guard_route(facts) is None


def test_core_known_workspace_read_can_be_terminal_low_risk(tmp_path: Path) -> None:
    facts = _facts("read_file", {"file_path": "README.md"}, tmp_path)
    assert terminal_low_risk_route(facts) is not None


@pytest.mark.parametrize(
    ("tool_name", "tool_args"),
    [
        ("read_file", {"file_path": "README.md", "offset": 1}),
        ("list_files", {"path": "src", "show_hidden": False}),
        ("grep", {"pattern": "main", "path": "src", "ignore_case": True}),
    ],
)
def test_closed_structured_workspace_reads_accept_default_ask(
    tmp_path: Path,
    tool_name: str,
    tool_args: dict[str, object],
) -> None:
    facts = _facts(tool_name, tool_args, tmp_path)

    route = terminal_low_risk_route(
        facts,
        policy_level="ask",
        default_ask=True,
        structured_read_guard_level="allow",
    )

    assert route is not None
    assert route.reason == "structured_workspace_read_allow"
    assert route.source == "deterministic_guard"


@pytest.mark.parametrize(
    ("policy_level", "default_ask", "guard_level"),
    [
        ("ask", False, "allow"),
        ("ask", True, "ask"),
        ("ask", True, "deny"),
        ("ask", True, "unevaluable"),
    ],
)
def test_structured_workspace_read_requires_positive_policy_evidence(
    tmp_path: Path,
    policy_level: str,
    default_ask: bool,
    guard_level: str,
) -> None:
    facts = _facts("read_file", {"file_path": "README.md"}, tmp_path)

    assert terminal_low_risk_route(
        facts,
        policy_level=policy_level,
        default_ask=default_ask,
        structured_read_guard_level=guard_level,
    ) is None


@pytest.mark.parametrize(
    ("tool_name", "tool_args"),
    [
        ("read_file", {"file_path": "README.md", "offset": "first"}),
        ("grep", {"path": "src"}),
        ("grep", {"pattern": "main", "output_mode": "unknown"}),
        ("glob", {"pattern": "**/*.py", "path": "src"}),
    ],
)
def test_invalid_or_excluded_structured_reads_do_not_fast_allow(
    tmp_path: Path,
    tool_name: str,
    tool_args: dict[str, object],
) -> None:
    facts = _facts(tool_name, tool_args, tmp_path)

    assert terminal_low_risk_route(facts) is None


def test_valid_workspace_lsp_can_be_terminal_low_risk(tmp_path: Path) -> None:
    install_permission_file_semantics()
    facts = _facts(
        "lsp",
        {
            "operation": "goToDefinition",
            "file_path": "src/main.py",
            "line": 1,
            "character": 1,
        },
        tmp_path,
    )

    assert terminal_low_risk_route(facts) is not None


@pytest.mark.parametrize(
    "tool_args",
    [
        {"operation": "unknown", "file_path": "src/main.py", "line": 1},
        {"operation": "goToDefinition", "file_path": "src/main.py"},
        {
            "operation": "goToDefinition",
            "file_path": "src/main.py",
            "line": "first",
        },
        {"operation": "workspaceSymbol", "file_path": "", "query": "main"},
    ],
)
def test_invalid_or_unbounded_lsp_is_not_terminal_low_risk(
    tmp_path: Path,
    tool_args: dict[str, object],
) -> None:
    install_permission_file_semantics()
    facts = _facts("lsp", tool_args, tmp_path)

    assert terminal_low_risk_route(facts) is None


def test_external_lsp_path_is_not_terminal_low_risk_without_engine_hint(
    tmp_path: Path,
) -> None:
    install_permission_file_semantics()
    external_path = (tmp_path.parent / "outside.py").as_posix()
    facts = _facts(
        "lsp",
        {
            "operation": "documentSymbol",
            "file_path": external_path,
        },
        tmp_path,
    )

    assert terminal_low_risk_route(facts) is None


def test_structured_read_symlink_escape_is_not_terminal_low_risk(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)
    link = tmp_path / "linked"
    link.symlink_to(outside, target_is_directory=True)
    facts = _facts(
        "read_file",
        {"file_path": (link / "secret.txt").as_posix()},
        tmp_path,
    )

    assert terminal_low_risk_route(facts) is None


def test_unknown_path_and_shell_are_never_promoted(tmp_path: Path) -> None:
    patch = _facts("apply_patch", {"patch": "*** Begin Patch"}, tmp_path)
    shell = _facts("bash", {"cmd": "ls src"}, tmp_path)
    assert patch.accesses_known is False
    assert terminal_low_risk_route(patch) is None
    assert terminal_low_risk_route(shell) is None


@pytest.mark.parametrize(
    ("tool_name", "tool_args"),
    [
        ("todo_get", {"id": "todo-1"}),
        ("session_list", {}),
        ("memory_get", {"path": "memory/MEMORY.md"}),
        ("memory_search", {"query": "project decisions"}),
        ("skill_tool", {"skill_name": "daily-report"}),
        ("browser_probe_cards", {"max_cards": "runtime-owned"}),
        ("browser_recall_offload", {"handle": "0123456789abcdef0123456789abcdef"}),
    ],
)
def test_closed_internal_actions_are_terminal_allows(
    tmp_path: Path,
    tool_name: str,
    tool_args: dict[str, object],
) -> None:
    assert terminal_internal_route(_facts(tool_name, tool_args, tmp_path)) is not None


@pytest.mark.parametrize(
    ("tool_name", "tool_args"),
    [
        ("session_list", {"unexpected": True}),
        ("todo_insert", {"idx": 1}),
        ("browser_probe_cards", {"target": "ftp://files.example.invalid/archive"}),
    ],
)
def test_open_or_legacy_internal_shapes_are_not_terminal_allows(
    tmp_path: Path,
    tool_name: str,
    tool_args: dict[str, object],
) -> None:
    assert terminal_internal_route(_facts(tool_name, tool_args, tmp_path)) is None


def test_high_effect_tool_requires_review_even_if_engine_allowed(tmp_path: Path) -> None:
    facts = _facts("upload_file", {"path": "out.txt"}, tmp_path)
    decision = post_policy_route(facts, policy_level="allow")
    assert decision is not None
    assert decision.level == "ask"


def test_search_skill_secret_guard_remains_terminal(tmp_path: Path) -> None:
    facts = _facts("search_skill", {"query": "api_key=super-secret-token"}, tmp_path)
    decision = deterministic_guard_route(facts)
    assert decision is not None
    assert decision.level == "deny"
    assert decision.reason == "search_skill_sensitive_query"


def test_browser_navigation_requires_runtime_network_guard(tmp_path: Path) -> None:
    url = "https://example.invalid/docs"
    facts = _facts("browser_navigate", {"url": url}, tmp_path)
    intent = OriginalUserIntentEvidence(
        source=UserIntentSource.HOST_USER_MESSAGE,
        text="Read the public documentation",
    )

    manual = deterministic_domain_route(
        facts,
        original_user_intent=intent,
        browser_runtime_security_profile=SimpleNamespace(network_guard_enforced=False),
    )
    reviewable = deterministic_domain_route(
        facts,
        original_user_intent=intent,
        browser_runtime_security_profile=SimpleNamespace(network_guard_enforced=True),
    )

    assert manual is not None and manual.requires_manual
    assert manual.reason == "browser_network_guard_unverified"
    assert reviewable is not None and reviewable.requires_reviewer


def test_browser_unsafe_url_is_hard_blocked(tmp_path: Path) -> None:
    facts = _facts("browser_navigate", {"url": "https://169.254.169.254/latest"}, tmp_path)
    decision = deterministic_domain_route(facts, original_user_intent=None)
    assert decision is not None and decision.is_hard_block
    assert decision.reason == "network_metadata_host"
