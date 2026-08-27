"""Current end-to-end decision ordering for the root Auto Permission rail."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import jiuwenswarm.agents.harness.common.rails.permissions._auto_permission.before_tool as before_tool_module
import jiuwenswarm.agents.harness.common.rails.permissions._auto_permission.invocation_context as invocation_context_module
import jiuwenswarm.agents.harness.common.rails.permissions._auto_permission.reviewer_override_consume as override_module
import jiuwenswarm.agents.harness.common.rails.permissions.tool_decision_facts as facts_module
import pytest
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.core.runner.callback import AbortError
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.single_agent.interrupt.handler import ResumeContext, ToolInterruptHandler
from openjiuwen.core.single_agent.interrupt.state import (
    INTERRUPT_AUTO_CONFIRM_KEY,
    ToolInterruptEntry,
    ToolInterruptionState,
)
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    InvokeInputs,
    ToolCallInputs,
)
from jiuwenswarm.agents.harness.common.rails.interrupt.interrupt_helpers import (
    build_permission_rail,
)
from jiuwenswarm.agents.harness.common.rails.permissions.auto_permission_rail import (
    AutoPermissionInterruptRail,
)
from jiuwenswarm.agents.harness.common.rails.permissions.auto_reviewer import (
    AutoReviewer,
    ReviewerOutcome,
)
from jiuwenswarm.agents.harness.common.rails.permissions.artifact_path_post_gate import (
    ArtifactCandidateState,
    ArtifactPathCandidate,
)
from jiuwenswarm.agents.harness.common.rails.permissions.artifact_path_provenance import (
    SessionArtifactPathProvenance,
)
from jiuwenswarm.agents.harness.common.rails.permissions.openjiuwen_contract import (
    classify_permission_result,
)
from jiuwenswarm.agents.harness.common.rails.permissions.policy_eval import (
    PolicyEvaluation,
)
from jiuwenswarm.agents.harness.common.rails.permissions.permission_interrupt_rail import (
    mark_pre_permission_hard_rejection,
)
from jiuwenswarm.agents.harness.common.rails.permissions.root_permission_queue import (
    RootPermissionQueue,
)
from jiuwenswarm.agents.harness.common.rails.permissions.root_context import (
    RootDecisionContext,
    RootIntentTurn,
    RootIntentTurnKind,
    bind_root_decision_context,
    reset_root_decision_context,
)
from jiuwenswarm.agents.harness.common.rails.permissions.root_permission_queue_rail import (
    RootPermissionQueueRail,
    bind_root_permission_request,
    reset_root_permission_request,
)
from jiuwenswarm.agents.harness.common.rails.permissions.reviewer_stream_metadata import (
    peek_reviewer_tool_result_metadata,
)
from jiuwenswarm.agents.harness.common.rails.permissions.session_deny import (
    SessionDenyStore,
)
from jiuwenswarm.agents.harness.common.rails.permissions.tool_capabilities import (
    install_permission_file_semantics,
)
from jiuwenswarm.agents.harness.common.rails.permissions.tool_decision_facts import (
    DecisionRoute,
    build_tool_decision_facts,
)
from jiuwenswarm.agents.harness.common.tools.command_runtime import CommandRuntimePaths
from tests.unit_tests.agentserver.permissions.auto_permission_test_support import (
    FakeBaseRail,
    StaticPolicyEvaluator,
    StaticReviewerClient,
    _strong_sandbox,
)


def _rail(
    tmp_path,
    evaluation: PolicyEvaluation,
    *,
    session_denies: SessionDenyStore | None = None,
) -> tuple[
    AutoPermissionInterruptRail,
    StaticPolicyEvaluator,
    StaticReviewerClient,
    FakeBaseRail,
]:
    base = FakeBaseRail()
    policy = StaticPolicyEvaluator(evaluation)
    reviewer = StaticReviewerClient(outcome=ReviewerOutcome.ALLOW_ONCE)
    rail = AutoPermissionInterruptRail(
        base_rail=base,
        permission_config={"enabled": True, "mode": "auto"},
        workspace_root=tmp_path,
        policy_evaluator=policy,
        auto_reviewer=AutoReviewer(client=reviewer),
        session_deny_store=session_denies,
    )
    return rail, policy, reviewer, base


def _real_lsp_rail(
    tmp_path,
    *,
    workspace_level: str,
    file_guard_enabled: bool = True,
    explicit_tool_ask: bool = False,
):
    permissions = {
        "enabled": True,
        "schema": "tiered_policy",
        "mode": "auto",
        "defaults": {"*": "ask"},
        "file_guard": {
            "enabled": file_guard_enabled,
            "defaults": {"read": "ask", "write": "ask", "exec": "ask"},
            "workspace": {
                "read": workspace_level,
                "write": workspace_level,
                "exec": "ask",
            },
        },
    }
    if explicit_tool_ask:
        permissions["tools"] = {"lsp": "ask"}
    rail = build_permission_rail(
        {
            "permissions": permissions,
        },
        enable_auto_permission=True,
        workspace_root=tmp_path,
    )
    reviewer = StaticReviewerClient(outcome=ReviewerOutcome.ALLOW_ONCE)
    rail.auto_reviewer = AutoReviewer(client=reviewer)
    return rail, reviewer


async def test_task_tool_ask_is_control_silent_after_engine(tmp_path) -> None:
    rail, policy, reviewer, base = _rail(
        tmp_path,
        PolicyEvaluation(level="ask", reason="default_ask"),
    )

    result = await rail.before_tool_call(
        tool_name="task_tool",
        tool_args={"action": "status"},
        session_id="session-a",
    )

    assert result is None
    assert len(policy.calls) == 1
    assert reviewer.requests == []
    assert base.calls == []


async def test_domain_reviewer_receives_model_purpose_claim(tmp_path) -> None:
    rail, _policy, reviewer, _base = _rail(
        tmp_path,
        PolicyEvaluation(level="ask", reason="default_ask"),
    )
    facts = build_tool_decision_facts(
        "skill_tool",
        {"skill_name": "hot-news-pptx"},
        workspace_root=tmp_path,
        original_args_were_valid_object=True,
    )
    route = DecisionRoute(
        level="ask",
        reason="domain_policy_skill_readonly",
        source="semantic_reviewer",
    )

    result = await rail._handle_domain_route(
        facts,
        domain_route=route,
        policy_level="ask",
        session_id="session-a",
        request_id="request-a",
        tool_call_id="call-a",
        now=0.0,
        user_input=None,
        original_user_intent=None,
        model_purpose_claim="read the requested skill",
    )

    assert result.handled is True
    assert reviewer.requests[-1].review_evidence["model_purpose_claim"] == (
        "read the requested skill"
    )


async def test_purpose_claim_uses_host_invocation_after_send_resolution(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rail, _policy, reviewer, _base = _rail(
        tmp_path,
        PolicyEvaluation(level="ask", reason="default_ask"),
    )
    host_args = {
        "description": "host-owned purpose",
        "abs_file_path_list": [str(tmp_path / "report.csv")],
        "target_channels": ["web"],
    }
    observed: list[dict[str, object]] = []
    real_resolver = before_tool_module.resolve_model_purpose_claim

    def replace_with_host(_args, _kwargs, invocation):
        return SimpleNamespace(
            invocation=replace(invocation, tool_args=host_args),
            identity=None,
            error="send_file_authorization_context_missing",
        )

    def record_claim(arguments):
        observed.append(dict(arguments))
        return real_resolver(arguments)

    monkeypatch.setattr(
        before_tool_module,
        "_resolve_trusted_send_identity",
        replace_with_host,
    )
    monkeypatch.setattr(
        before_tool_module,
        "resolve_model_purpose_claim",
        record_claim,
    )

    await rail.before_tool_call(
        tool_name="send_file_to_user",
        tool_args={"description": "compatibility purpose"},
        session_id="session-a",
    )

    assert observed == [host_args]
    assert reviewer.requests == []


async def test_semantic_artifact_round_trip_becomes_later_reviewer_evidence(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        invocation_context_module,
        "current_command_runtime_paths",
        lambda **_kwargs: CommandRuntimePaths(
            current_cwd=tmp_path,
            project_root=tmp_path,
            workspace_root=tmp_path,
            agent_workspace_root=tmp_path,
        ),
    )
    base = FakeBaseRail()
    reviewer = StaticReviewerClient(
        outcome=ReviewerOutcome.ALLOW_ONCE,
        reason_code="task_aligned_code",
        artifact_paths=("report.csv",),
    )
    ledger = SessionArtifactPathProvenance("session-a")
    rail = AutoPermissionInterruptRail(
        base_rail=base,
        permission_config={"enabled": True, "mode": "auto"},
        workspace_root=tmp_path,
        policy_evaluator=StaticPolicyEvaluator(
            PolicyEvaluation(level="ask", reason="default_ask")
        ),
        auto_reviewer=AutoReviewer(client=reviewer),
        session_artifact_paths=ledger,
    )
    session = _Session()
    create_ctx = _runtime_ctx(
        session,
        tool_name="mcp_exec_command",
        tool_args={
            "command": "python -c \"open('report.csv','w').write('ok')\"",
            "description": "  create the requested report  ",
            "call_goal": "ignored fallback",
            "workdir": str(tmp_path),
        },
    )
    intent_token = bind_root_decision_context(
        RootDecisionContext(
            "session-a",
            "request-a",
            "web",
            (
                RootIntentTurn(
                    request_id="request-a",
                    kind=RootIntentTurnKind.FRESH,
                    text="Create report.csv, then inspect and deliver it.",
                ),
            ),
        )
    )
    try:
        result = await rail.before_tool_call(create_ctx)
    finally:
        reset_root_decision_context(intent_token)

    assert result is None
    assert reviewer.requests[-1].review_evidence["model_purpose_claim"] == (
        "  create the requested report  "
    )
    assert create_ctx.inputs.tool_call.arguments["description"] == (
        "  create the requested report  "
    )
    assert "call_goal" not in create_ctx.inputs.tool_call.arguments
    output = tmp_path / "report.csv"
    output.write_text("ok", encoding="utf-8")
    create_ctx.inputs.tool_result = json.dumps({"exit_code": 0})
    await rail.after_tool_call(create_ctx)
    assert ledger.contains(root_session_id="session-a", path=output)

    reviewer.artifact_paths = ()
    inspect_ctx = _runtime_ctx(
        session,
        tool_name="mcp_exec_command",
        tool_args={
            "command": "wc -l report.csv",
            "call_goal": "inspect the generated report",
            "workdir": str(tmp_path),
        },
    )
    intent_token = bind_root_decision_context(
        RootDecisionContext(
            "session-a",
            "request-b",
            "web",
            (
                RootIntentTurn(
                    request_id="request-b",
                    kind=RootIntentTurnKind.FRESH,
                    text="Inspect the generated report.csv.",
                ),
            ),
        )
    )
    try:
        second_result = await rail.before_tool_call(inspect_ctx)
    finally:
        reset_root_decision_context(intent_token)

    assert second_result is None
    assert reviewer.requests[-1].review_evidence["model_purpose_claim"] == (
        "inspect the generated report"
    )
    assert reviewer.requests[-1].review_evidence[
        "trusted_session_artifact_paths"
    ] == ["report.csv"]


@pytest.mark.parametrize(
    ("outcome", "expected_result_class"),
    [
        (ReviewerOutcome.ALLOW_ONCE, "allowed"),
        (ReviewerOutcome.MANUAL, "interrupt"),
        (ReviewerOutcome.DENY, "denied"),
    ],
)
async def test_artifact_evidence_preserves_reviewer_outcome(
    tmp_path,
    outcome: str,
    expected_result_class: str,
) -> None:
    output = tmp_path / "report.csv"
    output.write_text("draft", encoding="utf-8")
    ledger = SessionArtifactPathProvenance("session-a")
    recorded = ledger.record_verified(
        state=ArtifactCandidateState(
            session_id="session-a",
            tool_name="write_file",
            tool_call_id="producer-call",
            workspace_root=str(tmp_path),
            effective_workdir=str(tmp_path),
            candidates=(
                ArtifactPathCandidate(
                    path=str(output),
                    requires_grounding=False,
                ),
            ),
            grounding_texts=(),
            facts=None,
        )
    )
    assert recorded.accepted == 1
    reviewer = StaticReviewerClient(outcome=outcome)
    rail = AutoPermissionInterruptRail(
        base_rail=FakeBaseRail(),
        permission_config={"enabled": True, "mode": "auto"},
        workspace_root=tmp_path,
        policy_evaluator=StaticPolicyEvaluator(
            PolicyEvaluation(level="ask", reason="default_ask")
        ),
        auto_reviewer=AutoReviewer(client=reviewer),
        session_artifact_paths=ledger,
    )
    ctx = _runtime_ctx(
        _Session(),
        tool_name="write_file",
        tool_args={"path": str(output), "content": "final"},
    )
    intent_token = bind_root_decision_context(
        RootDecisionContext(
            "session-a",
            "request-a",
            "web",
            (
                RootIntentTurn(
                    request_id="request-a",
                    kind=RootIntentTurnKind.FRESH,
                    text="Finalize the generated report.csv.",
                ),
            ),
        )
    )
    try:
        if outcome == ReviewerOutcome.MANUAL:
            with pytest.raises(AbortError):
                await rail.before_tool_call(ctx)
            result_class = "interrupt"
        else:
            result = await rail.before_tool_call(ctx)
            result_class = (
                "allowed" if result is None else classify_permission_result(result)
            )
    finally:
        reset_root_decision_context(intent_token)

    assert result_class == expected_result_class
    assert len(reviewer.requests) == 1
    request = reviewer.requests[0]
    assert request.allowed_outcomes == ("allow_once", "manual", "deny")
    assert request.review_evidence["trusted_session_artifact_paths"] == ["report.csv"]
    if outcome == ReviewerOutcome.ALLOW_ONCE:
        metadata = peek_reviewer_tool_result_metadata(
            ctx.extra,
            tool_call_id="call-1",
        )
        assert metadata is not None
        assert metadata["decision_source"] == "auto_reviewer"


async def test_bash_nonroot_workdir_does_not_register_same_named_root_file(
    tmp_path,
) -> None:
    root_output = tmp_path / "report.csv"
    root_output.write_text("unrelated", encoding="utf-8")
    workdir = tmp_path / "subdir"
    workdir.mkdir()
    actual_output = workdir / "report.csv"
    reviewer = StaticReviewerClient(
        outcome=ReviewerOutcome.ALLOW_ONCE,
        reason_code="task_aligned_code",
        artifact_paths=("subdir/report.csv",),
    )
    ledger = SessionArtifactPathProvenance("session-a")
    rail = AutoPermissionInterruptRail(
        base_rail=FakeBaseRail(),
        permission_config={"enabled": True, "mode": "auto"},
        workspace_root=tmp_path,
        sandbox=replace(
            _strong_sandbox(),
            execution_workspace_root=tmp_path.as_posix(),
        ),
        policy_evaluator=StaticPolicyEvaluator(
            PolicyEvaluation(level="ask", reason="default_ask")
        ),
        auto_reviewer=AutoReviewer(client=reviewer),
        session_artifact_paths=ledger,
    )
    session = _Session()
    create_ctx = _runtime_ctx(
        session,
        tool_name="bash",
        tool_args={
            "command": "python -c \"open('report.csv','w').write('ok')\"",
            "cwd": str(workdir),
            "shell_type": "bash",
        },
    )
    intent_token = bind_root_decision_context(
        RootDecisionContext(
            "session-a",
            "request-a",
            "web",
            (
                RootIntentTurn(
                    request_id="request-a",
                    kind=RootIntentTurnKind.FRESH,
                    text="Create subdir/report.csv and inspect it.",
                ),
            ),
        )
    )
    try:
        result = await rail.before_tool_call(create_ctx)
    finally:
        reset_root_decision_context(intent_token)

    assert result is None
    assert create_ctx.inputs.tool_call.arguments["workdir"] == str(workdir)
    assert "cwd" not in create_ctx.inputs.tool_call.arguments
    assert reviewer.requests[-1].review_evidence["effective_workdir"] == "subdir"
    actual_output.write_text("ok", encoding="utf-8")
    create_ctx.inputs.tool_result = json.dumps({"exit_code": 0})
    await rail.after_tool_call(create_ctx)
    assert ledger.contains(root_session_id="session-a", path=actual_output)
    assert not ledger.contains(root_session_id="session-a", path=root_output)

    reviewer.artifact_paths = ()
    inspect_ctx = _runtime_ctx(
        session,
        tool_name="bash",
        tool_args={
            "command": "wc -l report.csv",
            "workdir": str(workdir),
            "shell_type": "bash",
        },
    )
    intent_token = bind_root_decision_context(
        RootDecisionContext(
            "session-a",
            "request-b",
            "web",
            (
                RootIntentTurn(
                    request_id="request-b",
                    kind=RootIntentTurnKind.FRESH,
                    text="Inspect the generated subdir/report.csv.",
                ),
            ),
        )
    )
    try:
        second_result = await rail.before_tool_call(inspect_ctx)
    finally:
        reset_root_decision_context(intent_token)

    assert second_result is None
    assert reviewer.requests[-1].review_evidence[
        "trusted_session_artifact_paths"
    ] == ["subdir/report.csv"]


async def test_background_bash_does_not_register_existing_artifact_candidate(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "report.csv"
    output.write_text("pre-existing", encoding="utf-8")
    monkeypatch.setattr(
        facts_module,
        "extract_accesses_native",
        lambda *_args, **_kwargs: [(output, "write", "redirect")],
    )
    reviewer = StaticReviewerClient(
        outcome=ReviewerOutcome.ALLOW_ONCE,
        artifact_paths=("report.csv",),
    )
    ledger = SessionArtifactPathProvenance("session-a")
    rail = AutoPermissionInterruptRail(
        base_rail=FakeBaseRail(),
        permission_config={"enabled": True, "mode": "auto"},
        workspace_root=tmp_path,
        sandbox=replace(
            _strong_sandbox(),
            execution_workspace_root=tmp_path.as_posix(),
        ),
        policy_evaluator=StaticPolicyEvaluator(
            PolicyEvaluation(level="ask", reason="default_ask")
        ),
        auto_reviewer=AutoReviewer(client=reviewer),
        session_artifact_paths=ledger,
    )
    ctx = _runtime_ctx(
        _Session(),
        tool_name="bash",
        tool_args={
            "command": "printf ok > report.csv",
            "workdir": str(tmp_path),
            "run_in_background": True,
            "shell_type": "bash",
        },
    )

    result = await rail.before_tool_call(ctx)
    ctx.inputs.tool_result = ToolOutput(
        success=True,
        data={"pid": 123, "status": "started"},
    )
    await rail.after_tool_call(ctx)

    assert result is None
    assert not ledger.contains(root_session_id="session-a", path=output)


async def test_unfrozen_bash_workdir_suppresses_parser_write_candidate(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_output = tmp_path / "report.csv"
    root_output.write_text("unrelated", encoding="utf-8")
    runtime_cwd = tmp_path / "subdir"
    runtime_cwd.mkdir()
    monkeypatch.setattr(
        invocation_context_module,
        "current_command_runtime_paths",
        lambda **_kwargs: CommandRuntimePaths(
            current_cwd=runtime_cwd,
            project_root=tmp_path,
            workspace_root=tmp_path,
            agent_workspace_root=tmp_path,
        ),
    )
    monkeypatch.setattr(
        facts_module,
        "extract_accesses_native",
        lambda *_args, **_kwargs: [(root_output, "write", "redirect")],
    )
    reviewer = StaticReviewerClient(outcome=ReviewerOutcome.ALLOW_ONCE)
    ledger = SessionArtifactPathProvenance("session-a")
    rail = AutoPermissionInterruptRail(
        base_rail=FakeBaseRail(),
        permission_config={"enabled": True, "mode": "auto"},
        workspace_root=tmp_path,
        sandbox=replace(
            _strong_sandbox(),
            execution_workspace_root=tmp_path.as_posix(),
        ),
        policy_evaluator=StaticPolicyEvaluator(
            PolicyEvaluation(level="ask", reason="default_ask")
        ),
        auto_reviewer=AutoReviewer(client=reviewer),
        session_artifact_paths=ledger,
    )
    outside = tmp_path.parent / "outside"
    ctx = _runtime_ctx(
        _Session(),
        tool_name="bash",
        tool_args={
            "command": "printf ok > report.csv",
            "cwd": str(outside),
            "shell_type": "bash",
        },
    )

    result = await rail.before_tool_call(ctx)
    ctx.inputs.tool_result = ToolOutput(success=True, data={"content": ""})
    await rail.after_tool_call(ctx)

    assert result is None
    assert reviewer.requests[-1].review_evidence["effective_workdir"] == ""
    assert not ledger.contains(root_session_id="session-a", path=root_output)


async def test_exec_access_is_not_registered_as_host_artifact(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = tmp_path / "analyze.py"
    script.write_text("print('ok')", encoding="utf-8")
    monkeypatch.setattr(
        facts_module,
        "extract_accesses_native",
        lambda *_args, **_kwargs: [(script, "exec", "command")],
    )
    reviewer = StaticReviewerClient(outcome=ReviewerOutcome.ALLOW_ONCE)
    ledger = SessionArtifactPathProvenance("session-a")
    rail = AutoPermissionInterruptRail(
        base_rail=FakeBaseRail(),
        permission_config={"enabled": True, "mode": "auto"},
        workspace_root=tmp_path,
        policy_evaluator=StaticPolicyEvaluator(
            PolicyEvaluation(level="ask", reason="default_ask")
        ),
        auto_reviewer=AutoReviewer(client=reviewer),
        session_artifact_paths=ledger,
    )
    ctx = _runtime_ctx(
        _Session(),
        tool_name="mcp_exec_command",
        tool_args={"command": "python analyze.py"},
    )

    result = await rail.before_tool_call(ctx)
    ctx.inputs.tool_result = json.dumps({"exit_code": 0})
    await rail.after_tool_call(ctx)

    assert result is None
    assert not ledger.contains(root_session_id="session-a", path=script)


async def test_oversized_semantic_payload_goes_manual_without_reviewer_call(
    tmp_path,
) -> None:
    rail, _policy, reviewer, _base = _rail(
        tmp_path,
        PolicyEvaluation(level="ask", reason="default_ask"),
    )
    intent_token = bind_root_decision_context(
        RootDecisionContext(
            "session-a",
            "request-a",
            "web",
            (
                RootIntentTurn(
                    request_id="request-a",
                    kind=RootIntentTurnKind.FRESH,
                    text="Run the supplied script in the project workspace.",
                ),
            ),
        )
    )
    try:
        result = await rail.before_tool_call(
            tool_name="mcp_exec_command",
            tool_args={"command": "printf x\n" + "x" * (33 * 1024)},
            session_id="session-a",
        )
    finally:
        reset_root_decision_context(intent_token)

    assert classify_permission_result(result) == "interrupt"
    assert reviewer.requests == []


async def test_real_engine_deny_precedes_task_tool_control_silent(tmp_path) -> None:
    rail = build_permission_rail(
        {
            "permissions": {
                "enabled": True,
                "mode": "auto",
                "tools": {"task_tool": "deny"},
            }
        },
        enable_auto_permission=True,
        workspace_root=tmp_path,
    )

    result = await rail.before_tool_call(
        tool_name="task_tool",
        tool_args={"action": "status"},
        session_id="session-a",
    )

    assert classify_permission_result(result) == "denied"


@pytest.mark.parametrize(
    "tool_name", ["enter_plan_mode", "exit_plan_mode", "switch_mode"]
)
async def test_code_control_tools_are_silent_before_allow_capable_paths(
    tmp_path,
    tool_name: str,
) -> None:
    rail, policy, reviewer, base = _rail(
        tmp_path,
        PolicyEvaluation(level="ask", reason="default_ask"),
    )

    result = await rail.before_tool_call(
        tool_name=tool_name,
        tool_args={},
        session_id="session-a",
        user_input={"approved": True, "auto_confirm": True},
    )

    assert result is None
    assert len(policy.calls) == 1
    assert reviewer.requests == []
    assert base.calls == []


async def test_edit_file_reviewer_receives_complete_diff(tmp_path) -> None:
    rail, _policy, reviewer, base = _rail(
        tmp_path,
        PolicyEvaluation(level="ask", reason="default_ask"),
    )

    result = await rail.before_tool_call(
        tool_name="edit_file",
        tool_args={
            "file_path": str(tmp_path / "README.md"),
            "old_string": "old",
            "new_string": "new",
        },
        session_id="session-a",
    )

    assert result is None
    assert len(reviewer.requests) == 1
    assert reviewer.requests[0].review_evidence["reviewable_payload"] == {
        "old_string": "old",
        "new_string": "new",
        "replace_all": False,
    }
    assert base.calls == []


async def test_incomplete_edit_payload_goes_manual_without_reviewer_call(
    tmp_path,
) -> None:
    rail, _policy, reviewer, base = _rail(
        tmp_path,
        PolicyEvaluation(level="ask", reason="default_ask"),
    )

    result = await rail.before_tool_call(
        tool_name="edit_file",
        tool_args={
            "file_path": str(tmp_path / "README.md"),
            "new_string": "new",
        },
        session_id="session-a",
    )

    assert classify_permission_result(result) == "interrupt"
    assert reviewer.requests == []
    assert base.calls == []


async def test_custom_agent_deny_precedes_all_allow_capable_paths(tmp_path) -> None:
    rail, policy, reviewer, base = _rail(
        tmp_path,
        PolicyEvaluation(level="allow", reason="configured_allow"),
    )

    result = await rail.before_tool_call(
        tool_name="Agent",
        tool_args={"subagent_type": "reviewer", "background": True},
        session_id="session-a",
        user_input={"approved": True, "auto_confirm": True},
    )

    assert classify_permission_result(result) == "denied"
    assert len(policy.calls) == 1
    assert reviewer.requests == []
    assert base.calls == []


async def test_engine_fail_closed_has_zero_reviewer_or_base_effect(tmp_path) -> None:
    rail, policy, reviewer, base = _rail(
        tmp_path,
        PolicyEvaluation(
            level="ask",
            reason="permission_engine_failed",
            source="fail_closed",
        ),
    )

    result = await rail.before_tool_call(
        tool_name="read_file",
        tool_args={"path": str(tmp_path / "README.md")},
        session_id="session-a",
    )

    assert classify_permission_result(result) == "interrupt"
    assert len(policy.calls) == 1
    assert reviewer.requests == []
    assert base.calls == []


async def test_lsp_workspace_read_bypasses_reviewer_and_manual_base(tmp_path) -> None:
    rail, reviewer = _real_lsp_rail(tmp_path, workspace_level="allow")
    session = _Session()
    ctx = _runtime_ctx(
        session,
        tool_name="lsp",
        tool_args={
            "operation": "goToDefinition",
            "file_path": "src/main.py",
            "line": 1,
            "character": 1,
        },
    )

    result = await rail.before_tool_call(ctx)

    assert result is None
    assert reviewer.requests == []
    assert "deterministic_allow" in repr(ctx.extra)
    assert "reviewer_lifecycle" in repr(ctx.extra)


@pytest.mark.parametrize(
    ("tool_name", "tool_args"),
    [
        ("read_file", {"file_path": "README.md"}),
        ("list_files", {"path": "src", "show_hidden": False}),
        ("grep", {"pattern": "main", "path": "src"}),
    ],
)
async def test_default_ask_structured_workspace_read_skips_reviewer(
    tmp_path,
    tool_name: str,
    tool_args: dict[str, object],
) -> None:
    rail = build_permission_rail(
        {
            "permissions": {
                "enabled": True,
                "schema": "tiered_policy",
                "mode": "auto",
                "defaults": {"*": "ask"},
                "file_guard": {
                    "enabled": True,
                    "defaults": {"read": "ask", "write": "ask", "exec": "ask"},
                    "workspace": {
                        "read": "allow",
                        "write": "ask",
                        "exec": "ask",
                    },
                },
            }
        },
        enable_auto_permission=True,
        workspace_root=tmp_path,
    )
    reviewer = StaticReviewerClient(outcome=ReviewerOutcome.ALLOW_ONCE)
    rail.auto_reviewer = AutoReviewer(client=reviewer)
    ctx = _runtime_ctx(
        _Session(),
        tool_name=tool_name,
        tool_args=tool_args,
    )

    result = await rail.before_tool_call(ctx)

    assert result is None
    assert reviewer.requests == []
    metadata = repr(ctx.extra)
    assert "structured_workspace_read_allow" in metadata
    assert "'reviewer_called': False" in metadata
    assert "'reviewer_lifecycle': 'not_called'" in metadata


async def test_explicit_lsp_tool_ask_still_calls_reviewer(tmp_path) -> None:
    rail, reviewer = _real_lsp_rail(
        tmp_path,
        workspace_level="allow",
        explicit_tool_ask=True,
    )

    result = await rail.before_tool_call(
        tool_name="lsp",
        tool_args={
            "operation": "documentSymbol",
            "file_path": (tmp_path / "src" / "main.py").as_posix(),
        },
        session_id="session-a",
    )

    assert result is None
    assert len(reviewer.requests) == 1


async def test_invalid_lsp_input_does_not_use_fast_path(tmp_path) -> None:
    install_permission_file_semantics()
    rail, policy, reviewer, base = _rail(
        tmp_path,
        PolicyEvaluation(level="ask", reason="default_ask"),
    )

    result = await rail.before_tool_call(
        tool_name="lsp",
        tool_args={
            "operation": "goToDefinition",
            "file_path": "src/main.py",
        },
        session_id="session-a",
    )

    assert result is None
    assert len(policy.calls) == 1
    assert len(reviewer.requests) == 1
    assert base.calls == []


async def test_lsp_file_guard_ask_does_not_use_fast_path(tmp_path) -> None:
    source_path = (tmp_path / "src" / "main.py").as_posix()
    rail, reviewer = _real_lsp_rail(tmp_path, workspace_level="ask")

    result = await rail.before_tool_call(
        tool_name="lsp",
        tool_args={
            "operation": "documentSymbol",
            "file_path": source_path,
        },
        session_id="session-a",
    )

    assert result is None
    assert len(reviewer.requests) == 1


async def test_lsp_external_path_cannot_fast_allow_when_file_guard_disabled(
    tmp_path,
) -> None:
    rail, reviewer = _real_lsp_rail(
        tmp_path,
        workspace_level="allow",
        file_guard_enabled=False,
    )
    ctx = _runtime_ctx(
        _Session(),
        tool_name="lsp",
        tool_args={
            "operation": "documentSymbol",
            "file_path": (tmp_path.parent / "outside.py").as_posix(),
        },
    )

    result = await rail.before_tool_call(ctx)

    assert result is None
    assert len(reviewer.requests) == 1
    assert "deterministic_allow" not in repr(ctx.extra)


async def test_pre_permission_hard_rejection_skips_auto_permission(tmp_path) -> None:
    rail, policy, reviewer, base = _rail(
        tmp_path,
        PolicyEvaluation(level="allow", reason="configured_allow"),
    )
    ctx = _runtime_ctx(
        _Session(),
        tool_name="bash",
        tool_args={"command": "mkdir src/generated"},
    )
    ctx.extra["_skip_tool"] = True
    mark_pre_permission_hard_rejection(ctx)

    result = await rail.before_tool_call(ctx)

    assert result is None
    assert policy.calls == []
    assert reviewer.requests == []
    assert base.calls == []


async def test_admitted_manual_rejection_skips_policy_re_evaluation(
    tmp_path, monkeypatch
) -> None:
    rail, policy, reviewer, base = _rail(
        tmp_path,
        PolicyEvaluation(
            level="ask",
            reason="permission_engine_failed",
            source="fail_closed",
        ),
    )
    monkeypatch.setattr(
        before_tool_module,
        "root_permission_resume_from_context",
        lambda _ctx: SimpleNamespace(
            card=SimpleNamespace(key=SimpleNamespace(request_id="original-request"))
        ),
    )

    result = await rail.before_tool_call(
        tool_name="mcp_free_search",
        tool_args={"query": "blocked"},
        session_id="session-a",
        user_input={
            "approved": False,
            "auto_confirm": False,
            "feedback": "用户拒绝",
        },
    )

    assert classify_permission_result(result) == "user_rejection"
    assert policy.calls == []
    assert reviewer.requests == []
    assert base.calls == []


async def test_exact_session_deny_precedes_engine_allow_and_reviewer(tmp_path) -> None:
    denies = SessionDenyStore()
    denies.record_denial(
        session_id="session-a",
        tool_name="write_file",
        tool_args={"path": "report.md", "content": "draft"},
        reason="user_rejected",
    )
    rail, policy, reviewer, base = _rail(
        tmp_path,
        PolicyEvaluation(level="allow", reason="engine_allow"),
        session_denies=denies,
    )

    result = await rail.before_tool_call(
        tool_name="write_file",
        tool_args={"path": "report.md", "content": "draft"},
        session_id="session-a",
    )

    assert classify_permission_result(result) == "denied"
    assert len(policy.calls) == 1
    assert reviewer.requests == []
    assert base.calls == []


async def test_generic_mcp_keeps_manual_ceiling(tmp_path) -> None:
    rail, policy, reviewer, base = _rail(
        tmp_path,
        PolicyEvaluation(level="allow", reason="engine_allow"),
    )

    result = await rail.before_tool_call(
        tool_name="mcp_docs_lookup",
        tool_args={"query": "asyncio"},
        session_id="session-a",
    )

    assert classify_permission_result(result) == "interrupt"
    assert len(policy.calls) == 1
    assert reviewer.requests == []
    assert base.calls == []


class _Session:
    def __init__(self) -> None:
        self.session_id = "session-a"
        self.state: dict[str, object] = {}

    def get_state(self, key: str) -> object | None:
        return self.state.get(key)

    def update_state(self, values: dict[str, object]) -> None:
        self.state.update(values)


def _runtime_ctx(
    session: _Session,
    *,
    tool_name: str,
    tool_args: dict[str, object],
) -> AgentCallbackContext:
    tool_call = SimpleNamespace(id="call-1", name=tool_name, arguments=tool_args)
    return AgentCallbackContext(
        agent=SimpleNamespace(),
        inputs=ToolCallInputs(
            tool_call=tool_call,
            tool_name=tool_name,
            tool_args=tool_args,
        ),
        session=session,
        extra={},
    )


def _install_core_auto_confirm_contract(base: FakeBaseRail) -> None:
    base._get_auto_confirm_key = lambda tool_call: tool_call.name
    base._is_auto_confirmed = lambda config, key: bool(
        isinstance(config, dict) and config.get(key) is True
    )

    def store(ctx, key):
        config = ctx.session.get_state(INTERRUPT_AUTO_CONFIRM_KEY) or {}
        ctx.session.update_state(
            {INTERRUPT_AUTO_CONFIRM_KEY: {**dict(config), key: True}}
        )

    base._store_auto_confirm = store


async def test_reviewer_cancellation_finishes_only_active_permission_card(
    tmp_path,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingReviewerClient:
        async def assess(self, request: object) -> str:
            del request
            started.set()
            await release.wait()
            raise AssertionError("cancelled reviewer must not complete")

    queue = RootPermissionQueue(id_factory=lambda: "invocation-1")
    queue_rail = RootPermissionQueueRail(queue)
    base = FakeBaseRail()
    rail = AutoPermissionInterruptRail(
        base_rail=base,
        permission_config={"enabled": True, "mode": "auto"},
        workspace_root=tmp_path,
        policy_evaluator=StaticPolicyEvaluator(
            PolicyEvaluation(level="ask", reason="default_ask")
        ),
        auto_reviewer=AutoReviewer(client=BlockingReviewerClient()),
    )
    session = _Session()
    ctx = _runtime_ctx(
        session,
        tool_name="read_file",
        tool_args={"path": str(tmp_path / "README.md")},
    )
    token = bind_root_permission_request(
        root_session_id="session-a",
        request_id="request-a",
        runtime_mode="agent",
        agent_id="main-agent",
        enabled=True,
        queue=queue,
    )
    try:
        await queue_rail.before_tool_call(ctx)
        task = asyncio.create_task(rail.before_tool_call(ctx))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        reset_root_permission_request(token)

    assert queue.has_live(root_session_id="session-a") is False
    assert queue.begin_cutover(root_session_id="session-a") is False


async def test_auto_manual_session_choice_reuses_core_state_before_reviewer(
    tmp_path, monkeypatch
) -> None:
    rail, policy, reviewer, base = _rail(
        tmp_path,
        PolicyEvaluation(level="ask", reason="default_ask"),
    )
    _install_core_auto_confirm_contract(base)
    session = _Session()
    args = {"file_path": str(tmp_path / "report.md")}
    ctx = _runtime_ctx(session, tool_name="edit_file", tool_args=args)
    monkeypatch.setattr(
        override_module,
        "root_permission_resume_from_context",
        lambda _ctx: SimpleNamespace(card=SimpleNamespace(auto_manual=True)),
    )
    invocation = before_tool_module._extract_invocation((ctx,), {})
    facts = build_tool_decision_facts(
        "edit_file",
        args,
        workspace_root=tmp_path,
        original_args_were_valid_object=True,
    )

    first = await rail._consume_reviewer_override(
        facts,
        invocation=invocation,
        user_input={"approved": True, "auto_confirm": True, "feedback": ""},
        domain_route=None,
    )

    assert first.handled is True
    assert session.get_state(INTERRUPT_AUTO_CONFIRM_KEY) == {"edit_file": True}

    monkeypatch.setattr(
        before_tool_module,
        "root_permission_resume_from_context",
        lambda _ctx: None,
    )
    result = await rail.before_tool_call(ctx)

    assert result is None
    assert len(policy.calls) == 1
    assert reviewer.requests == []


async def test_shell_never_stores_or_consumes_session_auto_confirm(
    tmp_path,
    monkeypatch,
) -> None:
    rail, policy, reviewer, base = _rail(
        tmp_path,
        PolicyEvaluation(level="ask", reason="default_ask"),
    )
    _install_core_auto_confirm_contract(base)
    session = _Session()
    args = {"command": "ls"}
    ctx = _runtime_ctx(session, tool_name="mcp_exec_command", tool_args=args)
    monkeypatch.setattr(
        override_module,
        "root_permission_resume_from_context",
        lambda _ctx: SimpleNamespace(card=SimpleNamespace(auto_manual=True)),
    )
    invocation = before_tool_module._extract_invocation((ctx,), {})
    facts = build_tool_decision_facts(
        "mcp_exec_command",
        args,
        workspace_root=tmp_path,
        original_args_were_valid_object=True,
    )

    first = await rail._consume_reviewer_override(
        facts,
        invocation=invocation,
        user_input={"approved": True, "auto_confirm": True, "feedback": ""},
        domain_route=None,
    )

    assert first.handled is True
    assert facts.accesses_known is False
    assert session.get_state(INTERRUPT_AUTO_CONFIRM_KEY) in (None, {})

    session.update_state({INTERRUPT_AUTO_CONFIRM_KEY: {"mcp_exec_command": True}})
    monkeypatch.setattr(
        override_module,
        "root_permission_resume_from_context",
        lambda _ctx: None,
    )
    result = await rail.before_tool_call(ctx)

    assert result is None
    assert len(policy.calls) == 1
    assert len(reviewer.requests) == 1


async def test_session_auto_confirm_cannot_bypass_unknown_manual_ceiling(
    tmp_path,
) -> None:
    rail, policy, reviewer, base = _rail(
        tmp_path,
        PolicyEvaluation(level="ask", reason="default_ask"),
    )
    _install_core_auto_confirm_contract(base)
    session = _Session()
    session.update_state({INTERRUPT_AUTO_CONFIRM_KEY: {"mcp_docs_lookup": True}})
    ctx = _runtime_ctx(
        session,
        tool_name="mcp_docs_lookup",
        tool_args={"query": "asyncio"},
    )

    with pytest.raises(AbortError):
        await rail.before_tool_call(ctx)

    assert len(policy.calls) == 1
    assert reviewer.requests == []


async def test_resource_scoped_external_send_never_uses_tool_key_remember(
    tmp_path,
    monkeypatch,
) -> None:
    rail, _policy, _reviewer, base = _rail(
        tmp_path,
        PolicyEvaluation(level="ask", reason="default_ask"),
    )
    _install_core_auto_confirm_contract(base)
    session = _Session()
    args = {"file_path": str(tmp_path / "report.md")}
    ctx = _runtime_ctx(session, tool_name="upload_file", tool_args=args)
    monkeypatch.setattr(
        override_module,
        "root_permission_resume_from_context",
        lambda _ctx: SimpleNamespace(card=SimpleNamespace(auto_manual=True)),
    )
    invocation = before_tool_module._extract_invocation((ctx,), {})
    facts = build_tool_decision_facts(
        "upload_file",
        args,
        workspace_root=tmp_path,
        original_args_were_valid_object=True,
    )

    result = await rail._consume_reviewer_override(
        facts,
        invocation=invocation,
        user_input={"approved": True, "auto_confirm": True, "feedback": ""},
        domain_route=None,
    )

    assert result.handled is True
    assert session.get_state(INTERRUPT_AUTO_CONFIRM_KEY) in (None, {})


async def test_ineligible_auto_manual_never_reaches_core_session_remember(
    tmp_path,
) -> None:
    rail, policy, reviewer, base = _rail(
        tmp_path,
        PolicyEvaluation(level="ask", reason="default_ask"),
    )
    _install_core_auto_confirm_contract(base)
    session = _Session()
    unknown_ctx = _runtime_ctx(session, tool_name="edit_file", tool_args={})
    invocation = before_tool_module._extract_invocation((unknown_ctx,), {})
    request = rail._build_runtime_interrupt_request(
        invocation,
        {
            "status": "interrupt",
            "metadata": {"auto_permission_manual": True},
        },
    )
    assert request.auto_confirm_key == ""

    tool_call = ToolCall(
        id="call-1",
        type="function",
        name="edit_file",
        arguments="{}",
    )
    state = ToolInterruptionState(
        ai_message=AssistantMessage(tool_calls=[tool_call]),
        iteration=0,
        interrupted_tools={
            tool_call.id: ToolInterruptEntry(
                tool_call=tool_call,
                interrupt_requests={tool_call.id: request},
            )
        },
        auto_confirm_mapping={tool_call.id: request.auto_confirm_key},
    )
    answer = InteractiveInput()
    answer.update(
        tool_call.id,
        {"approved": True, "auto_confirm": True, "feedback": ""},
    )

    async def execute_tool_call(*_args):
        return [(None, None)]

    handler = ToolInterruptHandler(SimpleNamespace())
    await handler.handle_resume(
        ResumeContext(
            state=state,
            user_input=answer,
            ctx=unknown_ctx,
            context=SimpleNamespace(),
            session=session,
            invoke_inputs=InvokeInputs(query=answer),
            execute_tool_call=execute_tool_call,
        )
    )

    assert session.get_state(INTERRUPT_AUTO_CONFIRM_KEY) in (None, {})

    known_args = {
        "file_path": str(tmp_path / "report.md"),
        "old_string": "old",
        "new_string": "new",
    }
    known_ctx = _runtime_ctx(session, tool_name="edit_file", tool_args=known_args)
    result = await rail.before_tool_call(known_ctx)

    assert result is None
    assert len(policy.calls) == 1
    assert len(reviewer.requests) == 1
