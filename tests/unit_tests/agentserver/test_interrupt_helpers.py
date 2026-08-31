import asyncio
from types import SimpleNamespace

import pytest
from openjiuwen.harness.rails.security.tool_security_rail import (
    PermissionInterruptRail,
)

from jiuwenswarm.agents.harness.common.rails.interrupt.interrupt_helpers import (
    build_verified_permission_ask_user_question,
    build_permission_rail,
    convert_interactions_to_ask_user_question,
)
from jiuwenswarm.agents.harness.common.rails.permissions.auto_permission_rail import (
    AutoPermissionInterruptRail,
)
from jiuwenswarm.agents.harness.common.rails.permissions._auto_permission.models import (
    PermissionInterruptRequest,
)
from jiuwenswarm.agents.harness.common.rails.permissions.tool_decision_facts import (
    build_tool_decision_facts,
)
from jiuwenswarm.agents.harness.common.rails.permissions.persistent_audit import (
    PersistentAuditWriter,
)
from jiuwenswarm.agents.harness.common.rails.permissions.permission_interrupt_rail import (
    JiuwenSwarmPermissionInterruptRail,
)
from jiuwenswarm.agents.harness.common.rails.permissions.root_permission_queue import (
    RootPermissionQueue,
)
from jiuwenswarm.server.runtime.agent_adapter.browser_runtime_security import (
    BrowserRuntimeSecurityProfile,
)


def test_permission_builder_defaults_to_develop_manual_rail() -> None:
    rail = build_permission_rail(
        {"permissions": {"enabled": True, "mode": "auto"}},
        llm=object(),
    )

    assert isinstance(rail, PermissionInterruptRail)
    assert isinstance(rail, JiuwenSwarmPermissionInterruptRail)
    assert not isinstance(rail, AutoPermissionInterruptRail)


def test_explicit_auto_builder_uses_installed_config(tmp_path) -> None:
    rail = build_permission_rail(
        {"permissions": {"enabled": True, "mode": "auto"}},
        enable_auto_permission=True,
        workspace_root=tmp_path,
    )

    assert isinstance(rail, AutoPermissionInterruptRail)
    assert rail.auto_reviewer is None
    assert rail.persistent_audit_writer is None
    assert rail.base_rail._host.get_permissions_snapshot() == rail.permission_config


def test_explicit_auto_builder_preserves_host_browser_security_profile(
    tmp_path,
) -> None:
    profile = BrowserRuntimeSecurityProfile(
        network_guard_enforced=True,
        guard_provider="test-guard",
    )

    rail = build_permission_rail(
        {"permissions": {"enabled": True, "mode": "auto"}},
        enable_auto_permission=True,
        workspace_root=tmp_path,
        browser_runtime_security_profile=profile,
    )

    assert isinstance(rail, AutoPermissionInterruptRail)
    assert rail.browser_runtime_security_profile is profile


def test_explicit_auto_builder_composes_enabled_persistent_audit(tmp_path) -> None:
    audit_root = tmp_path / "audit-data"
    rail = build_permission_rail(
        {
            "permissions": {
                "enabled": True,
                "mode": "auto",
                "auto": {
                    "persistent_audit_enabled": True,
                    "persistent_audit_root": audit_root.as_posix(),
                },
            }
        },
        enable_auto_permission=True,
        workspace_root=tmp_path,
    )

    assert isinstance(rail, AutoPermissionInterruptRail)
    assert isinstance(rail.persistent_audit_writer, PersistentAuditWriter)
    facts = build_tool_decision_facts(
        "read_file",
        {"path": (tmp_path / "secret-token.txt").as_posix()},
        workspace_root=tmp_path,
        original_args_were_valid_object=True,
    )

    result = rail._emit_audit(
        facts,
        decision="allow",
        reason="reviewer_allow_once",
        degraded=False,
    )

    assert result.persisted is True
    content = result.path.read_text(encoding="utf-8")
    assert "secret-token.txt" not in content
    assert tmp_path.as_posix() not in content


