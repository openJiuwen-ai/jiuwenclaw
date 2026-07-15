# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""deepresearch_stream router 纯函数单测。"""
import json

from jiuwenclaw.agentserver.tools.deepresearch_stream_router import (
    RouterState,
    build_interrupt_prompt,
    collected_questions,
    route_chunk,
)


def test_first_seen_node_emits_task_start():
    state = RouterState()
    chunk = {"agent": "outline", "event": "", "content": "大纲..."}
    frames = route_chunk(chunk, state)
    assert frames[0] == {"event_type": "chat.reasoning", "content": "大纲..."}
    assert frames[1]["event_type"] == "task.start"
    assert frames[1]["node_name"] == "outline"
    assert frames[1]["task_id"] == "dr_outline"
    assert state.active_nodes["outline"]["started"] is True


def test_same_node_second_chunk_no_duplicate_start():
    state = RouterState()
    route_chunk({"agent": "outline", "content": "a"}, state)
    frames = route_chunk({"agent": "outline", "content": "b"}, state)
    assert frames == [{"event_type": "chat.reasoning", "content": "b"}]


def test_event_done_emits_task_complete():
    state = RouterState()
    route_chunk({"agent": "outline", "content": "a"}, state)
    frames = route_chunk({"agent": "outline", "event": "done"}, state)
    assert any(f["event_type"] == "task.complete" for f in frames)


def test_parallel_section_nodes_emit_reasoning_instead_of_task_frames():
    expected = {
        "plan_reasoning": "规划调研",
        "collector_query_generation": "生成检索词",
        "collector_info_retrieval": "资料检索",
        "collector_supervisor": "采集评估",
        "collector_summary": "资料汇总",
        "sub_reporter": "章节撰写",
    }

    for agent, display_name in expected.items():
        state = RouterState()
        started = route_chunk(
            {
                "agent": agent,
                "section_idx": "3",
                "section_title": "真实章节标题",
                "event": "start",
                "content": "",
            },
            state,
        )
        completed = route_chunk(
            {"agent": agent, "section_idx": "3", "event": "done", "content": ""},
            state,
        )

        assert started == [{
            "event_type": "chat.reasoning",
            "task_id": "deepresearch_section_3",
            "task_content": "真实章节标题",
            "task_index": 3,
            "content": f"{display_name}开始\n",
        }]
        assert completed == [{
            "event_type": "chat.reasoning",
            "task_id": "deepresearch_section_3",
            "task_content": "真实章节标题",
            "task_index": 3,
            "content": f"{display_name}完成\n",
        }]


def test_parallel_section_reasoning_preserves_known_title_when_later_chunk_omits_it():
    state = RouterState()
    route_chunk(
        {
            "agent": "plan_reasoning",
            "section_idx": "1",
            "section_title": "真实标题",
            "section_total": 1,
            "event": "start",
        },
        state,
    )
    frames = route_chunk(
        {"agent": "collector_info_retrieval", "section_idx": "1", "event": "start"},
        state,
    )

    assert frames == [{
        "event_type": "chat.reasoning",
        "task_id": "deepresearch_section_1",
        "task_content": "真实标题",
        "task_index": 1,
        "content": "资料检索开始\n",
    }]


def test_parallel_section_reasoning_keeps_authoritative_outline_title():
    state = RouterState(section_titles={"2": "核心架构设计与检索增强能力深度对比"})
    frames = route_chunk(
        {
            "agent": "plan_reasoning",
            "section_idx": "2",
            "section_total": 3,
            "event": "message",
            "content": json.dumps({
                "title": "检索策略与索引能力信息采集",
                "thought": "完整规划过程",
            }, ensure_ascii=False),
        },
        state,
    )

    assert all(frame["task_content"] == "核心架构设计与检索增强能力深度对比" for frame in frames)
    assert state.section_titles["2"] == "核心架构设计与检索增强能力深度对比"


