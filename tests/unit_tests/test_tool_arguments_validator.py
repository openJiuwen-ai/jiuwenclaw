# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from __future__ import annotations

import pytest

from jiuwenclaw.tool_arguments_validator import (
    tool_arguments_failure_message,
    tool_arguments_failure_payload,
    validate_tool_arguments,
)


@pytest.mark.parametrize(
    ("arguments", "expected_kind", "ok"),
    [
        ('{"path":"a"}', "valid_object", True),
        ({"path": "a"}, "valid_object", True),
        ('{"path":"a"', "truncated", False),
        ('{"content":"abc', "truncated", False),
        ('["a"]', "not_object", False),
        ('"abc"', "not_object", False),
        ("", "invalid_json", False),
        (None, "not_string_or_dict", False),
    ],
)
def test_validate_tool_arguments_classifies_inputs(arguments, expected_kind, ok):
    validation = validate_tool_arguments(arguments)

    assert validation.ok is ok
    assert validation.kind == expected_kind
    if ok:
        assert validation.normalized.startswith("{")
    else:
        assert validation.normalized == "{}"


def test_validate_tool_arguments_respects_length_as_truncation_signal():
    validation = validate_tool_arguments('{"path":', finish_reason="length")

    assert validation.ok is False
    assert validation.kind == "truncated"
    assert validation.finish_reason == "length"


@pytest.mark.parametrize("finish_reason", ["tool_calls", "stop", None])
def test_validate_tool_arguments_does_not_trust_finish_reason_for_bad_json(finish_reason):
    validation = validate_tool_arguments('{"path":"a"', finish_reason=finish_reason)

    assert validation.ok is False
    assert validation.kind == "truncated"


def test_stream_event_rail_ensure_json_arguments_sanitizes_bad_history():
    from jiuwenclaw.agentserver.deep_agent.rails.stream_event_rail import JiuClawStreamEventRail

    ensure_json_arguments = getattr(JiuClawStreamEventRail, "_ensure_json_arguments")

    assert ensure_json_arguments('{"path":"a"') == "{}"
    assert ensure_json_arguments({"path": "a"}) == '{"path": "a"}'


def test_wire_sanitizer_normalizes_tool_arguments_before_provider_call():
    from jiuwenclaw.jiuwen_core_patch import _sanitize_wire_tool_arguments

    params = {
        "messages": [
            {
                "role": "assistant",
                "finish_reason": "length",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "write_file", "arguments": '{"path":"a"'},
                    }
                ],
            }
        ]
    }

    _sanitize_wire_tool_arguments(params)

    assert params["messages"][0]["tool_calls"][0]["function"]["arguments"] == "{}"


@pytest.mark.asyncio
async def test_ability_manager_patch_skips_invalid_arguments():
    from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
    from openjiuwen.core.single_agent.ability_manager import AbilityManager

    from jiuwenclaw.jiuwen_core_patch import apply_tool_invoke_interface_log

    execute_attr = "_execute_single_tool_call"
    original_execute = getattr(AbilityManager, execute_attr)
    calls = []

    async def fake_execute(self, tool_call, session, tag=None):
        calls.append((tool_call, session, tag))
        return {"success": True}, None

    try:
        setattr(AbilityManager, execute_attr, fake_execute)
        apply_tool_invoke_interface_log()

        manager = AbilityManager()
        tool_call = ToolCall(
            id="call_1",
            type="function",
            name="write_file",
            arguments='{"path":"a"',
        )
        patched_execute = getattr(manager, execute_attr)
        result, tool_message = await patched_execute(tool_call, session=None)

        assert calls == []
        assert result["success"] is False
        assert result["skipped"] is True
        assert result["recovery_hint"] == "split_large_tool_arguments"
        assert result["kind"] == "truncated"
        assert tool_message.tool_call_id == "call_1"
        assert "已跳过真实工具执行" in tool_message.content
    finally:
        setattr(AbilityManager, execute_attr, original_execute)


@pytest.mark.parametrize(
    ("arguments", "expected_kind"),
    [
        ('{"tasks": search}', "invalid_json"),
        ('{"tasks": \u641c\u7d22\u539f\u6587"}', "invalid_json"),
        ('{"a": 1, "b": unquoted}', "invalid_json"),
    ],
)
def test_structural_json_errors_are_not_truncated(arguments, expected_kind):
    validation = validate_tool_arguments(arguments)

    assert validation.ok is False
    assert validation.kind == expected_kind
    assert validation.reason.startswith("非法")