def test_explicit_auto_builder_degrades_when_audit_root_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("JIUWENSWARM_DATA_DIR", raising=False)
    rail = build_permission_rail(
        {
            "permissions": {
                "enabled": True,
                "mode": "auto",
                "auto": {"persistent_audit_enabled": True},
            }
        },
        enable_auto_permission=True,
        workspace_root=tmp_path,
    )

    assert isinstance(rail, AutoPermissionInterruptRail)
    facts = build_tool_decision_facts(
        "read_file",
        {"path": "README.md"},
        workspace_root=tmp_path,
        original_args_were_valid_object=True,
    )

    result = rail._emit_audit(
        facts,
        decision="allow",
        reason="reviewer_allow_once",
        degraded=False,
    )

    assert result.persisted is False
    assert result.degraded is True
    assert result.reason == "audit_root_unavailable"


def test_explicit_auto_builder_wires_isolated_reviewer_from_llm(tmp_path) -> None:
    class RebuildableModel:
        def __init__(self, *, model_client_config, model_config) -> None:
            self.model_client_config = model_client_config
            self.model_config = model_config

    model = RebuildableModel(
        model_client_config={"model_name": "reviewer-test"},
        model_config={"temperature": 0.5},
    )
    rail = build_permission_rail(
        {
            "permissions": {
                "enabled": True,
                "mode": "auto",
                "auto": {
                    "reviewer_timeout_ms": 4321,
                    "reviewer_min_confidence": 0.0,
                },
            }
        },
        llm=model,
        enable_auto_permission=True,
        workspace_root=tmp_path,
    )

    assert isinstance(rail, AutoPermissionInterruptRail)
    assert rail.auto_reviewer is not None
    assert rail.auto_reviewer.client._model is not model
    assert (
        rail.auto_reviewer.client._model.model_client_config
        == model.model_client_config
    )
    assert rail.auto_reviewer.client._model.model_config == model.model_config
    assert rail.auto_reviewer.timeout_seconds == pytest.approx(4.321)
    assert rail.auto_reviewer.min_confidence == 0.0


def test_explicit_auto_builder_with_unrebuildable_model_fails_closed(
    tmp_path,
) -> None:
    rail = build_permission_rail(
        {"permissions": {"enabled": True, "mode": "auto"}},
        llm=object(),
        enable_auto_permission=True,
        workspace_root=tmp_path,
    )

    assert isinstance(rail, AutoPermissionInterruptRail)
    assert rail.auto_reviewer is None


@pytest.mark.parametrize(
    "permissions",
    [
        {"enabled": False, "mode": "auto"},
        {"enabled": "true", "mode": "auto"},
        {"enabled": True, "mode": "manual"},
        {"mode": "auto"},
    ],
)
def test_explicit_auto_builder_rejects_invalid_activation_snapshot(
    permissions: dict[str, object],
) -> None:
    with pytest.raises(
        ValueError,
        match="auto_permission_activation_requires_enabled_auto_mode",
    ):
        build_permission_rail(
            {"permissions": permissions},
            enable_auto_permission=True,
        )


@pytest.mark.parametrize("enabled", [False, "true", 1, None])
def test_permission_builder_requires_exact_enabled_true(enabled: object) -> None:
    rail = build_permission_rail(
        {"permissions": {"enabled": enabled, "mode": "manual"}},
    )

    assert rail is None


def _evolution_interrupt(
    tool_name: str,
    operation: str,
    *,
    metadata: dict | None = None,
):
    if operation == "evolve":
        message = "是否批准 Skill 'demo-skill' 的 1 条演进经验？"
    else:
        message = "是否执行 Skill 'demo-skill' 的 1 项经验精简操作？"

    value = {
        "message": message,
        "tool_name": tool_name,
        "metadata": metadata
        or {
            "source": "evolution_interrupt",
            "interrupt_kind": "skill_evolution_approval",
        },
        "ui_options": [
            {
                "label": "本次允许",
                "value": "allow_once",
                "description": "允许本次技能演进变更执行",
            },
            {
                "label": "总是允许",
                "value": "allow_always",
                "description": "自动允许后续匹配的技能演进变更",
            },
            {"label": "拒绝", "value": "reject", "description": "跳过本次技能演进变更"},
        ],
    }
    return SimpleNamespace(
        id="call_123",
        value=value,
    )


