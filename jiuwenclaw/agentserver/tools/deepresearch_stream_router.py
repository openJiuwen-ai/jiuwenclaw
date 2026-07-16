# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""deepresearch_stream tool 的 chunk → RelayClawEventType frame 路由。

纯函数,无副作用:输入一条 deepsearch chunk(JSON dict)+ RouterState,
输出 0~N 个 send_push payload(event_type + task_id + task_content + ...)。
调用方(deepresearch_stream tool)负责把 payload 包成 msg 后 send_push。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

# 节点中文显示名。与 DeepResearchTaskManager.NODE_DISPLAY_INFO 保持一致
# (直接 import 类属性会触发 manager 模块重初始化,这里显式复制常量)。
NODE_DISPLAY_INFO: dict[str, tuple[str, str]] = {
    "intent_recognition": ("意图识别", "分析研究需求"),
    "question_generator": ("问题生成", "规划搜索问题"),
    "info_collector": ("信息收集", "检索网页信息"),
    "understanding_analyzer": ("理解分析", "综合分析检索结果"),
    "outline": ("大纲生成", "规划报告章节结构"),
    "plan_reasoning": ("规划调研", "为当前章节制定分步信息采集计划"),
    "collector_query_generation": ("生成检索词", "为当前章节生成搜索查询"),
    "collector_info_retrieval": ("资料检索", "并行检索网页和资料"),
    "collector_supervisor": ("采集评估", "判断当前资料是否充分"),
    "collector_summary": ("资料汇总", "整理本轮采集结果"),
    "sub_reporter": ("章节撰写", "生成章节内容"),
    "editor_team": ("报告撰写", "生成报告内容"),
    "reporter": ("报告整合", "整合最终报告"),
    "source_tracer": ("溯源校验", "核查报告事实陈述"),
    "source_tracer_infer": ("溯源推理", "推理校验事实链路"),
}

# 低价值节点,不推进度(与 manager _SKIP_PROGRESS_NODES 一致)
_SKIP_NODES = {"start", "end", "framework", "entry", "outline_interaction"}

# 交互内容节点 → 累积到哪个 parts 列表。实际 SDK 的 outline interrupt
# 只携带审批提示，因此必须保留此前 outline 节点输出作为卡片正文。
_INTERACTION_NODES: dict[str, str] = {
    "outline": "outline_parts",
    "reporter": "report_parts",
}

_SECTION_PROCESS_NODES = {
    "plan_reasoning",
    "collector_query_generation",
    "collector_info_retrieval",
    "collector_supervisor",
    "collector_summary",
    "sub_reporter",
}

_CONTROL_PROCESS_VALUES = {"SUCCESS", "ALL END", "SECTION END"}
_QUESTION_NODES = {"question_generator", "generate_questions"}

DEEPRESEARCH_STAGES: tuple[str, ...] = (
    "研究主题澄清",
    "大纲生成与确认",
    "并行调研与章节撰写",
    "报告整合",
    "引用溯源与校验",
    "报告交付",
)

_NODE_STAGE: dict[str, int] = {
    "intent_recognition": 1,
    "question_generator": 1,
    "generate_questions": 1,
    "feedback_handler": 1,
    "outline": 2,
    "outline_interaction": 2,
    "editor_team": 3,
    "reporter": 4,
    "vlm_chart_generator": 4,
    "source_tracer": 5,
    "source_tracer_infer": 5,
}


@dataclass
class RouterState:
    """路由器跨 chunk 状态:追踪每个节点的 start/done + 累积 report + 捕获中断信息。"""

    active_nodes: dict[str, dict] = field(default_factory=dict)
    # report 累积(仅 reporter;report 不在 marker key 列表,必须累积)
    report_parts: list[str] = field(default_factory=list)
    # outline_interaction 的 interrupt marker 可能不带大纲正文。
    outline_parts: list[str] = field(default_factory=list)
    # 中断 chunk 捕获(interrupt chunk 先于 interrupted marker 到达)
    interrupt_node_id: str = ""
    interrupt_raw_prompt: str = ""
    interrupt_conversation_id: str = ""
    section_titles: dict[str, str] = field(default_factory=dict)
    authoritative_section_indices: set[str] = field(default_factory=set)
    question_parts: dict[str, list[str]] = field(default_factory=dict)
    question_order: list[str] = field(default_factory=list)
    current_stage: int = 0
    stages_completed: bool = False
    parallel_stage_open: bool = False

    def __post_init__(self) -> None:
        self.authoritative_section_indices.update(self.section_titles)


