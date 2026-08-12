# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from openjiuwen.core.common.logging import logger
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.base import ModelContext
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.context.context_utils import ContextUtils
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.forked.compressor.reinjection import (
    ReinjectContext,
    StateReinjector as FullCompactStateReinjector,
    build_file_reinjected_content,
    build_plan_mode_reinjected_content,
    build_plan_reinjected_content,
    build_skill_reinjected_content,
    build_single_reinjected_state_message,
    build_task_status_reinjected_content,
    build_todo_reinjected_content,
    build_tool_result_hint_reinjected_content,
)
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.context.session_memory_manager import (
    group_completed_api_rounds as group_completed_api_round_ranges,
)
from openjiuwen.core.foundation.llm import AssistantMessage, BaseMessage, ToolMessage, UserMessage

STATE_REINJECTION_MARKER = "[STATE_REINJECTION]"

__all__ = [
    "FullCompactStateReinjector",
    "ReinjectedStateBuilderSpec",
    "STATE_REINJECTION_MARKER",
    "build_compressor_reinjected_state_message",
    "build_file_reinjected_content",
    "build_plan_mode_reinjected_content",
    "build_plan_reinjected_content",
    "build_skill_reinjected_content",
    "build_task_status_reinjected_content",
    "build_todo_reinjected_content",
    "build_tool_result_hint_reinjected_content",
]


@dataclass(frozen=True)
class ReinjectedStateBuilderSpec:
    name: str
    label: str
    builder: Callable[..., Any]


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


def resolve_context_max(context: ModelContext, model_name: str | None = None) -> int:
    """Resolve the token capacity used by context processor ratio thresholds."""
    return ContextUtils.resolve_context_max(
        model_name=model_name or getattr(context, "_model_name", None),
        fallback_context_window_tokens=getattr(context, "_context_window_tokens", None),
        model_context_window_tokens=getattr(context, "_model_context_window_tokens", None),
    )


def resolve_ratio_token_threshold(context_max: int, ratio: float) -> int:
    """Convert a context-capacity ratio to a positive token threshold."""
    return max(int(context_max * ratio), 1)


def build_compressor_reinjected_state_message(
    *,
    source_messages: List[BaseMessage],
    messages_to_keep: List[BaseMessage],
    context: Any,
    config: Any,
    builder_names: List[str] | None,
    session_state: dict[str, Any] | None = None,
) -> UserMessage | None:
    ctx = ReinjectContext(
        session_state=session_state if session_state is not None else get_session_state_for_reinjection(context),
        source_messages=source_messages,
        messages_to_keep=messages_to_keep,
        workspace_root=get_workspace_root_for_reinjection(context),
        config=config,
        state_marker=STATE_REINJECTION_MARKER,
        truncate=lambda text: truncate_state_text(
            text,
            int(getattr(config, "reinject_max_chars_per_section", 12000) or 12000),
        ),
        context=context,
    )
    return build_single_reinjected_state_message(ctx, builder_names=builder_names)


def get_session_state_for_reinjection(context: Any) -> dict[str, Any]:
    if context is None or not hasattr(context, "get_session_ref"):
        return {}
    try:
        session = context.get_session_ref()
    except Exception:
        return {}
    if session is None or not hasattr(session, "get_state"):
        return {}
    try:
        state = session.get_state()
    except Exception:
        return {}
    return state if isinstance(state, dict) else {}


def get_workspace_root_for_reinjection(context: Any) -> str | None:
    if context is None or not hasattr(context, "workspace_dir"):
        return None
    try:
        workspace_root = context.workspace_dir()
    except Exception:
        return None
    return workspace_root or None


def truncate_state_text(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    head = text[: max_chars // 2]
    tail = text[-max(max_chars - len(head), 0):]
    return f"{head}\n...[STATE_REINJECTION_TRUNCATED]...\n{tail}"


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
