# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Outbound stream: outer-tool_result resets streamed-content flag; task_id is forwarded."""

from types import SimpleNamespace

from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
    JiuWenSwarmDeepAdapter,
    _is_outer_react_tool_result,
    _propagate_stream_source_id,
)


def _answer_chunk(text: str = "以下是完成情况概要") -> SimpleNamespace:
    return SimpleNamespace(
        type="answer",
        payload={"output": {"output": text, "chunked": False}},
    )


def test_outer_skill_acceleration_tool_result_is_outer() -> None:
    assert _is_outer_react_tool_result(
        {
            "tool_result": {
                "tool_name": "skill_acceleration_exec",
                "tool_call_id": "call_outer_1",
                "result": "ok",
            }
        }
    )


def test_inner_skill_turbo_tool_id_is_not_outer() -> None:
    assert not _is_outer_react_tool_result(
        {
            "tool_call_id": "BashTool_skill_turbo",
            "result": "ok",
        }
    )


def test_inner_stream_source_id_is_not_outer() -> None:
    assert not _is_outer_react_tool_result(
        {
            "stream_source_id": "p6_1_page_worker",
            "tool_call_id": "call_page_1",
        }
    )


def test_nested_tool_result_skill_turbo_suffix_is_not_outer() -> None:
    assert not _is_outer_react_tool_result(
        {
            "tool_result": {
                "tool_id": "ReadFileTool_skill_turbo",
                "result": "{}",
            }
        }
    )


def test_empty_payload_counts_as_outer() -> None:
    assert _is_outer_react_tool_result({})
    assert _is_outer_react_tool_result(None)


def test_propagate_copies_task_id_and_stream_source_id() -> None:
    result = {"event_type": "chat.delta", "content": "完成执行 Stage 5: 模板上下文预处理（5/14）"}
    out = _propagate_stream_source_id(
        {
            "content": result["content"],
            "task_id": "task_abc123",
            "stream_source_id": "p6_1_page_worker",
        },
        result,
    )
    assert out["task_id"] == "task_abc123"
    assert out["stream_source_id"] == "p6_1_page_worker"


def test_propagate_skips_blank_task_id() -> None:
    result = {"event_type": "chat.delta", "content": "hello"}
    out = _propagate_stream_source_id({"task_id": "  "}, result)
    assert "task_id" not in out


def test_parse_llm_output_forwards_task_id() -> None:
    chunk = SimpleNamespace(
        type="llm_output",
        payload={
            "content": "完成执行 Stage 5: 模板上下文预处理（5/14）",
            "task_id": "task_abc123",
        },
    )
    parsed = JiuWenSwarmDeepAdapter._parse_stream_chunk(chunk)
    assert parsed["event_type"] == "chat.delta"
    assert parsed["content"] == "完成执行 Stage 5: 模板上下文预处理（5/14）"
    assert parsed["task_id"] == "task_abc123"


def test_parse_llm_reasoning_forwards_task_id() -> None:
    chunk = SimpleNamespace(
        type="llm_reasoning",
        payload={"content": "内部推理", "task_id": "task_think"},
    )
    parsed = JiuWenSwarmDeepAdapter._parse_stream_chunk(chunk)
    assert parsed["event_type"] == "chat.reasoning"
    assert parsed["task_id"] == "task_think"


def test_parse_llm_output_without_task_id_unchanged() -> None:
    chunk = SimpleNamespace(type="llm_output", payload={"content": "hello"})
    parsed = JiuWenSwarmDeepAdapter._parse_stream_chunk(chunk)
    assert parsed == {"event_type": "chat.delta", "content": "hello"}


def test_parse_content_chunk_forwards_task_id() -> None:
    chunk = SimpleNamespace(
        type="content_chunk",
        payload={"content": "开始执行 Stage 6: 内容策划（6/14）", "task_id": "task_plan"},
    )
    parsed = JiuWenSwarmDeepAdapter._parse_stream_chunk(chunk)
    assert parsed["event_type"] == "chat.delta"
    assert parsed["task_id"] == "task_plan"


def test_parse_unknown_chat_delta_payload_forwards_task_id() -> None:
    chunk = SimpleNamespace(
        type="node.progress",
        payload={
            "event_type": "chat.delta",
            "content": "完成执行 Stage 8: 深度研究（8/14）",
            "task_id": "task_research",
        },
    )
    parsed = JiuWenSwarmDeepAdapter._parse_stream_chunk(chunk)
    assert parsed["event_type"] == "chat.delta"
    assert parsed["task_id"] == "task_research"


def test_same_round_streamed_answer_still_empty_final() -> None:
    answer = "以下是完成情况概要"
    parsed = JiuWenSwarmDeepAdapter._parse_stream_chunk(
        _answer_chunk(answer),
        _has_streamed_content=True,
        _streamed_text=answer,
    )
    assert parsed == {"event_type": "chat.final", "content": ""}


def test_streamed_flag_without_visible_text_keeps_final_for_drain() -> None:
    answer = "以下是完成情况概要"
    parsed = JiuWenSwarmDeepAdapter._parse_stream_chunk(
        _answer_chunk(answer),
        _has_streamed_content=True,
        _streamed_text="",
    )
    assert parsed == {"event_type": "chat.final", "content": answer}


def test_after_outer_tool_result_reset_answer_keeps_summary() -> None:
    has_streamed_content = True
    if _is_outer_react_tool_result(
        {"tool_result": {"tool_name": "skill_acceleration_exec", "tool_call_id": "call_1"}}
    ):
        has_streamed_content = False
    parsed = JiuWenSwarmDeepAdapter._parse_stream_chunk(
        _answer_chunk(),
        _has_streamed_content=has_streamed_content,
    )
    assert parsed == {
        "event_type": "chat.final",
        "content": "以下是完成情况概要",
    }


def test_after_inner_tool_result_answer_stays_empty_final() -> None:
    answer = "以下是完成情况概要"
    has_streamed_content = True
    if _is_outer_react_tool_result({"tool_call_id": "BashTool_skill_turbo"}):
        has_streamed_content = False
    parsed = JiuWenSwarmDeepAdapter._parse_stream_chunk(
        _answer_chunk(answer),
        _has_streamed_content=has_streamed_content,
        _streamed_text=answer,
    )
    assert parsed == {"event_type": "chat.final", "content": ""}
