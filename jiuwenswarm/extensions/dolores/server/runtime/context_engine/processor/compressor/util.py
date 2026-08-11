# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from openjiuwen.core.common.logging import logger
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.context.session_memory_manager import (
    group_completed_api_rounds as group_completed_api_round_ranges,
)
from openjiuwen.core.foundation.llm import AssistantMessage, BaseMessage, ToolMessage, UserMessage


STATE_REINJECTION_MARKER = "[STATE_REINJECTION]"
TEAM_STATE_LABEL = "TEAM_STATE"
TEAM_MESSAGES_LABEL = "TEAM_MESSAGES"
TEAM_TOOL_CALL_NAMES = {"send_message", "view_task", "claim_task", "member_complete_task"}
TEAM_MESSAGE_TOOL_CALL_NAMES = {"send_message"}


@dataclass(frozen=True)
class ReinjectedStateBuilderSpec:
    name: str
    label: str
    builder: Callable[..., Any]


class FullCompactStateReinjector:
    def __init__(self) -> None:
        self._builders: List[ReinjectedStateBuilderSpec] = []

    def register_builder(
        self,
        *,
        name: str,
        label: str,
        builder: Callable[..., Any],
    ) -> None:
        spec = ReinjectedStateBuilderSpec(name=name, label=label, builder=builder)
        for index, existing in enumerate(self._builders):
            if existing.name == name:
                self._builders[index] = spec
                return
        self._builders.append(spec)

    def iter_builders(self) -> Tuple[ReinjectedStateBuilderSpec, ...]:
        return tuple(self._builders)


def build_plan_reinjected_content(
    processor: Any,
    *,
    context: Any,
    messages: List[BaseMessage],
    messages_to_keep: List[BaseMessage],
) -> str:
    _ = processor, context, messages, messages_to_keep
    return ""


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


def build_skill_reinjected_content(
    processor: Any,
    *,
    context: Any,
    messages: List[BaseMessage],
    messages_to_keep: List[BaseMessage],
) -> List[UserMessage]:
    _ = context
    keep_signatures = {message_signature(message) for message in messages_to_keep}
    selected_rounds: List[List[BaseMessage]] = []
    seen_round_signatures: set[tuple[str, ...]] = set()

    for round_messages in reversed(group_completed_api_rounds(messages)):
        round_signatures = tuple(message_signature(message) for message in round_messages)
        if round_signatures in seen_round_signatures:
            continue
        if any(signature in keep_signatures for signature in round_signatures):
            continue
        if not round_contains_skill_read(round_messages):
            continue
        selected_rounds.append([message.model_copy(deep=True) for message in round_messages])
        seen_round_signatures.add(round_signatures)
        if len(selected_rounds) >= processor.advanced_config.reinject_recent_skills:
            break

    selected_rounds.reverse()
    reinjected_messages: List[UserMessage] = []
    for round_messages in selected_rounds:
        serialized_round = "\n".join(
            f"role={message.role}, content={message_to_text(message)}" for message in round_messages
        )
        reinjected_messages.append(
            UserMessage(
                content=(
                    f"{processor.state_marker}\n[SKILLS]\n{processor.truncate_state_text(serialized_round)}"
                )
            )
        )
    return reinjected_messages


def build_file_reinjected_content(
    processor: Any,
    *,
    context: Any,
    messages: List[BaseMessage],
    messages_to_keep: List[BaseMessage],
) -> str:
    _ = processor, context, messages, messages_to_keep
    return ""


def build_task_status_reinjected_content(
    processor: Any,
    *,
    context: Any,
    messages: List[BaseMessage],
    messages_to_keep: List[BaseMessage],
) -> str:
    _ = messages, messages_to_keep
    session_state = context.get_session_ref().get_state()
    try:
        iteration = int(session_state.get("task_state", {}).get("iteration") or 0)
    except Exception:
        iteration = 0
    try:
        pending_follow_ups = list(session_state.get("task_state", {}).get("pending_follow_ups") or [])
    except Exception:
        pending_follow_ups = []

    stop_condition_state = (
        session_state.get("task_state", {}).get("stop_condition_state", {})
        if isinstance(session_state.get("task_state", {}), dict)
        else None
    )
    stop_reason = stop_condition_state.get("stop_reason") if isinstance(stop_condition_state, dict) else None

    if not iteration and not pending_follow_ups and not stop_reason:
        return ""

    lines = [
        "Current task-loop status for this session:",
        f"- Completed outer-loop rounds: {iteration}.",
        f"- Pending follow-up queries: {len(pending_follow_ups)}.",
    ]
    if stop_reason:
        lines.append(f"- Last recorded stop reason: {stop_reason}.")
    return processor.truncate_state_text("\n".join(lines))


