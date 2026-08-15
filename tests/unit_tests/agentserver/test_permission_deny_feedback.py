"""Permission deny feedback — answer → ConfirmPayload → model tool_result envelope."""

from jiuwenswarm.agents.harness.common.rails.interrupt.interrupt_helpers import (
    format_permission_deny_tool_result,
    resolve_permission_deny_language,
)
from jiuwenswarm.server.runtime.agent_adapter.interface import JiuWenSwarm


def _confirm_payload(selected, custom_input="", *, source="permission_interrupt"):
    interactive = JiuWenSwarm._build_interactive_input_from_answers(
        "call_1",
        [{"selected_options": selected, "custom_input": custom_input}],
        source,
    )
    return interactive.user_inputs["call_1"]


def test_format_permission_deny_empty_default_cn():
    result = format_permission_deny_tool_result("用户拒绝", language="zh")
    assert result.startswith("[PERMISSION_DENIED]")
    assert "操作未执行" in result


def test_resolve_permission_deny_language_prefers_explicit_and_config(monkeypatch):
    assert resolve_permission_deny_language("en") == "en"
    assert resolve_permission_deny_language("cn") == "zh"
    monkeypatch.setattr(
        "jiuwenswarm.common.config.get_config",
        lambda: {"preferred_language": "en"},
    )
    assert resolve_permission_deny_language() == "en"


def test_format_permission_deny_with_note_en():
    result = format_permission_deny_tool_result(
        "dont do nothing",
        language="en",
    )
    assert result.startswith("[PERMISSION_DENIED]")
    assert "NOT performed" in result
    assert "dont do nothing" in result


def test_format_permission_deny_idempotent_when_already_prefixed():
    original = "[PERMISSION_DENIED] already wrapped"
    assert format_permission_deny_tool_result(original) == original


def test_permission_deny_without_feedback_uses_deny_envelope(monkeypatch):
    monkeypatch.setattr(
        "jiuwenswarm.common.config.get_config",
        lambda: {"preferred_language": "zh"},
    )
    payload = _confirm_payload(["拒绝"], source="permission_interrupt")
    assert payload["approved"] is False
    assert payload["feedback"].startswith("[PERMISSION_DENIED]")
    assert "操作未执行" in payload["feedback"]


def test_permission_deny_with_feedback_wraps_for_model(monkeypatch):
    monkeypatch.setattr(
        "jiuwenswarm.common.config.get_config",
        lambda: {"preferred_language": "en"},
    )
    payload = _confirm_payload(
        ["拒绝"], "use the Read tool instead", source="permission_interrupt"
    )
    assert payload["approved"] is False
    assert payload["feedback"].startswith("[PERMISSION_DENIED]")
    assert "use the Read tool instead" in payload["feedback"]
    assert "Follow the user's guidance" in payload["feedback"]


def test_confirm_interrupt_deny_keeps_raw_feedback():
    payload = _confirm_payload(
        ["Reject"], "do not delete that file", source="confirm_interrupt"
    )
    assert payload["approved"] is False
    assert payload["feedback"] == "do not delete that file"


def test_plan_revise_still_gets_raw_feedback_on_confirm_interrupt():
    """Regression guard: plan revise shares confirm_interrupt + reject."""
    payload = _confirm_payload(
        ["reject"], "把迁移拆成两个阶段", source="confirm_interrupt"
    )
    assert payload["approved"] is False
    assert payload["feedback"] == "把迁移拆成两个阶段"