def test_failure_message_differs_by_kind():
    truncated = validate_tool_arguments('{"path":"a"')
    invalid_json = validate_tool_arguments('{"tasks": search}')

    msg_truncated = tool_arguments_failure_message(tool_name="write_file", validation=truncated)
    msg_invalid = tool_arguments_failure_message(tool_name="todo_create", validation=invalid_json)

    assert "疑似被截断" in msg_truncated
    assert "拆分成多次" in msg_truncated
    assert "双引号" in msg_invalid
    assert "语法错误" in msg_invalid
    assert "无需拆分" in msg_invalid


def test_failure_payload_recovery_hint_differs_by_kind():
    truncated = validate_tool_arguments('{"path":"a"')
    invalid_json = validate_tool_arguments('{"tasks": search}')

    payload_truncated = tool_arguments_failure_payload(tool_name="write_file", validation=truncated)
    payload_invalid = tool_arguments_failure_payload(tool_name="todo_create", validation=invalid_json)

    assert payload_truncated["recovery_hint"] == "split_large_tool_arguments"
    assert payload_invalid["recovery_hint"] == "fix_json_syntax"
    assert payload_truncated["kind"] == "truncated"
    assert payload_invalid["kind"] == "invalid_json"


@pytest.mark.asyncio
async def test_call_llm_patch_propagates_finish_reason_via_contextvar():
    from jiuwenclaw.jiuwen_core_patch import (
        _tool_finish_reason_var,
        apply_react_agent_finish_reason_patch,
    )

    try:
        from openjiuwen.core.single_agent.agents.react_agent import ReActAgent
    except Exception:
        pytest.skip("ReActAgent not available")

    class FakeMessage:
        def __init__(self, finish_reason):
            self.finish_reason = finish_reason

    async def _fake_call_llm(self, messages, tools=None):
        return FakeMessage(finish_reason="length")

    had_call_llm = hasattr(ReActAgent, "_call_llm")
    original = getattr(ReActAgent, "_call_llm", None)
    ReActAgent._call_llm = _fake_call_llm  # pylint: disable=protected-access
    try:
        apply_react_agent_finish_reason_patch()
        patched = ReActAgent._call_llm  # pylint: disable=protected-access
        assert getattr(patched, "_jiuwen_finish_reason_patched", False) is True

        _tool_finish_reason_var.set(None)
        result = await patched(object(), [])
        assert result.finish_reason == "length"
        assert _tool_finish_reason_var.get() == "length"
    finally:
        if had_call_llm:
            ReActAgent._call_llm = original  # pylint: disable=protected-access
        else:
            try:
                delattr(ReActAgent, "_call_llm")
            except AttributeError:
                pass


@pytest.mark.asyncio
async def test_patched_execute_uses_finish_reason_from_contextvar():
    from jiuwenclaw.jiuwen_core_patch import (
        _tool_finish_reason_var,
        apply_tool_invoke_interface_log,
    )

    from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
    from openjiuwen.core.single_agent.ability_manager import AbilityManager

    execute_attr = "_execute_single_tool_call"
    original_execute = getattr(AbilityManager, execute_attr)

    async def _noop_execute(self, tool_call, session, tag=None):
        return {"success": True}, None

    try:
        setattr(AbilityManager, execute_attr, _noop_execute)
        apply_tool_invoke_interface_log()
        patched_execute = getattr(AbilityManager, execute_attr)

        _tool_finish_reason_var.set("length")
        tool_call = ToolCall(
            id="call_1",
            type="function",
            name="write_file",
            arguments='{"path":',
        )
        result, tool_message = await patched_execute(object(), tool_call, session=None)

        assert result["success"] is False
        assert result["kind"] == "truncated"
        assert result["recovery_hint"] == "split_large_tool_arguments"

        _tool_finish_reason_var.set("tool_calls")
        tool_call2 = ToolCall(
            id="call_2",
            type="function",
            name="todo_create",
            arguments='{"tasks": search}',
        )
        result2, _ = await patched_execute(object(), tool_call2, session=None)

        assert result2["success"] is False
        assert result2["kind"] == "invalid_json"
        assert result2["recovery_hint"] == "fix_json_syntax"
    finally:
        setattr(AbilityManager, execute_attr, original_execute)
