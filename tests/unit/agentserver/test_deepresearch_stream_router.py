# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""deepresearch_stream router 纯函数单测。"""
import json

from jiuwenclaw.agentserver.tools.deepresearch.stream_router import (
    RouterState,
    build_interrupt_prompt,
    collected_questions,
    route_chunk,
)


STAGE_TITLES = [
    "研究主题澄清",
    "大纲生成与确认",
    "并行调研与章节撰写",
    "报告整合",
    "引用溯源与校验",
    "报告交付",
]


def _stage_update(frames):
    return next((frame for frame in frames if frame["event_type"] == "task.update"), None)


def _assert_stage(update, active_stage):
    assert update is not None
    assert [task["task_id"] for task in update["tasks"]] == [
        f"deepresearch_stage_{index}" for index in range(1, 7)
    ]
    assert [task["task_content"] for task in update["tasks"]] == STAGE_TITLES
    expected = [
        "completed" if index < active_stage
        else "in_progress" if index == active_stage
        else "pending"
        for index in range(1, 7)
    ]
    assert [task["status"] for task in update["tasks"]] == expected
    assert update["total_tasks"] == 6
    assert update["completed_tasks"] == active_stage - 1
    assert update["in_progress_tasks"] == 1
    assert update["pending_tasks"] == 6 - active_stage


def test_workflow_nodes_advance_six_stage_snapshot():
    state = RouterState()

    for agent, stage in (
        ("intent_recognition", 1),
        ("outline", 2),
        ("editor_team", 3),
        ("reporter", 4),
        ("source_tracer", 5),
    ):
        _assert_stage(_stage_update(route_chunk({"agent": agent}, state)), stage)


def test_interrupt_nodes_advance_stage_before_raw_chunk_is_suppressed():
    feedback = route_chunk(
        {"agent": "feedback_handler", "message_type": "interrupt"},
        RouterState(),
    )
    outline = route_chunk(
        {"agent": "outline_interaction", "message_type": "interrupt"},
        RouterState(),
    )

    _assert_stage(_stage_update(feedback), 1)
    _assert_stage(_stage_update(outline), 2)


def test_stage_snapshot_never_regresses_on_late_earlier_node():
    state = RouterState()
    route_chunk({"agent": "source_tracer"}, state)

    frames = route_chunk({"agent": "outline", "content": "迟到的大纲事件"}, state)

    assert _stage_update(frames) is None


def test_first_seen_node_emits_task_start():
    state = RouterState()
    chunk = {"agent": "intent_recognition", "event": "", "content": "分析..."}
    frames = route_chunk(chunk, state)
    task_start = next(frame for frame in frames if frame["event_type"] == "task.start")
    assert task_start["node_name"] == "intent_recognition"
    assert task_start["task_id"] == "dr_intent_recognition"
    assert state.active_nodes["intent_recognition"]["started"] is True


def test_outline_reasoning_is_nested_under_stage_two():
    frames = route_chunk(
        {"agent": "outline", "event": "start", "content": "大纲正文"},
        RouterState(),
    )

    assert [
        frame for frame in frames if frame["event_type"] == "chat.reasoning"
    ] == [{
        "event_type": "chat.reasoning",
        "task_id": "deepresearch_stage_2",
        "task_content": "大纲生成 - 规划报告章节结构",
        "stream_source_id": "dr_outline",
        "content": "大纲正文",
    }]
    assert not any(
        frame["event_type"] == "task.start" and frame.get("task_id") == "dr_outline"
        for frame in frames
    )


def test_same_node_second_chunk_no_duplicate_start():
    state = RouterState()
    route_chunk({"agent": "intent_recognition", "content": "a"}, state)
    frames = route_chunk({"agent": "intent_recognition", "content": "b"}, state)
    assert frames == []


def test_event_done_emits_task_complete():
    state = RouterState()
    route_chunk({"agent": "intent_recognition", "content": "a"}, state)
    frames = route_chunk({"agent": "intent_recognition", "event": "done"}, state)
    assert any(f["event_type"] == "task.complete" for f in frames)