def _node_key(agent: str, section_idx: str) -> str:
    return f"{section_idx}:{agent}" if section_idx and section_idx != "0" else agent


def _task_frame(event_type: str, agent: str, section_idx: str, display: tuple[str, str]) -> dict:
    task_id = f"dr_{section_idx}_{agent}" if section_idx != "0" else f"dr_{agent}"
    task_content = display[0] + (f" - {display[1]}" if display[1] else "")
    return {
        "event_type": event_type,
        "task_id": task_id,
        "task_content": task_content,
        "node_name": agent,
        "section_idx": section_idx,
        "display_name": display[0],
        "description": display[1],
    }


def _stage_boundary(event_type: str, stage: int) -> dict:
    return {
        "event_type": event_type,
        "task_id": f"deepresearch_stage_{stage}",
        "task_content": DEEPRESEARCH_STAGES[stage - 1],
    }


def advance_stage(state: RouterState, stage: int, *, complete: bool = False) -> dict | None:
    """Advance the six-stage task snapshot without allowing regressions."""
    if complete:
        if state.stages_completed:
            return None
        state.current_stage = len(DEEPRESEARCH_STAGES)
        state.stages_completed = True
    else:
        if state.stages_completed or stage <= state.current_stage:
            return None
        if stage < 1 or stage > len(DEEPRESEARCH_STAGES):
            raise ValueError(f"invalid deepresearch stage: {stage}")
        state.current_stage = stage

    tasks = []
    for index, title in enumerate(DEEPRESEARCH_STAGES, start=1):
        if state.stages_completed or index < state.current_stage:
            status = "completed"
        elif index == state.current_stage:
            status = "in_progress"
        else:
            status = "pending"
        tasks.append({
            "task_id": f"deepresearch_stage_{index}",
            "task_content": title,
            "status": status,
        })

    completed = len(DEEPRESEARCH_STAGES) if state.stages_completed else state.current_stage - 1
    in_progress = 0 if state.stages_completed else 1
    return {
        "event_type": "task.update",
        "tasks": tasks,
        "total_tasks": len(DEEPRESEARCH_STAGES),
        "completed_tasks": completed,
        "in_progress_tasks": in_progress,
        "pending_tasks": len(DEEPRESEARCH_STAGES) - completed - in_progress,
    }


