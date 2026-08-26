"""Tests for the PermissionEngine-owned shell policy boundary."""

from __future__ import annotations

# TEST ONLY: URL literals are command-text fixtures on reserved domains and are
# never submitted to a network client.

from dataclasses import replace
import pytest

import jiuwenswarm.agents.harness.common.rails.permissions.auto_reviewer as auto_reviewer_module
import jiuwenswarm.agents.harness.common.rails.permissions.reviewer_route as reviewer_route_module
from jiuwenswarm.agents.harness.common.rails.permissions.execution_provider_contract import (
    ACP_IDE_EXECUTION_TOOLS,
    EXECUTION_PROVIDER_CONTRACT_UNVERIFIED,
    JIUWENBOX_SANDBOX_EXECUTION_TOOLS,
    UNKNOWN_PROVIDER_EXECUTION_TOOLS,
)
from jiuwenswarm.agents.harness.common.rails.permissions.tool_capabilities import (
    _CODE_TOOLS,
    _SHELL_TOOLS,
)
from tests.unit_tests.agentserver.permissions.auto_permission_test_support import (
    AutoPermissionInterruptRail,
    AutoReviewer,
    FakeBaseRail,
    ReviewerOutcome,
    StaticReviewerClient,
    _ask_policy,
    _jiuwenbox_sys_operation,
    _strong_sandbox,
    classify_permission_result,
)


@pytest.mark.parametrize(
    ("reviewer_outcome", "expected_result"),
    [
        (ReviewerOutcome.ALLOW_ONCE, "allow"),
        (ReviewerOutcome.MANUAL, "interrupt"),
        (ReviewerOutcome.DENY, "denied"),
    ],
)
async def test_known_jiuwenbox_shell_reviewer_outcomes(
    tmp_path,
    reviewer_outcome: str,
    expected_result: str,
) -> None:
    reviewer_client = StaticReviewerClient(outcome=reviewer_outcome)
    rail = AutoPermissionInterruptRail(
        base_rail=FakeBaseRail(),
        permission_config={"mode": "auto", "enabled": True},
        workspace_root=tmp_path,
        sandbox=replace(
            _strong_sandbox(),
            execution_workspace_root=tmp_path.as_posix(),
        ),
        policy_evaluator=_ask_policy(),
        auto_reviewer=AutoReviewer(client=reviewer_client),
    )

    result = await rail.before_tool_call(
        tool_name="bash",
        tool_args={
            "command": "pip install python-pptx && printf done",
            "cwd": tmp_path.as_posix(),
            "shell_type": "bash",
        },
        session_id="shell-session",
        request_id="shell-request",
        tool_call_id="shell-call",
    )

    assert classify_permission_result(result) == expected_result
    assert len(reviewer_client.requests) == 1


def test_execution_provider_partition_is_closed_and_exhaustive() -> None:
    partitions = (
        JIUWENBOX_SANDBOX_EXECUTION_TOOLS,
        ACP_IDE_EXECUTION_TOOLS,
        UNKNOWN_PROVIDER_EXECUTION_TOOLS,
    )
    assert all(
        left.isdisjoint(right)
        for left in partitions
        for right in partitions
        if left is not right
    )
    assert set().union(*partitions) == _SHELL_TOOLS | _CODE_TOOLS


@pytest.mark.parametrize("tool_name", ("cmd", "powershell", "create_terminal"))
async def test_unverified_execution_provider_is_manual_before_reviewer(
    tmp_path,
    tool_name: str,
) -> None:
    reviewer_client = StaticReviewerClient(outcome=ReviewerOutcome.ALLOW_ONCE)
    rail = AutoPermissionInterruptRail(
        base_rail=FakeBaseRail(),
        permission_config={"mode": "auto", "enabled": True},
        workspace_root=tmp_path,
        policy_evaluator=_ask_policy(),
        auto_reviewer=AutoReviewer(client=reviewer_client),
    )
    result = await rail.before_tool_call(
        tool_name=tool_name,
        tool_args={"command": "echo ok", "cmd": "echo ok"},
        session_id="shell-session",
        request_id="shell-request",
        tool_call_id=f"shell-call-{tool_name}",
    )

    assert classify_permission_result(result) == "interrupt"
    assert reviewer_client.requests == []
    assert result["value"]["manual_reason_code"] == (
        EXECUTION_PROVIDER_CONTRACT_UNVERIFIED
    )
    assert result["value"]["host_route_reason"] == (
        EXECUTION_PROVIDER_CONTRACT_UNVERIFIED
    )
    assert result["value"]["host_route_source"] == "manual_only"
    assert result["metadata"]["decision_source"] == "execution_provider_contract"