def test_parallel_section_nodes_emit_nested_reasoning_without_node_task_frames():
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

        started_reasoning = [
            frame for frame in started if frame["event_type"] == "chat.reasoning"
        ]
        assert started_reasoning == [{
            "event_type": "chat.reasoning",
            "task_id": "deepresearch_stage_3",
            "task_content": "真实章节标题",
            "task_index": 3,
            "stream_source_id": "deepresearch_section_3",
            "content": f"{display_name}开始\n",
        }]
        completed_reasoning = [
            frame for frame in completed if frame["event_type"] == "chat.reasoning"
        ]
        assert completed_reasoning == [{
            "event_type": "chat.reasoning",
            "task_id": "deepresearch_stage_3",
            "task_content": "真实章节标题",
            "task_index": 3,
            "stream_source_id": "deepresearch_section_3",
            "content": f"{display_name}完成\n",
        }]
        _assert_stage(_stage_update(started), 3)
        assert not any(
            frame["event_type"] == "task.start"
            and frame["task_id"] != "deepresearch_stage_3"
            for frame in started + completed
        )


def test_parallel_sections_use_explicit_stage_three_parent_without_boundaries():
    state = RouterState()

    section_frames = route_chunk(
        {
            "agent": "plan_reasoning",
            "section_idx": "1",
            "section_title": "真实章节标题",
            "event": "start",
        },
        state,
    )
    reporter_frames = route_chunk({"agent": "reporter", "event": "start"}, state)

    assert not any(frame["event_type"] in {"task.start", "task.complete"} for frame in section_frames)
    section_reasoning = [frame for frame in section_frames if frame["event_type"] == "chat.reasoning"]
    assert all(frame["task_id"] == "deepresearch_stage_3" for frame in section_reasoning)
    assert all(frame["stream_source_id"] == "deepresearch_section_1" for frame in section_reasoning)
    assert reporter_frames[0]["event_type"] == "task.update"
    assert not any(frame["event_type"] in {"task.start", "task.complete"} for frame in reporter_frames)