@pytest.mark.parametrize(
    ("tool_name", "operation", "approval_kind", "question"),
    [
        (
            "simplify_skill_experiences",
            "simplify",
            "simplify",
            "是否执行 Skill 'demo-skill' 的 1 项经验精简操作？",
        ),
        (
            "evolve_skill_experiences",
            "evolve",
            "evolve",
            "是否批准 Skill 'demo-skill' 的 1 条演进经验？",
        ),
    ],
)
def test_structured_evolution_approval_interrupt_is_classified(
    tool_name,
    operation,
    approval_kind,
    question,
):
    interaction = _evolution_interrupt(tool_name, operation)

    result = convert_interactions_to_ask_user_question([interaction])

    assert result is not None
    assert result["source"] == "evolution_interrupt"
    assert result["approval_kind"] == approval_kind
    assert "approval_schema" not in result
    assert "evolution_meta" not in result
    assert "rail_kind" not in result
    assert "approval_detail" not in result["questions"][0]
    assert result["questions"][0]["question"] == question
    assert [option["value"] for option in result["questions"][0]["options"]] == [
        "allow_once",
        "allow_always",
        "reject",
    ]


def test_skill_evolution_tool_name_without_detail_is_classified():
    interaction = SimpleNamespace(
        id="call_123",
        value={
            "message": "Skill evolution approval required.",
            "tool_name": "simplify_skill_experiences",
        },
    )

    result = convert_interactions_to_ask_user_question([interaction])

    assert result is not None
    assert result["source"] == "evolution_interrupt"
    assert result["approval_kind"] == "simplify"
    assert result["questions"][0]["question"] == "Skill evolution approval required."


def test_legacy_skill_evolution_approval_metadata_is_classified():
    interaction = _evolution_interrupt(
        "evolve_skill_experiences",
        "evolve",
        metadata={"source": "skill_evolution_approval"},
    )

    result = convert_interactions_to_ask_user_question([interaction])

    assert result is not None
    assert result["source"] == "evolution_interrupt"
    assert result["approval_kind"] == "evolve"


def _bind_single_permission_interaction(interaction) -> RootPermissionQueue:
    tool_call_id = str(interaction.id)
    invocation_id = f"invocation:{tool_call_id}"
    queue = RootPermissionQueue(id_factory=lambda: invocation_id)
    card = queue.begin(
        root_session_id="root-session",
        request_id="root-request",
        runtime_mode="agent",
        agent_id="main-agent",
        execution_session_id="root-session",
        tool_call_id=tool_call_id,
        tool_name=str(_read_test_value(interaction.value, "tool_name") or "tool"),
    )
    metadata = dict(_read_test_value(interaction.value, "metadata") or {})
    metadata["tool_invocation_key"] = card.key.to_wire()
    _write_test_value(interaction.value, "tool_call_id", tool_call_id)
    _write_test_value(interaction.value, "metadata", metadata)
    request = PermissionInterruptRequest(
        message="permission required",
        payload_schema={},
        metadata=metadata,
        tool_name=str(_read_test_value(interaction.value, "tool_name") or "tool"),
        tool_call_id=tool_call_id,
    )
    queue.mark_pending(card.key, request=request, auto_manual=False, root_context=None)
    return queue


def _read_test_value(value, name):
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _write_test_value(value, name, item) -> None:
    if isinstance(value, dict):
        value[name] = item
    else:
        setattr(value, name, item)