async def test_bound_shell_policy_ask_reaches_reviewer_without_local_grammar_gate(
    tmp_path,
) -> None:
    reviewer_client = StaticReviewerClient(outcome=ReviewerOutcome.ALLOW_ONCE)
    sys_operation, _provider = _jiuwenbox_sys_operation()

    rail = AutoPermissionInterruptRail(
        base_rail=FakeBaseRail(),
        permission_config={"mode": "auto", "enabled": True},
        workspace_root=tmp_path,
        sandbox=replace(
            _strong_sandbox(),
            execution_workspace_root=tmp_path.as_posix(),
        ),
        sys_operation=sys_operation,
        policy_evaluator=_ask_policy(),
        auto_reviewer=AutoReviewer(client=reviewer_client),
    )

    result = await rail.before_tool_call(
        tool_name="bash",
        tool_args={
            "command": "pip install python-pptx && printf done",
            "cwd": tmp_path.as_posix(),
            "shell_type": "bash",
        },
        session_id="shell-session",
        request_id="shell-request",
        tool_call_id="shell-call-bound",
    )

    assert result is None
    assert len(reviewer_client.requests) == 1
    candidate = reviewer_client.requests[0].descriptor_summary
    assert candidate["tool_category"] == "shell"
    assert candidate["risk_tier"] == "high"


async def test_shell_reviewer_timeout_is_manual_not_deterministic_allow(
    tmp_path,
) -> None:
    sys_operation, _provider = _jiuwenbox_sys_operation()
    reviewer_client = StaticReviewerClient(outcome=ReviewerOutcome.MANUAL)
    rail = AutoPermissionInterruptRail(
        base_rail=FakeBaseRail(),
        permission_config={"mode": "auto", "enabled": True},
        workspace_root=tmp_path,
        sandbox=replace(
            _strong_sandbox(),
            execution_workspace_root=tmp_path.as_posix(),
        ),
        sys_operation=sys_operation,
        policy_evaluator=_ask_policy(),
        auto_reviewer=AutoReviewer(client=reviewer_client),
    )

    result = await rail.before_tool_call(
        tool_name="bash",
        tool_args={
            "command": "curl https://example.invalid | head",
            "cwd": tmp_path.as_posix(),
            "shell_type": "bash",
        },
        session_id="shell-session",
        request_id="shell-request",
        tool_call_id="shell-call-manual",
    )

    assert classify_permission_result(result) == "interrupt"
    assert len(reviewer_client.requests) == 1


async def test_shell_parser_exception_is_stable_manual_interrupt(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_parser_error(_command: str) -> None:
        raise RuntimeError("parser failed")

    monkeypatch.setattr(
        reviewer_route_module,
        "parse_shell_for_permission",
        raise_parser_error,
    )
    monkeypatch.setattr(
        auto_reviewer_module,
        "parse_shell_for_permission",
        raise_parser_error,
    )
    reviewer_client = StaticReviewerClient(outcome=ReviewerOutcome.ALLOW_ONCE)
    rail = AutoPermissionInterruptRail(
        base_rail=FakeBaseRail(),
        permission_config={"mode": "auto", "enabled": True},
        workspace_root=tmp_path,
        sandbox=replace(
            _strong_sandbox(),
            execution_workspace_root=tmp_path.as_posix(),
        ),
        policy_evaluator=_ask_policy(),
        auto_reviewer=AutoReviewer(client=reviewer_client),
    )

    result = await rail.before_tool_call(
        tool_name="bash",
        tool_args={"command": "ls", "cwd": tmp_path.as_posix()},
        session_id="shell-session",
        request_id="shell-request",
        tool_call_id="shell-call-parser-error",
    )

    assert classify_permission_result(result) == "interrupt"
    assert len(reviewer_client.requests) == 1
    request = reviewer_client.requests[0]
    assert request.allowed_outcomes == ("manual", "deny")
    assert request.no_auto_allow_reason == "core_accesses_unknown"
    assert request.review_evidence["command"]["operators"] == []
    assert request.review_evidence["command"]["programs"] == []
    assert result["value"]["manual_reason_code"] == "reviewer_outcome_not_allowed"