def _as_text(val) -> str:
    """marker 透传字段转文本:str 直用;dict/list(JSON)转字符串供 agent 按 (1) §Stage3 规则解析。"""
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    try:
        return json.dumps(val, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(val)


def collected_questions(state: RouterState) -> str:
    """Return question-generator message fragments in first-seen message order."""
    return "".join(
        "".join(state.question_parts[message_id])
        for message_id in state.question_order
    ).strip()


def _remember_question_chunk(state: RouterState, chunk: dict, content: Any) -> None:
    if (
        str(chunk.get("agent", "")).strip() not in _QUESTION_NODES
        or str(chunk.get("message_type", "")).strip() != "message_chunk"
    ):
        return
    text = _as_text(content)
    if not text:
        return
    message_id = str(chunk.get("message_id", "")).strip() or "__default__"
    if message_id not in state.question_parts:
        state.question_parts[message_id] = []
        state.question_order.append(message_id)
    state.question_parts[message_id].append(text)


def _chunk_reasoning_content(chunk: dict, content: Any) -> Any:
    reasoning = chunk.get("reasoning_content")
    if reasoning or not isinstance(content, str):
        return reasoning
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return reasoning
    return parsed.get("reasoning_content") if isinstance(parsed, dict) else reasoning


def _raw_process_parts(chunk: dict, content: Any) -> list[str]:
    """Return original process bodies without compaction or semantic summarization."""
    parts: list[str] = []
    for value in (_chunk_reasoning_content(chunk, content), content):
        text = _as_text(value)
        if not text or not text.strip() or text.strip() in _CONTROL_PROCESS_VALUES:
            continue
        if text not in parts:
            parts.append(text)
    return parts


def _remember_section_title(state: RouterState, section_idx: str, section_title: Any) -> str:
    section_title = _as_text(section_title).strip()
    if section_title and section_idx not in state.authoritative_section_indices:
        state.section_titles[section_idx] = section_title
    return state.section_titles.get(section_idx, "")


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _section_reasoning(state: RouterState, chunk: dict, content: str) -> dict:
    section_idx = str(chunk.get("section_idx", "0")).strip() or "0"
    section_title = _remember_section_title(state, section_idx, chunk.get("section_title"))
    payload = {
        "event_type": "chat.reasoning",
        "task_id": f"deepresearch_section_{section_idx}",
        "task_content": section_title or f"章节 {section_idx}",
        "stream_source_id": f"deepresearch_section_{section_idx}",
        "content": content,
    }
    task_index = _positive_int(section_idx)
    if task_index is not None:
        payload["task_index"] = task_index
    total_tasks = _positive_int(chunk.get("section_total"))
    if total_tasks is not None:
        payload["total_tasks"] = total_tasks
    return payload


def is_outline_status_placeholder(val: Any) -> bool:
    """Return whether SDK content is only the outline interaction status prompt."""
    text = _as_text(val).strip()
    return bool(re.fullmatch(r"round\s+\d+\s*:\s*waiting for user feedback\.?", text, re.IGNORECASE))


def build_interrupt_prompt(node_id: str, state: RouterState, marker: dict, query: str) -> str:
    """中断时按 node 拼交互内容进 outcome.prompt。

    marker 字段优先；outline/report 在 SDK marker 缺少正文时使用路由器累积内容。
    """
    if node_id == "feedback_handler":
        ctx = (
            _as_text(marker.get("prompt"))
            or _as_text(marker.get("content"))
            or _as_text(marker.get("questions"))
            or query
        )
        raw = _as_text(marker.get("prompt")) or state.interrupt_raw_prompt or ""
    elif node_id == "outline_interaction":
        # (1) §Stage3(b) 读 marker.content(JSON 解析为 OutlineContent),其次 marker.outline
        marker_content = _as_text(marker.get("content"))
        if is_outline_status_placeholder(marker_content):
            marker_content = ""
        ctx = (
            marker_content
            or _as_text(marker.get("outline"))
            or "".join(state.outline_parts)
            or query
        )
        raw = state.interrupt_raw_prompt or ""
        if is_outline_status_placeholder(raw):
            raw = ""
    elif node_id == "user_feedback_processor":
        rpt = "".join(state.report_parts)  # 报告不在 marker,必须累积
        ctx = rpt[:6000] + ("…\n(完整报告见最终产物)" if len(rpt) > 6000 else "")
        raw = state.interrupt_raw_prompt or ""
    else:
        ctx, raw = "", state.interrupt_raw_prompt or ""
    if ctx.strip():
        return f"{ctx.strip()}\n\n{raw.strip()}".strip() if raw.strip() else ctx.strip()
    return raw or "请输入反馈"


def route_chunk(chunk: dict, state: RouterState) -> list[dict]:
    """把一条 deepsearch chunk 路由成 0~N 个 send_push payload。

    职责:① 累积 report 和 outline,为不含正文的 interrupt marker 提供兜底;
         ② 捕获 interrupt chunk 的 node_id/raw_prompt 进 state(interrupt chunk 先于
            interrupted marker 到达,marker 到达时 tool 调 build_interrupt_prompt);
         ③ outline 正文和并行章节未经压缩的原始过程写入 chat.reasoning。
    status marker(__deepsearch_status__)不在此处理(由 tool 主循环识别);interrupted marker
    本体由 tool 主循环捕获后整块传给 build_interrupt_prompt(marker 参数)。
    """
    frames: list[dict] = []
    if "__deepsearch_status__" in chunk:
        return frames

    agent = str(chunk.get("agent", "")).strip()
    event = str(chunk.get("event", "")).strip()
    content = chunk.get("content", "")
    message_type = str(chunk.get("message_type", "")).strip()
    _remember_question_chunk(state, chunk, content)

    section_idx = str(chunk.get("section_idx", "0"))
    target_stage = 3 if agent in _SECTION_PROCESS_NODES and section_idx != "0" else _NODE_STAGE.get(agent)
    if target_stage is not None:
        if state.parallel_stage_open and target_stage > 3:
            frames.append(_stage_boundary("task.complete", 3))
            state.parallel_stage_open = False
        stage_update = advance_stage(state, target_stage)
        if stage_update is not None:
            frames.append(stage_update)
        if target_stage == 3 and state.current_stage == 3 and not state.parallel_stage_open:
            frames.append(_stage_boundary("task.start", 3))
            state.parallel_stage_open = True

    # 中断 chunk:捕获 raw_prompt + node_id,不转发(interrupted marker 到达时拼 prompt)
    if message_type == "interrupt" or str(chunk.get("event")) == "waiting_user_input":
        state.interrupt_node_id = agent
        state.interrupt_raw_prompt = content if isinstance(content, str) else str(content)
        cid = chunk.get("conversation_id", "")
        if cid:
            state.interrupt_conversation_id = cid
        return frames

    # 累积 report(仅 reporter;report 不在 marker,中断时拼进 outcome.prompt)
    parts_attr = _INTERACTION_NODES.get(agent)
    if parts_attr and isinstance(content, str) and content:
        getattr(state, parts_attr).append(content)

    if not agent or agent in _SKIP_NODES:
        return frames

    key = _node_key(agent, section_idx)
    display = NODE_DISPLAY_INFO.get(agent)
    if not display:
        return frames  # 未知节点不推(避免噪声)

    if agent in _SECTION_PROCESS_NODES and section_idx != "0":
        process_parts = _raw_process_parts(chunk, content)
        _remember_section_title(state, section_idx, chunk.get("section_title"))
        node_state = state.active_nodes.get(key)
        if node_state is None:
            node_state = {
                "started": True,
                "done": False,
                "agent_name": agent,
                "section_idx": section_idx,
            }
            state.active_nodes[key] = node_state
            if event != "done":
                frames.append(_section_reasoning(state, chunk, f"{display[0]}开始\n"))

        if event == "done" and not node_state["done"]:
            node_state["done"] = True
            frames.append(_section_reasoning(state, chunk, f"{display[0]}完成\n"))

        for process_content in process_parts:
            frames.append(_section_reasoning(state, chunk, process_content))
        return frames

    # 大纲正文只读展示在思考过程；其他非并行节点维持 reasoning_content 通用透传。
    if agent == "outline":
        task_content = display[0] + (f" - {display[1]}" if display[1] else "")
        for process_content in _raw_process_parts(chunk, content):
            frames.append({
                "event_type": "chat.reasoning",
                "task_id": "deepresearch_stage_2",
                "task_content": task_content,
                "stream_source_id": "dr_outline",
                "content": process_content,
            })
        return frames
    else:
        reasoning = _chunk_reasoning_content(chunk, content)
        if reasoning:
            frames.append({"event_type": "chat.reasoning", "content": _as_text(reasoning)})

    # 首次见 → task.start(交互节点也发边界气泡,让用户看到"大纲生成中"等)
    if key not in state.active_nodes:
        state.active_nodes[key] = {
            "started": True,
            "done": False,
            "agent_name": agent,
            "section_idx": section_idx,
        }
        frames.append(_task_frame("task.start", agent, section_idx, display))

    # event=done → task.complete
    if event == "done":
        node_state = state.active_nodes.get(key)
        if node_state and node_state["started"] and not node_state["done"]:
            node_state["done"] = True
            frames.append(_task_frame("task.complete", agent, section_idx, display))

    return frames