def test_permission_interrupt_exposes_only_reviewer_ui_metadata() -> None:
    interaction = SimpleNamespace(
        id="tool-call-17",
        metadata={
            "decision_source": "auto_reviewer",
            "reviewer_status": "manual",
            "secret_context": "must-not-reach-ui",
        },
        value={
            "message": "reviewer_manual",
            "tool_name": "bash",
            "metadata": {
                "final_reviewer_status": "manual",
                "manual_reason_summary": "Review this command before execution.",
            },
        },
    )

    registry = _bind_single_permission_interaction(interaction)
    result = convert_interactions_to_ask_user_question(
        [interaction], root_permission_queue=registry
    )

    assert result is not None
    assert result["source"] == "permission_interrupt"
    question = result["questions"][0]
    assert [option["label"] for option in question["options"]] == [
        "本次允许",
        "会话内记住",
        "永久记住",
        "拒绝",
    ]
    assert question["card_id"] == "invocation:tool-call-17"
    assert "tool_call_id" not in question
    assert "tool_invocation_key" not in question
    assert question["reviewer_metadata"] == {
        "decision_source": "auto_reviewer",
        "final_reviewer_status": "manual",
        "manual_reason_summary": "Review this command before execution.",
        "reviewer_status": "manual",
    }
    assert "secret_context" not in question["reviewer_metadata"]


def test_verified_permission_card_keeps_core_locator_backend_only() -> None:
    interaction = SimpleNamespace(
        id="tool-call-fail-closed",
        value={
            "message": "policy_engine_check_failed",
            "tool_name": "free_search",
            "tool_args": {"query": "C12-S205-FAIL-CLOSED"},
            "metadata": {},
        },
    )
    queue = _bind_single_permission_interaction(interaction)
    key = queue.snapshot_scope(root_session_id="root-session")[0]
    card = queue.get(key)

    result = build_verified_permission_ask_user_question(interaction, card)

    assert result is not None
    assert result["request_id"] == "tool-call-fail-closed"
    question = result["questions"][0]
    assert question["card_id"] == "invocation:tool-call-fail-closed"
    assert "tool_call_id" not in question
    assert "tool_invocation_key" not in question


def test_permission_interrupt_projects_reviewer_metadata_from_request_object() -> None:
    tool_call_id = "tool-call-object"
    interaction = SimpleNamespace(
        id=tool_call_id,
        value=PermissionInterruptRequest(
            message="reviewer_manual",
            payload_schema={},
            metadata={},
            tool_name="bash",
            tool_call_id=tool_call_id,
            decision_source="auto_reviewer",
            reviewer_status="manual",
            final_reviewer_status="manual",
            manual_reason_summary="Review this command before execution.",
            secret_context="must-not-reach-ui",
        ),
    )
    registry = _bind_single_permission_interaction(interaction)

    result = convert_interactions_to_ask_user_question(
        [interaction], root_permission_queue=registry
    )

    assert result is not None
    question = result["questions"][0]
    assert question["reviewer_metadata"] == {
        "decision_source": "auto_reviewer",
        "final_reviewer_status": "manual",
        "manual_reason_summary": "Review this command before execution.",
        "reviewer_status": "manual",
    }
    assert "secret_context" not in question["reviewer_metadata"]


def test_permission_interrupt_projects_sanitized_bounded_tool_payload() -> None:
    interaction = SimpleNamespace(
        id="tool-call-payload",
        metadata={
            "reviewer_assessment_id": "internal-id",
            "decision_digest": "internal-digest",
            "reviewer_prompt": "internal-prompt",
            "manual_reason_summary": "Check token=plain-secret before approval.",
            "contract_gate_missing_evidence": [
                f"missing-{index}" for index in range(10)
            ],
        },
        value={
            "message": "reviewer_manual",
            "tool_name": "bash",
            "tool_args": {
                "command": "echo visible",
                "db_password": "hunter2",
                "secret_context": "must-not-reach-ui",
                "nested": {"secret_context": "also-hidden", "safe": True},
            },
        },
    )

    registry = _bind_single_permission_interaction(interaction)
    result = convert_interactions_to_ask_user_question(
        [interaction], root_permission_queue=registry
    )

    assert result is not None
    question = result["questions"][0]
    assert question["tool_payload"] == {
        "command": "echo visible",
        "db_password": "[REDACTED]",
        "nested": {"safe": True},
    }
    reviewer = question["reviewer_metadata"]
    assert (
        reviewer["manual_reason_summary"] == "Check token=[redacted] before approval."
    )
    assert reviewer["contract_gate_missing_evidence"][-1] == "[TRUNCATED]"
    assert len(reviewer["contract_gate_missing_evidence"]) == 9
    for forbidden in (
        "reviewer_assessment_id",
        "decision_digest",
        "reviewer_prompt",
        "secret_context",
    ):
        assert forbidden not in reviewer


