# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from __future__ import annotations

import pytest

from jiuwenclaw.tool_arguments_validator import validate_tool_arguments


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