def test_plan_reasoning_message_emits_complete_original_json():
    state = RouterState()
    frames = route_chunk(
        {
            "agent": "plan_reasoning",
            "section_idx": "1",
            "section_title": "第一章",
            "section_total": 1,
            "event": "message",
            "content": {
                "title": "梳理主流记忆框架的分类、架构与数据流",
                "thought": "这段详细推理必须完整进入思考过程。",
            },
        },
        state,
    )

    assert frames == [
        {
            "event_type": "chat.reasoning",
            "task_id": "deepresearch_section_1",
            "task_content": "第一章",
            "task_index": 1,
            "total_tasks": 1,
            "content": "规划调研开始\n",
        },
        {
            "event_type": "chat.reasoning",
            "task_id": "deepresearch_section_1",
            "task_content": "第一章",
            "task_index": 1,
            "total_tasks": 1,
            "content": json.dumps(
                {
                    "title": "梳理主流记忆框架的分类、架构与数据流",
                    "thought": "这段详细推理必须完整进入思考过程。",
                },
                ensure_ascii=False,
            ),
        },
    ]


def test_collector_summary_response_preserves_long_text_and_line_breaks():
    state = RouterState()
    original = (
        "本轮已确认 Mem0、MemOS 与 Zep 的核心架构差异。\n\n"
        "证据覆盖长期记忆、图记忆和部署方式，后续将补充基准测试数据。"
        + "更多原始过程。" * 40
    )
    frames = route_chunk(
        {
            "agent": "collector_summary",
            "section_idx": "1",
            "section_title": "第一章",
            "section_total": 1,
            "event": "summary_response",
            "content": original,
        },
        state,
    )

    assert frames == [
        {
            "event_type": "chat.reasoning",
            "task_id": "deepresearch_section_1",
            "task_content": "第一章",
            "task_index": 1,
            "total_tasks": 1,
            "content": "资料汇总开始\n",
        },
        {
            "event_type": "chat.reasoning",
            "task_id": "deepresearch_section_1",
            "task_content": "第一章",
            "task_index": 1,
            "total_tasks": 1,
            "content": original,
        },
    ]
    assert "\n\n" in frames[1]["content"]
    assert len(frames[1]["content"]) > 120


def test_parallel_sections_keep_interleaved_process_in_explicit_section_tasks():
    state = RouterState()
    first = route_chunk({
        "agent": "collector_info_retrieval",
        "section_idx": "1",
        "section_title": "第一章",
        "event": "message",
        "content": "第一章原始检索过程",
    }, state)
    second = route_chunk({
        "agent": "collector_info_retrieval",
        "section_idx": "2",
        "section_title": "第二章",
        "event": "message",
        "content": "第二章原始检索过程",
    }, state)
    third = route_chunk({
        "agent": "collector_info_retrieval",
        "section_idx": "1",
        "event": "message",
        "content": "第一章后续过程",
    }, state)

    assert first[-1]["task_id"] == "deepresearch_section_1"
    assert first[-1]["task_content"] == "第一章"
    assert second[-1]["task_id"] == "deepresearch_section_2"
    assert second[-1]["task_content"] == "第二章"
    assert third == [{
        "event_type": "chat.reasoning",
        "task_id": "deepresearch_section_1",
        "task_content": "第一章",
        "task_index": 1,
        "content": "第一章后续过程",
    }]


def test_parallel_section_emits_distinct_reasoning_and_content_without_compaction():
    state = RouterState()
    frames = route_chunk({
        "agent": "collector_supervisor",
        "section_idx": "2",
        "section_title": "第二章",
        "event": "message",
        "reasoning_content": "原始推理\n第二行",
        "content": ["证据 A", {"source": "原始来源"}],
    }, state)

    assert [frame["content"] for frame in frames] == [
        "采集评估开始\n",
        "原始推理\n第二行",
        json.dumps(["证据 A", {"source": "原始来源"}], ensure_ascii=False),
    ]
    assert all(frame["task_id"] == "deepresearch_section_2" for frame in frames)