def build_plan_mode_reinjected_content(
    processor: Any,
    *,
    context: Any,
    messages: List[BaseMessage],
    messages_to_keep: List[BaseMessage],
) -> str:
    _ = messages, messages_to_keep
    session_state = context.get_session_ref().get_state()

    try:
        plan_mode = session_state.get("plan_mode", {})
    except Exception:
        plan_mode = None
    if not isinstance(plan_mode, dict):
        return ""

    mode = plan_mode.get("mode", "auto")
    pre_plan_mode = plan_mode.get("pre_plan_mode", "")
    plan_slug = plan_mode.get("plan_slug", "")

    lines = [
        "Current plan-mode status for this session:",
        f"- Active mode: {mode}.",
    ]
    if pre_plan_mode:
        lines.append(f"- Previous mode before entering plan mode: {pre_plan_mode}.")
    if plan_slug:
        lines.append(f"- Active plan identifier: {plan_slug}.")
    return processor.truncate_state_text("\n".join(lines))


def is_skill_file_path(file_path: str) -> bool:
    if not file_path:
        return False
    normalized = file_path.replace("\\", "/").lower()
    return normalized.endswith("/skill.md") or normalized.endswith("skill.md")


def extract_skill_name_from_path(file_path: str) -> str:
    if not file_path:
        return ""
    normalized = file_path.replace("\\", "/").rstrip("/")
    parts = normalized.split("/")
    if len(parts) >= 2 and parts[-1].lower() == "skill.md":
        return parts[-2]
    return ""


def round_contains_skill_read(messages: List[BaseMessage]) -> bool:
    for message in messages:
        if not isinstance(message, AssistantMessage):
            continue
        for tool_call in getattr(message, "tool_calls", None) or []:
            tool_name = getattr(tool_call, "name", "") or ""
            if tool_name != "read_file":
                continue
            arguments_text = getattr(tool_call, "arguments", "") or ""
            parsed_arguments = parse_tool_arguments(arguments_text)
            file_path = extract_argument_value(parsed_arguments, arguments_text, ("file_path",))
            if is_skill_file_path(file_path):
                return True
    return False


def group_completed_api_rounds(messages: List[BaseMessage]) -> List[List[BaseMessage]]:
    return [list(messages[start:end]) for start, end in group_completed_api_round_ranges(messages)]


def message_signature(message: BaseMessage) -> str:
    tool_call_ids = []
    if isinstance(message, AssistantMessage):
        tool_call_ids = [
            getattr(tool_call, "id", "") or "" for tool_call in (getattr(message, "tool_calls", None) or [])
        ]
    raw = f"{message.role}|{message_to_text(message)}|{'|'.join(tool_call_ids)}"
    return raw


def extract_skill_file_content(processor: Any, result_text: str) -> str:
    if not result_text:
        return ""

    content_match = re.search(
        r'"content"\s*:\s*"(?P<content>(?:[^"\\]|\\.)*)"',
        result_text,
        re.DOTALL,
    )
    content = ""
    if content_match:
        raw_content = content_match.group("content")
        try:
            content = json.loads(f'"{raw_content}"')
        except Exception:
            content = raw_content.replace('\\"', '"').replace("\\n", "\n")
    else:
        content = result_text

    content = content.strip()
    if not content:
        return ""
    return processor.truncate_state_text(content)


def describe_tool_call(tool_name: str, arguments_text: str) -> str:
    parsed_arguments = parse_tool_arguments(arguments_text)
    if tool_name == "read_file":
        file_path = extract_argument_value(parsed_arguments, arguments_text, ("file_path",))
        return f"read_file path={file_path or '[unknown]'}"
    if tool_name == "write_file":
        file_path = extract_argument_value(parsed_arguments, arguments_text, ("file_path",))
        return f"write_file path={file_path or '[unknown]'}"
    if tool_name == "edit_file":
        file_path = extract_argument_value(parsed_arguments, arguments_text, ("file_path",))
        return f"edit_file path={file_path or '[unknown]'}"
    if tool_name == "glob":
        pattern = extract_argument_value(parsed_arguments, arguments_text, ("pattern",))
        path = extract_argument_value(parsed_arguments, arguments_text, ("path",))
        return f"glob pattern={pattern or '[unknown]'} path={path or '.'}"
    if tool_name == "grep":
        pattern = extract_argument_value(parsed_arguments, arguments_text, ("pattern",))
        path = extract_argument_value(parsed_arguments, arguments_text, ("path", "file_path"))
        return f"grep pattern={pattern or '[unknown]'} path={path or '[unknown]'}"
    return f"{tool_name} args={arguments_text}"


