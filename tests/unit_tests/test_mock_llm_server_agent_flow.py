"""mock_llm_server loadtest Agent 阶段机单元测试。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    script = (
        Path(__file__).resolve().parents[2]
        / "packages/jiuwenclaw-ee/claw_manager/scripts/mock_llm_server.py"
    )
    spec = importlib.util.spec_from_file_location("mock_llm_server", script)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _assistant_tool(call_id: str, name: str, arguments: str = "{}") -> dict:
    return {
        "role": "assistant",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        ],
    }


def _tool_result(call_id: str, name: str | None = None, content: str = "ok") -> dict:
    msg: dict = {"role": "tool", "tool_call_id": call_id, "content": content}
    if name is not None:
        msg["name"] = name
    return msg


def test_stage_progresses_by_tool_name_not_count():
    mod = _load_module()
    messages: list[dict] = []
    assert mod._agent_flow_stage(messages) == 0

    messages.extend([_assistant_tool("c1", "todo_create"), _tool_result("c1", "todo_create")])
    assert mod._agent_flow_stage(messages) == 1

    messages.append({"role": "assistant", "content": "long opening"})
    assert mod._agent_flow_stage(messages) == 2

    messages.extend([_assistant_tool("c2", "bash"), _tool_result("c2", "bash")])
    assert mod._agent_flow_stage(messages) == 3

    messages.extend([_assistant_tool("c3", "write_file"), _tool_result("c3", "write_file")])
    assert mod._agent_flow_stage(messages) == 4

    messages.extend([_assistant_tool("c4", "read_file"), _tool_result("c4", "read_file")])
    assert mod._agent_flow_stage(messages) == 5

    messages.extend([_assistant_tool("c5", "todo_modify"), _tool_result("c5", "todo_modify")])
    assert mod._agent_flow_stage(messages) == 6

    messages.extend([_assistant_tool("c6", "todo_modify"), _tool_result("c6", "todo_modify")])
    assert mod._agent_flow_stage(messages) == 7

    messages.extend(
        [_assistant_tool("c7", "send_file_to_user"), _tool_result("c7", "send_file_to_user")]
    )
    assert mod._agent_flow_stage(messages) == 8


def test_extra_tool_does_not_skip_send_file_stage():
    mod = _load_module()
    messages = [
        _assistant_tool("c1", "todo_create"),
        _tool_result("c1", "todo_create"),
        {"role": "assistant", "content": "opening"},
        _assistant_tool("c2", "bash"),
        _tool_result("c2", "bash"),
        _assistant_tool("cX", "edit_file"),
        _tool_result("cX", "edit_file", "noise"),
        _assistant_tool("c3", "write_file"),
        _tool_result("c3", "write_file"),
        _assistant_tool("c4", "read_file"),
        _tool_result("c4", "read_file"),
        _assistant_tool("c5", "todo_modify"),
        _tool_result("c5", "todo_modify"),
        _assistant_tool("c6", "todo_modify"),
        _tool_result("c6", "todo_modify"),
    ]
    # 旧逻辑按 tool 条数会跳过 send_file；按工具名仍应停在 stage 7
    assert mod._agent_flow_stage(messages) == 7


def test_send_file_result_forces_stage_8_even_with_trailing_noise():
    mod = _load_module()
    messages = [
        _assistant_tool("c1", "todo_create"),
        _tool_result("c1"),  # 仅靠 tool_call_id 解析 name
        _assistant_tool("c7", "send_file_to_user"),
        _tool_result("c7"),
        _assistant_tool("cX", "edit_file"),
        _tool_result("cX", "edit_file"),
    ]
    assert mod._agent_flow_stage(messages) == 8


def test_plan_after_send_file_is_final_text():
    mod = _load_module()
    payload = {
        "messages": [
            _assistant_tool("c7", "send_file_to_user"),
            _tool_result("c7", "send_file_to_user"),
        ]
    }
    plan = mod._plan_agent_flow_response(payload, novel_chars=800, excerpt_chars=200)
    assert plan.kind == "stream_text"
    assert plan.text == mod._NOVEL_FINAL_MESSAGE