def test_parallel_section_filters_control_process_values():
    for value in ("SUCCESS", " ALL END ", "SECTION END", "", "   "):
        state = RouterState()
        route_chunk({
            "agent": "sub_reporter",
            "section_idx": "1",
            "section_title": "第一章",
            "event": "start",
        }, state)
        frames = route_chunk({
            "agent": "sub_reporter",
            "section_idx": "1",
            "event": "summary_response",
            "content": value,
        }, state)
        assert frames == []


def test_sub_reporter_success_response_does_not_restart_reasoning():
    state = RouterState()
    base = {"section_idx": "1", "section_title": "第一章", "section_total": 1}
    route_chunk(
        {
            **base,
            "agent": "collector_summary",
            "event": "summary_response",
            "content": "已确认三个框架的关键架构差异。",
        },
        state,
    )
    route_chunk({**base, "agent": "sub_reporter", "event": "start"}, state)
    route_chunk({**base, "agent": "sub_reporter", "event": "done"}, state)
    frames = route_chunk(
        {
            **base,
            "agent": "sub_reporter",
            "event": "summary_response",
            "content": "SUCCESS",
        },
        state,
    )

    assert frames == []


def test_interrupt_chunk_not_forwarded():
    state = RouterState()
    frames = route_chunk({"agent": "outline_interaction", "message_type": "interrupt"}, state)
    assert frames == []


def test_status_marker_skipped():
    state = RouterState()
    frames = route_chunk({"__deepsearch_status__": "started", "conversation_id": "X"}, state)
    assert frames == []


def test_skip_node_no_frame():
    state = RouterState()
    assert route_chunk({"agent": "start"}, state) == []
    assert route_chunk({"agent": "end"}, state) == []


def test_unknown_node_no_frame():
    state = RouterState()
    assert route_chunk({"agent": "mystery_node", "content": "x"}, state) == []


def test_reasoning_content_emits_chat_reasoning():
    state = RouterState()
    frames = route_chunk({"agent": "outline", "reasoning_content": "thinking..."}, state)
    assert any(f["event_type"] == "chat.reasoning" and f["content"] == "thinking..." for f in frames)


def test_outline_content_emits_chat_reasoning_for_readonly_thinking_display():
    state = RouterState()
    frames = route_chunk({
        "agent": "outline",
        "event": "message",
        "content": "# 研究大纲\n\n1. 背景\n2. 方案对比",
    }, state)

    assert frames[0] == {
        "event_type": "chat.reasoning",
        "content": "# 研究大纲\n\n1. 背景\n2. 方案对比",
    }


def test_question_chunks_are_aggregated_by_message_id_in_first_seen_order():
    state = RouterState()
    route_chunk({
        "agent": "question_generator",
        "message_type": "message_chunk",
        "message_id": "q1",
        "content": "1. 关注哪些市场",
    }, state)
    route_chunk({
        "agent": "question_generator",
        "message_type": "message_chunk",
        "message_id": "q2",
        "content": "2. 使用什么时间范围",
    }, state)
    route_chunk({
        "agent": "question_generator",
        "message_type": "message_chunk",
        "message_id": "q1",
        "content": "？\n",
    }, state)

    assert collected_questions(state) == "1. 关注哪些市场？\n2. 使用什么时间范围"


def test_question_cache_ignores_unrelated_chunks():
    state = RouterState()
    route_chunk({
        "agent": "info_collector",
        "message_type": "message_chunk",
        "message_id": "noise",
        "content": "检索过程",
    }, state)
    route_chunk({
        "agent": "question_generator",
        "message_type": "summary_response",
        "message_id": "noise-2",
        "content": "不是问题碎片",
    }, state)

    assert collected_questions(state) == ""


def test_section_node_uses_keyed_state():
    state = RouterState()
    route_chunk({"agent": "sub_reporter", "section_idx": "1", "content": "a"}, state)
    route_chunk({"agent": "sub_reporter", "section_idx": "2", "content": "b"}, state)
    # 两个 section 独立,各发一次 task.start
    assert len([k for k in state.active_nodes if k.startswith("1:") or k.startswith("2:")]) == 2