def test_non_permission_interrupt_does_not_project_tool_payload() -> None:
    interaction = SimpleNamespace(
        id="confirm-payload",
        value={
            "message": "Please approve or reject?",
            "tool_name": "switch_mode",
            "tool_args": {"mode": "code"},
        },
    )

    result = convert_interactions_to_ask_user_question([interaction])

    assert result is not None
    assert "tool_payload" not in result["questions"][0]


def test_permission_interrupt_projects_only_opaque_live_card_id() -> None:
    interaction = SimpleNamespace(
        id="tool-call-17",
        value={
            "message": "approve this tool",
            "tool_name": "bash",
            "tool_call_id": "tool-call-17",
        },
    )
    registry = _bind_single_permission_interaction(interaction)
    result = convert_interactions_to_ask_user_question(
        [interaction], root_permission_queue=registry
    )

    question = result["questions"][0]
    assert question["card_id"] == "invocation:tool-call-17"
    assert "tool_invocation_key" not in question
    assert "tool_call_id" not in question


def test_live_permission_locator_takes_priority_over_plain_query_shape() -> None:
    interaction = SimpleNamespace(
        id="tool-call-query",
        value={
            "message": "reviewer_manual",
            "tool_name": "bash",
            "tool_args": {"query": "humanoid robot embodied AI news August 2026"},
        },
    )
    registry = _bind_single_permission_interaction(interaction)

    result = convert_interactions_to_ask_user_question(
        [interaction], root_permission_queue=registry
    )

    assert result is not None
    assert result["source"] == "permission_interrupt"
    assert result["request_id"] == "tool-call-query"


def test_permission_like_plain_query_without_locator_fails_closed() -> None:
    interaction = SimpleNamespace(
        id="tool-call-missing",
        value={
            "message": "reviewer_manual",
            "tool_name": "bash",
            "tool_args": {"query": "clarify this command"},
        },
    )

    assert convert_interactions_to_ask_user_question([interaction]) is None


def test_invalid_permission_locator_blocks_structured_ask_fallback() -> None:
    interaction = SimpleNamespace(
        id="tool-call-forged",
        value={
            "tool_name": "ask_user",
            "questions": [{"question": "Approve?", "header": "Approval"}],
            "metadata": {"tool_invocation_key": {"version": 1}},
        },
    )

    assert convert_interactions_to_ask_user_question([interaction]) is None


def test_active_permission_locator_fails_closed_before_projection() -> None:
    interaction = SimpleNamespace(
        id="tool-call-active",
        value={"message": "reviewer_manual", "tool_name": "bash"},
    )
    invocation_id = "invocation:active"
    registry = RootPermissionQueue(id_factory=lambda: invocation_id)
    card = registry.begin(
        root_session_id="root-session",
        request_id="root-request",
        runtime_mode="agent",
        agent_id="main-agent",
        execution_session_id="root-session",
        tool_call_id=interaction.id,
        tool_name="bash",
    )
    interaction.value["metadata"] = {"tool_invocation_key": card.key.to_wire()}

    assert (
        convert_interactions_to_ask_user_question(
            [interaction], root_permission_queue=registry
        )
        is None
    )