def parse_tool_arguments(arguments_text: str) -> Dict[str, Any]:
    if not arguments_text:
        return {}
    try:
        parsed = json.loads(arguments_text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def extract_argument_value(
    parsed_arguments: Dict[str, Any],
    arguments_text: str,
    keys: Tuple[str, ...],
) -> str:
    for key in keys:
        value = parsed_arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in keys:
        match = re.search(rf'"{re.escape(key)}"\s*:\s*"([^"]+)"', arguments_text)
        if match:
            return match.group(1).strip()
    return ""


def find_tool_result_text(
    messages: List[BaseMessage],
    tool_call_id: Optional[str],
) -> str:
    if not tool_call_id:
        return ""
    for message in reversed(messages):
        if isinstance(message, ToolMessage) and getattr(message, "tool_call_id", None) == tool_call_id:
            return message_to_text(message)
    return ""


def extract_tool_result_hint(
    tool_name: str,
    result_text: str,
    allowed_tool_names: List[str],
) -> str:
    if not result_text:
        return ""
    if tool_name not in set(allowed_tool_names):
        return ""
    if tool_name == "read_file":
        file_path_match = re.search(r'"file_path"\s*:\s*"([^"]+)"', result_text)
        line_count_match = re.search(r'"line_count"\s*:\s*(\d+)', result_text)
        parts = []
        if file_path_match:
            parts.append(f"result_path={file_path_match.group(1)}")
        if line_count_match:
            parts.append(f"lines={line_count_match.group(1)}")
        return " ".join(parts)
    if tool_name == "glob":
        count_match = re.search(r'"count"\s*:\s*(\d+)', result_text)
        if count_match:
            return f"matches={count_match.group(1)}"
    if tool_name == "grep":
        count_match = re.search(r'"count"\s*:\s*(\d+)', result_text)
        if count_match:
            return f"hits={count_match.group(1)}"
    if tool_name == "edit_file":
        replacements_match = re.search(r'"replacements"\s*:\s*(\d+)', result_text)
        if replacements_match:
            return f"replacements={replacements_match.group(1)}"
    if tool_name == "write_file":
        bytes_match = re.search(r'"bytes_written"\s*:\s*(\d+)', result_text)
        if bytes_match:
            return f"bytes_written={bytes_match.group(1)}"
    return ""


def message_to_text(message: BaseMessage) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except TypeError:
        return str(content)


def is_summary_message(message: BaseMessage, summary_marker: str) -> bool:
    """Return whether a message is a compressed memory block with the given marker."""
    return (
        isinstance(message, UserMessage)
        and isinstance(message.content, str)
        and message.content.startswith(summary_marker)
    )


def collect_summary_indices(messages: List[BaseMessage], summary_marker: str) -> List[int]:
    """Return indices of all compressed summary messages with the given marker."""
    return [i for i, msg in enumerate(messages) if is_summary_message(msg, summary_marker)]


def estimate_content_tokens(content: Any) -> int:
    """Approximate token count when no token counter is available."""
    if isinstance(content, str):
        return len(content) // 3
    try:
        return len(json.dumps(content, ensure_ascii=False)) // 3
    except TypeError:
        return len(str(content)) // 3


def count_messages_tokens(messages: List[BaseMessage], token_counter, processor_type: str = "") -> int:
    """Count tokens with tokenizer-first strategy and character fallback."""
    if not messages:
        return 0
    if token_counter is not None:
        try:
            return token_counter.count_messages(messages)
        except Exception as exc:  # pragma: no cover - defensive fallback
            prefix = f"[{processor_type}] " if processor_type else ""
            logger.warning(f"{prefix}token_counter failed, fallback to char-based estimate: {exc}")
    return sum(estimate_content_tokens(getattr(message, "content", "")) for message in messages)


def find_last_completed_api_round_end_idx(
        messages: List[BaseMessage],
        start_idx: int,
        end_idx: int,
) -> int:
    """Return absolute end index for the last complete API round in the selected range."""
    if end_idx < start_idx:
        return end_idx
    candidate_messages = messages[start_idx:end_idx + 1]
    completed_rounds = group_completed_api_round_ranges(candidate_messages)
    if not completed_rounds:
        return start_idx - 1
    _, completed_end = completed_rounds[-1]
    return start_idx + completed_end - 1


def iter_summary_merge_ranges(
        messages: List[BaseMessage],
        summary_marker: str,
        min_blocks: int,
) -> List[Tuple[int, int]]:
    """Return contiguous summary-message ranges eligible for second-stage merge."""
    ranges: List[Tuple[int, int]] = []
    start_idx: Optional[int] = None
    previous_idx: Optional[int] = None

    for idx, message in enumerate(messages):
        if is_summary_message(message, summary_marker):
            if start_idx is None:
                start_idx = idx
            previous_idx = idx
            continue
        if start_idx is not None and previous_idx is not None:
            if previous_idx - start_idx + 1 >= min_blocks:
                ranges.append((start_idx, previous_idx))
            start_idx = None
            previous_idx = None

    if start_idx is not None and previous_idx is not None:
        if previous_idx - start_idx + 1 >= min_blocks:
            ranges.append((start_idx, previous_idx))

    return ranges