def test_report_content_accumulated():
    # reporter 和 outline 都需要在 terminal marker 缺字段时提供交互兜底。
    state = RouterState()
    route_chunk({"agent": "reporter", "content": "第一章正文\n"}, state)
    route_chunk({"agent": "reporter", "content": "第二章正文\n"}, state)
    route_chunk({"agent": "generate_questions", "content": "问题A"}, state)
    route_chunk({"agent": "outline", "content": "大纲..."}, state)
    assert "".join(state.report_parts) == "第一章正文\n第二章正文\n"
    assert "".join(state.outline_parts) == "大纲..."
    assert not hasattr(state, "questions_parts")  # 累积器已移除


def test_interrupt_chunk_captures_node_and_prompt():
    state = RouterState()
    route_chunk(
        {
            "agent": "outline_interaction",
            "message_type": "interrupt",
            "content": "大纲已生成,请确认",
            "conversation_id": "C1",
        },
        state,
    )
    assert state.interrupt_node_id == "outline_interaction"
    assert state.interrupt_raw_prompt == "大纲已生成,请确认"
    assert state.interrupt_conversation_id == "C1"


def test_build_interrupt_prompt_outline_from_marker_content():
    # (1) §Stage3(b) 读 marker.content(JSON 解析为 OutlineContent);marker 有 content → 用它
    state = RouterState()
    marker = {"content": "第一章 来自marker\n第二章 来自marker", "agent": "outline_interaction"}
    prompt = build_interrupt_prompt("outline_interaction", state, marker, "query")
    assert "来自marker" in prompt


def test_build_interrupt_prompt_outline_marker_empty_uses_accumulated_outline():
    # 实际 SDK 的 interrupt chunk 只含审批提示；大纲正文来自此前 outline chunk。
    state = RouterState()
    route_chunk({"agent": "outline", "content": "第一章 累积大纲"}, state)
    route_chunk(
        {"agent": "outline_interaction", "message_type": "interrupt", "content": "请审批大纲"},
        state,
    )
    prompt = build_interrupt_prompt(state.interrupt_node_id, state, {}, "研究主题X")
    assert "第一章 累积大纲" in prompt
    assert "请审批大纲" in prompt


def test_build_interrupt_prompt_outline_status_placeholder_uses_accumulated_outline():
    state = RouterState()
    route_chunk({"agent": "outline", "content": "# 第一章\n累积大纲正文"}, state)

    prompt = build_interrupt_prompt(
        "outline_interaction",
        state,
        {"content": "Round 1: waiting for user feedback."},
        "研究主题X",
    )

    assert "累积大纲正文" in prompt
    assert "Round 1: waiting for user feedback." not in prompt


def test_build_interrupt_prompt_feedback_falls_back_to_query():
    state = RouterState()  # marker 无透传,interrupt chunk 也无内容
    route_chunk(
        {"agent": "feedback_handler", "message_type": "interrupt", "content": ""}, state
    )
    prompt = build_interrupt_prompt(state.interrupt_node_id, state, {}, "研究主题X")
    assert "研究主题X" in prompt


def test_build_interrupt_prompt_feedback_prefers_marker_prompt():
    state = RouterState()
    marker = {"prompt": "研究进行中,请输入反馈意见：", "agent": "feedback_handler"}
    prompt = build_interrupt_prompt("feedback_handler", state, marker, "研究主题X")
    assert "研究进行中" in prompt
    assert "研究主题X" not in prompt  # marker.prompt 优先,不退到 query fallback


def test_build_interrupt_prompt_ufp_truncates_report():
    # report 不在 marker,必须累积
    state = RouterState()
    route_chunk({"agent": "reporter", "content": "R" * 7000}, state)
    route_chunk(
        {
            "agent": "user_feedback_processor",
            "message_type": "interrupt",
            "content": "请选择后续操作",
        },
        state,
    )
    prompt = build_interrupt_prompt(state.interrupt_node_id, state, {}, "q")
    assert len(prompt) < 6200  # 截断 6000 + 占位
    assert "完整报告见最终产物" in prompt