def test_live_permission_locator_takes_priority_over_parallel_ask_shell() -> None:
    permission = SimpleNamespace(
        id="tool-call-live",
        value={"message": "reviewer_manual", "tool_name": "bash"},
    )
    registry = _bind_single_permission_interaction(permission)
    ask = SimpleNamespace(
        id="ask-call",
        value={
            "tool_name": "ask_user",
            "questions": [{"question": "Unrelated?", "header": "Question"}],
        },
    )

    result = convert_interactions_to_ask_user_question(
        [permission, ask], root_permission_queue=registry
    )

    assert result is not None
    assert result["source"] == "permission_interrupt"
    assert result["request_id"] == "tool-call-live"


@pytest.mark.parametrize(
    ("tool_name", "metadata"),
    [
        ("switch_mode", {}),
        (
            "simplify_skill_experiences",
            {
                "source": "evolution_interrupt",
                "interrupt_kind": "skill_evolution_approval",
            },
        ),
    ],
)
def test_live_permission_locator_cannot_be_reclassified(
    tool_name: str,
    metadata: dict[str, str],
) -> None:
    interaction = SimpleNamespace(
        id=f"tool-call-{tool_name}",
        value={
            "message": "Please approve or reject?",
            "tool_name": tool_name,
            "metadata": metadata,
        },
    )
    registry = _bind_single_permission_interaction(interaction)

    result = convert_interactions_to_ask_user_question(
        [interaction], root_permission_queue=registry
    )

    assert result is not None
    assert result["source"] == "permission_interrupt"
    assert "approval_kind" not in result


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_mixed_permission_set_with_missing_locator_fails_closed(
    nested: bool,
    reverse: bool,
) -> None:
    live = SimpleNamespace(
        id="tool-call-live",
        value={"message": "reviewer_manual", "tool_name": "bash"},
    )
    registry = _bind_single_permission_interaction(live)
    missing = SimpleNamespace(
        id="tool-call-missing",
        value={"message": "reviewer_manual", "tool_name": "bash"},
    )
    interactions = [missing, live] if reverse else [live, missing]
    state_outputs = [interactions] if nested else interactions

    assert (
        convert_interactions_to_ask_user_question(
            state_outputs, root_permission_queue=registry
        )
        is None
    )


def test_permission_interrupt_without_root_queue_fails_closed() -> None:
    interaction = SimpleNamespace(
        id="interaction-17",
        value={
            "message": "approve this tool",
            "tool_name": "bash",
            "metadata": {
                "tool_invocation_key": {
                    "version": 1,
                    "invocation_id": "tiv-17",
                    "root_session_id": "session-1",
                    "request_id": "request-1",
                    "executor_kind": "agent",
                    "execution_session_id": "session-1",
                    "tool_call_id": "tool-call-17",
                }
            },
        },
    )

    result = convert_interactions_to_ask_user_question([interaction])

    assert result is None


def test_permission_interrupt_drops_partial_invocation_key() -> None:
    interaction = SimpleNamespace(
        id="interaction-17",
        value={
            "message": "approve this tool",
            "tool_name": "bash",
            "metadata": {
                "tool_invocation_key": {
                    "version": 1,
                    "invocation_id": "partial",
                }
            },
        },
    )

    result = convert_interactions_to_ask_user_question([interaction])

    assert result is None


def test_confirm_interrupt_does_not_expose_reviewer_ui_metadata() -> None:
    interaction = SimpleNamespace(
        id="confirm-17",
        value={
            "message": "Please approve or reject?",
            "tool_name": "switch_mode",
            "metadata": {"reviewer_status": "manual"},
        },
    )

    result = convert_interactions_to_ask_user_question([interaction])

    assert result is not None
    assert result["source"] == "confirm_interrupt"
    question = result["questions"][0]
    assert "tool_call_id" not in question
    assert "reviewer_metadata" not in question


