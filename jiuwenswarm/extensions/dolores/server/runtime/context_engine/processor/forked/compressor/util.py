# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from typing import Any, Dict, List

from openjiuwen.core.foundation.llm import AssistantMessage, BaseMessage, UserMessage

from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.forked.compressor.support.util import (
    FullCompactStateReinjector,
    ReinjectedStateBuilderSpec,
    build_compressor_reinjected_state_message,
    build_file_reinjected_content,
    build_plan_mode_reinjected_content,
    build_plan_reinjected_content,
    build_single_reinjected_state_message,
    build_skill_reinjected_content,
    build_task_status_reinjected_content,
    build_todo_reinjected_content,
    build_tool_result_hint_reinjected_content,
    collect_summary_indices,
    count_messages_tokens,
    find_last_completed_api_round_end_idx,
    find_tool_result_text,
    is_summary_message,
    iter_summary_merge_ranges,
    message_to_text,
    parse_tool_arguments,
)

STATE_REINJECTION_MARKER = "[STATE_REINJECTION]"
TEAM_STATE_LABEL = "TEAM_STATE"
TEAM_MESSAGES_LABEL = "TEAM_MESSAGES"
TEAM_TOOL_CALL_NAMES = {"send_message", "view_task", "claim_task", "member_complete_task"}
TEAM_MESSAGE_TOOL_CALL_NAMES = {"send_message"}

__all__ = [
    "FullCompactStateReinjector",
    "ReinjectedStateBuilderSpec",
    "STATE_REINJECTION_MARKER",
    "build_compressor_reinjected_state_message",
    "build_file_reinjected_content",
    "build_plan_mode_reinjected_content",
    "build_plan_reinjected_content",
    "build_single_reinjected_state_message",
    "build_skill_reinjected_content",
    "build_task_status_reinjected_content",
    "build_team_collaboration_reinjected_messages",
    "build_todo_reinjected_content",
    "build_tool_result_hint_reinjected_content",
    "collect_summary_indices",
    "count_messages_tokens",
    "find_last_completed_api_round_end_idx",
    "find_tool_result_text",
    "is_summary_message",
    "iter_summary_merge_ranges",
    "message_to_text",
    "parse_tool_arguments",
]


def build_team_collaboration_reinjected_messages(messages: List[BaseMessage]) -> List[UserMessage]:
    state_lines = ["Task-board observations and updates recovered from compressed dialogue:"]
    message_lines = ["Member messages recovered from compressed dialogue:"]
    for message in messages:
        if _is_state_reinjection_message(message):
            continue
        if not isinstance(message, AssistantMessage):
            continue
        for tool_call in getattr(message, "tool_calls", None) or []:
            tool_name = getattr(tool_call, "name", "") or ""
            if tool_name not in TEAM_TOOL_CALL_NAMES:
                continue
            rendered = _render_team_tool_call_line(
                tool_call,
                find_tool_result_text(messages, getattr(tool_call, "id", None)),
            )
            if rendered:
                if tool_name in TEAM_MESSAGE_TOOL_CALL_NAMES:
                    message_lines.append(rendered)
                else:
                    state_lines.append(rendered)
    sections: List[str] = []
    if len(state_lines) > 1:
        sections.append(f"[{TEAM_STATE_LABEL}]\n" + "\n".join(state_lines))
    if len(message_lines) > 1:
        sections.append(f"[{TEAM_MESSAGES_LABEL}]\n" + "\n".join(message_lines))
    if not sections:
        return []
    return [
        UserMessage(
            content=(
                f"{STATE_REINJECTION_MARKER}\n"
                + "\n\n".join(sections)
            )
        )
    ]


def _is_state_reinjection_message(message: BaseMessage) -> bool:
    return isinstance(message, UserMessage) and message_to_text(message).startswith(STATE_REINJECTION_MARKER)


def _render_team_tool_call_line(tool_call: Any, result_text: str) -> str:
    tool_name = getattr(tool_call, "name", "") or ""
    arguments_text = getattr(tool_call, "arguments", "") or ""
    args = parse_tool_arguments(arguments_text)

    action_desc = _describe_team_tool_action(tool_name, args)
    line = f"- {action_desc} [{tool_name}]".rstrip()

    result_text = (result_text or "").strip()
    if result_text:
        compacted = " ".join(part.strip() for part in result_text.splitlines() if part.strip())
        if len(compacted) > 200:
            compacted = compacted[:200] + "..."
        line += f"\n  -> {compacted}"
    return line


def _describe_team_tool_action(tool_name: str, args: Dict[str, Any]) -> str:
    if tool_name == "send_message":
        to_raw = args.get("to")
        if isinstance(to_raw, list):
            targets = "、".join(str(item) for item in to_raw if item)
            base = f"向 {targets} 发送消息" if targets else "发送消息"
        elif to_raw == "*":
            base = "向全队广播消息"
        elif to_raw:
            base = f"向 {to_raw} 发送消息"
        else:
            base = "发送消息"
        summary = str(args.get("summary") or "").strip()
        if not summary:
            content = str(args.get("content") or "")
            summary = (content[:80] + "...") if len(content) > 80 else content
        return f"{base}：{summary}" if summary else base

    if tool_name == "view_task":
        action = args.get("action") or "list"
        if action == "get":
            task_id = args.get("task_id")
            return f"查看任务 #{task_id} 详情" if task_id else "查看任务详情"
        if action == "claimable":
            return "查询可领取的任务"
        status = args.get("status")
        return f"列出任务（状态={status}）" if status else "列出任务"

    if tool_name == "claim_task":
        task_id = args.get("task_id") or "?"
        status = args.get("status")
        if status == "completed":
            return f"完成任务 #{task_id}"
        if status == "claimed":
            return f"认领任务 #{task_id}"
        return f"处理任务 #{task_id}（status={status or '?'}）"

    if tool_name == "member_complete_task":
        task_id = args.get("task_id") or "?"
        note = str(args.get("note") or "").strip()
        note_short = (note[:80] + "...") if len(note) > 80 else note
        if note_short:
            return f"完成自己负责的任务 #{task_id}，备注：{note_short}"
        return f"完成自己负责的任务 #{task_id}"

    return f"调用 {tool_name}"