def test_stage_internal_nodes_use_explicit_parent_without_task_boundaries():
    frames = route_chunk(
        {"agent": "reporter", "event": "start", "reasoning_content": "整合报告"},
        RouterState(),
    )

    reasoning = [frame for frame in frames if frame["event_type"] == "chat.reasoning"]
    assert [frame["content"] for frame in reasoning] == ["报告整合开始\n", "整合报告"]
    assert all(frame["task_id"] == "deepresearch_stage_4" for frame in reasoning)
    assert all(frame["task_content"] == "报告整合 - 整合最终报告" for frame in reasoning)
    assert all(frame["stream_source_id"] == "dr_reporter" for frame in reasoning)
    assert not any(frame["event_type"].startswith("task.") and frame["event_type"] != "task.update" for frame in frames)


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
        "task_id": "deepresearch_stage_3",
        "task_content": "真实标题",
        "task_index": 1,
        "stream_source_id": "deepresearch_section_1",
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

    reasoning = [frame for frame in frames if frame["event_type"] == "chat.reasoning"]
    _assert_stage(_stage_update(frames), 3)
    assert all(frame["task_content"] == "核心架构设计与检索增强能力深度对比" for frame in reasoning)
    assert all(frame["stream_source_id"] == "deepresearch_section_2" for frame in reasoning)
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

    reasoning = [frame for frame in frames if frame["event_type"] == "chat.reasoning"]
    assert reasoning == [
        {
            "event_type": "chat.reasoning",
            "task_id": "deepresearch_stage_3",
            "task_content": "第一章",
            "task_index": 1,
            "total_tasks": 1,
            "stream_source_id": "deepresearch_section_1",
            "content": "规划调研开始\n",
        },
        {
            "event_type": "chat.reasoning",
            "task_id": "deepresearch_stage_3",
            "task_content": "第一章",
            "task_index": 1,
            "total_tasks": 1,
            "stream_source_id": "deepresearch_section_1",
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

    reasoning = [frame for frame in frames if frame["event_type"] == "chat.reasoning"]
    assert reasoning == [
        {
            "event_type": "chat.reasoning",
            "task_id": "deepresearch_stage_3",
            "task_content": "第一章",
            "task_index": 1,
            "total_tasks": 1,
            "stream_source_id": "deepresearch_section_1",
            "content": "资料汇总开始\n",
        },
        {
            "event_type": "chat.reasoning",
            "task_id": "deepresearch_stage_3",
            "task_content": "第一章",
            "task_index": 1,
            "total_tasks": 1,
            "stream_source_id": "deepresearch_section_1",
            "content": original,
        },
    ]
    assert "\n\n" in reasoning[1]["content"]
    assert len(reasoning[1]["content"]) > 120


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

    assert first[-1]["task_id"] == "deepresearch_stage_3"
    assert first[-1]["task_content"] == "第一章"
    assert second[-1]["task_id"] == "deepresearch_stage_3"
    assert second[-1]["task_content"] == "第二章"
    assert third == [{
        "event_type": "chat.reasoning",
        "task_id": "deepresearch_stage_3",
        "task_content": "第一章",
        "task_index": 1,
        "stream_source_id": "deepresearch_section_1",
        "content": "第一章后续过程",
    }]


def test_parallel_sections_emit_one_stage_update_without_chapter_snapshots():
    state = RouterState(section_titles={"1": "第一章", "2": "第二章"})

    started = route_chunk({
        "agent": "collector_info_retrieval",
        "section_idx": "1",
        "section_total": 2,
        "event": "start",
        "content": "第一章检索过程",
    }, state)
    completed = route_chunk({
        "agent": "sub_reporter",
        "section_idx": "1",
        "section_total": 2,
        "event": "done",
        "content": "SUCCESS",
    }, state)
    repeated_success = route_chunk({
        "agent": "sub_reporter",
        "section_idx": "1",
        "section_total": 2,
        "event": "summary_response",
        "content": "SUCCESS",
    }, state)

    _assert_stage(_stage_update(started), 3)
    assert _stage_update(completed) is None
    assert all(
        frame["stream_source_id"] == "deepresearch_section_1"
        for frame in started + completed
        if frame["event_type"] == "chat.reasoning"
    )
    assert repeated_success == []


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

    reasoning = [frame for frame in frames if frame["event_type"] == "chat.reasoning"]
    assert [frame["content"] for frame in reasoning] == [
        "采集评估开始\n",
        "原始推理\n第二行",
        json.dumps(["证据 A", {"source": "原始来源"}], ensure_ascii=False),
    ]
    assert all(frame["task_id"] == "deepresearch_stage_3" for frame in reasoning)


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


def test_sub_reporter_forwards_reasoning_without_streaming_chapter_body():
    state = RouterState()
    frames = route_chunk(
        {
            "agent": "sub_reporter",
            "section_idx": "1",
            "section_title": "第一章",
            "section_total": 1,
            "event": "message",
            "reasoning_content": "正在组织章节结构",
            "content": "# 第一章\n\n这是完整章节正文。",
        },
        state,
    )

    reasoning = [frame for frame in frames if frame["event_type"] == "chat.reasoning"]
    assert [frame["content"] for frame in reasoning] == [
        "章节撰写开始\n",
        "正在组织章节结构",
    ]


def test_interrupt_chunk_not_forwarded():
    state = RouterState()
    frames = route_chunk({"agent": "outline_interaction", "message_type": "interrupt"}, state)
    _assert_stage(_stage_update(frames), 2)
    assert len(frames) == 1


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

    reasoning = [frame for frame in frames if frame["event_type"] == "chat.reasoning"]
    assert reasoning[0] == {
        "event_type": "chat.reasoning",
        "task_id": "deepresearch_stage_2",
        "task_content": "大纲生成 - 规划报告章节结构",
        "stream_source_id": "dr_outline",
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