def _scene_hook_input(normalized_tool_name: str, user_input):
    from openjiuwen.harness.security.host import PermissionSceneHookInput

    return PermissionSceneHookInput(
        ctx=SimpleNamespace(session=None),
        tool_call=SimpleNamespace(id="call_1", name=normalized_tool_name, arguments={}),
        user_input=user_input,
        normalized_tool_name=normalized_tool_name,
        tool_args={},
        engine=None,
    )


def _permission_scene_hook(permission_config=None):
    rail = build_permission_rail(
        {"permissions": permission_config or {"enabled": True}}
    )
    assert rail is not None
    hook = rail._host.permission_scene_hook
    assert hook is not None
    return hook


def test_scene_hook_approves_ask_user_on_resume():
    """Regression for issue #1976.

    The permission rail intercepts every tool. On resume it would otherwise
    grab the ask_user answer as its own user_input and re-raise a permission
    interrupt, making the option card re-pop forever. The scene hook must
    approve ask_user so its answer reaches the model.
    """
    hook = _permission_scene_hook()
    resume_answer = {
        "answers": {"__free_text__": "数据处理"},
        "original_request": "...",
    }

    outcome = asyncio.run(hook(_scene_hook_input("ask_user", resume_answer)))

    assert outcome == ("approve",)


def test_scene_hook_approves_ask_user_on_first_pass():
    hook = _permission_scene_hook()

    outcome = asyncio.run(hook(_scene_hook_input("ask_user", None)))

    assert outcome == ("approve",)


def test_scene_hook_leaves_other_tools_to_engine():
    """Non-interactive tools must still fall through to the tiered engine
    (returns ``None``) when no owner-scope context is set."""
    hook = _permission_scene_hook()

    outcome = asyncio.run(hook(_scene_hook_input("bash", None)))

    assert outcome is None


def test_build_multi_questions_ignores_string_options():
    """Regression for #2331: options='a,b' must not become character options + Other."""
    from jiuwenswarm.agents.harness.common.rails.interrupt.interrupt_helpers import (
        _build_multi_questions,
    )

    questions = _build_multi_questions(
        [
            {
                "question": "Which option?",
                "header": "Choice",
                "options": "a,b",
            }
        ]
    )

    assert len(questions) == 1
    assert questions[0]["options"] == []


def test_build_multi_questions_appends_other_for_valid_options():
    from jiuwenswarm.agents.harness.common.rails.interrupt.interrupt_helpers import (
        _build_multi_questions,
    )

    questions = _build_multi_questions(
        [
            {
                "question": "Which option?",
                "header": "Choice",
                "options": [
                    {"label": "A", "description": "opt a"},
                    {"label": "B", "description": "opt b"},
                ],
            }
        ]
    )

    assert [opt["label"] for opt in questions[0]["options"]] == ["A", "B", "Other"]


@pytest.mark.parametrize(
    ("owner_level", "expected"),
    [
        ("allow", ("approve",)),
        (
            "ask",
            ("reject", "[PERMISSION_DENIED] 该工具未被授权 (owner_scopes: ask)"),
        ),
        (
            "deny",
            ("reject", "[PERMISSION_DENIED] 该工具未被授权 (owner_scopes: deny)"),
        ),
    ],
)
def test_scene_hook_applies_non_avatar_principal_owner_scope(
    owner_level: str,
    expected: tuple[str, ...],
) -> None:
    from jiuwenswarm.agents.harness.common.rails.permissions.owner_scopes import (
        cleanup_permission_context,
        setup_permission_context,
    )

    installed_permissions = {
        "enabled": True,
        "owner_scopes": {
            "web": {
                "principal-42": {
                    "tools": {"bash": owner_level},
                }
            }
        },
    }
    token = setup_permission_context(
        SimpleNamespace(
            channel_id="web",
            metadata={"principal_user_id": "principal-42"},
        )
    )
    try:
        outcome = asyncio.run(
            _permission_scene_hook(installed_permissions)(
                _scene_hook_input("bash", None)
            )
        )
    finally:
        cleanup_permission_context(token)

    assert outcome == expected
