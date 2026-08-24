"""Focused contracts for static permission safety primitives."""

from types import SimpleNamespace

from jiuwenswarm.agents.harness.common.rails.permissions.execution_provider_contract import (
    requires_manual_execution_provider_review,
    requires_no_host_fallback,
)
from jiuwenswarm.agents.harness.common.rails.permissions.protected_paths import (
    JIUWENCLAW_PROTECTED_WRITE_PATHS,
    merge_protected_write_paths,
)
from jiuwenswarm.agents.harness.common.rails.permissions.sandbox_profile import (
    SandboxDescriptor,
    build_sandbox_profile,
)


def test_execution_provider_contract_is_closed_by_tool_identity() -> None:
    assert requires_no_host_fallback("bash", tool_category="shell") is True
    assert requires_no_host_fallback("code", tool_category="code") is True
    assert requires_no_host_fallback("create_terminal", tool_category="shell") is False
    assert (
        requires_manual_execution_provider_review(
            "create_terminal", tool_category="shell"
        )
        is True
    )
    assert (
        requires_manual_execution_provider_review("bash", tool_category="network")
        is False
    )


def test_sandbox_profile_reads_only_owned_string_roots() -> None:
    source = SimpleNamespace(
        sandbox_execution_workspace_root=" /sandbox/workspace ",
        sandbox_python_package_venv_root=object(),
        unrelated="ignored",
    )

    assert build_sandbox_profile(source) == SandboxDescriptor(
        execution_workspace_root="/sandbox/workspace",
        python_package_venv_root="",
    )
    assert build_sandbox_profile(None) == SandboxDescriptor()


def test_protected_path_merge_is_stable_and_deduplicated() -> None:
    merged = merge_protected_write_paths(
        (" custom ", JIUWENCLAW_PROTECTED_WRITE_PATHS[0]),
        JIUWENCLAW_PROTECTED_WRITE_PATHS,
    )

    assert merged[0] == "custom"
    assert merged.count(JIUWENCLAW_PROTECTED_WRITE_PATHS[0]) == 1
    assert merged[1:] == JIUWENCLAW_PROTECTED_WRITE_PATHS
