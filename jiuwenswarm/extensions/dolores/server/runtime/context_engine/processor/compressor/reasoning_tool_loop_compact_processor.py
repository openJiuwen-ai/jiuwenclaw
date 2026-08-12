# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Compact consecutive identical reasoning + tool-call rounds in context."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from openjiuwen.core.common.logging import logger
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.base import ModelContext
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.context_engine import ContextEngine
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.base import ContextEvent, ContextProcessor
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    BaseMessage,
    ToolMessage,
)

_LOOP_WARNING_TEMPLATE_CN = """\
检测到连续多轮返回思考内容相同且调用了相同的工具集。
重复的思考内容预览：
---
{reasoning_preview}
---
工具调用命令如下：
---
{tool_calls}
---
工具执行结果如下：
---
{tool_results}
---
**请跳出多轮重复执行，推进尚未完成的实质工作。**
"""

_LOOP_WARNING_TEMPLATE_EN = """\
Detected consecutive turns with identical reasoning_content and the same tool name set.
Repeated reasoning preview:
---
{reasoning_preview}
---
Tool calls:
---
{tool_calls}
---
Tool results:
---
{tool_results}
---
**Break out of the multi-turn repeated execution, and make real progress on unfinished work.**
"""

_LOOP_WARNING_TEMPLATES = {
    "cn": _LOOP_WARNING_TEMPLATE_CN,
    "en": _LOOP_WARNING_TEMPLATE_EN,
}


class ReasoningToolLoopCompactProcessorConfig(BaseModel):
    """Detect and compact consecutive identical reasoning + tool-call rounds."""

    enabled: bool = Field(
        default=True,
        description="Enable consecutive reasoning/tool-call loop compaction.",
    )
    consecutive_threshold: int = Field(
        default=3,
        ge=2,
        description=(
            "Trigger compaction when this many consecutive completed tool rounds "
            "share identical reasoning_content and identical tool name set."
        ),
    )
    reasoning_min_chars: int = Field(
        default=4,
        ge=1,
        description="Ignore rounds whose reasoning_content is shorter than this after strip.",
    )
    reasoning_preview_max_chars: int = Field(
        default=512,
        ge=1,
        description="Max characters of reasoning kept inside the loop warning AssistantMessage.",
    )
    language: str = Field(
        default="cn",
        description="Loop warning language ('cn' or 'en').",
    )


@dataclass(frozen=True)
class _ToolRound:
    start: int
    end: int
    fingerprint: Tuple[str, Tuple[str, ...]]
    reasoning: str
    tool_names: Tuple[str, ...]


@ContextEngine.register_processor()
class ReasoningToolLoopCompactProcessor(ContextProcessor):
    """Fold consecutive identical reasoning + tool-call rounds.

    Match rule (strict AND):
      - reasoning_content identical (after strip, and long enough)
      - tool name set identical (multi-tool rounds compare as a set; arguments
        and tool results are ignored)

    Compaction runs on the ADD path only (after tool results are committed):
      - delete all matched duplicated rounds
      - insert one AssistantMessage with the latest round's tool calls and results
    """

    @property
    def config(self) -> ReasoningToolLoopCompactProcessorConfig:
        return self._config

    async def trigger_add_messages(
            self,
            context: ModelContext,
            messages_to_add: List[BaseMessage],
            **kwargs: Any,
    ) -> bool:
        if not self.config.enabled:
            return False
        all_messages = context.get_messages() + messages_to_add
        if not self._api_round(all_messages):
            return False
        return self._find_compact_range(all_messages) is not None

    async def on_add_messages(
            self,
            context: ModelContext,
            messages_to_add: List[BaseMessage],
            **kwargs: Any,
    ) -> Tuple[ContextEvent | None, List[BaseMessage]]:
        all_messages = context.get_messages() + messages_to_add
        compacted = self._compact_messages(all_messages)
        if compacted is None:
            return None, messages_to_add

        context.set_messages(compacted)
        logger.info(
            "[ReasoningToolLoopCompact] compacted consecutive identical "
            "reasoning/tool rounds on ADD path: before=%d after=%d",
            len(all_messages),
            len(compacted),
        )
        return ContextEvent(
            event_type=self.processor_type(),
            compact_summary="reasoning_tool_loop_compacted",
        ), []

    def load_state(self, state: Dict[str, Any]) -> None:
        return

    def save_state(self) -> Dict[str, Any]:
        return {}

    def _compact_messages(self, messages: List[BaseMessage]) -> Optional[List[BaseMessage]]:
        compact_range = self._find_compact_range(messages)
        if compact_range is None:
            return None

        fold_start, fold_end, rounds = compact_range
        latest = rounds[-1]
        tool_calls_text, tool_results_text = self._collect_call_and_result_sets(
            messages[latest.start:latest.end]
        )
        summary = AssistantMessage(
            content=self._build_warning_content(
                reasoning=latest.reasoning,
                tool_calls=tool_calls_text,
                tool_results=tool_results_text,
            )
        )
        return messages[:fold_start] + [summary]

    def _find_compact_range(
            self,
            messages: Sequence[BaseMessage],
    ) -> Optional[Tuple[int, int, List[_ToolRound]]]:
        rounds = self._collect_tool_rounds(messages)
        if len(rounds) < self.config.consecutive_threshold:
            return None

        trailing: List[_ToolRound] = [rounds[-1]]
        fingerprint = rounds[-1].fingerprint
        for round_info in reversed(rounds[:-1]):
            if round_info.fingerprint != fingerprint:
                break
            trailing.append(round_info)
        trailing.reverse()

        if len(trailing) < self.config.consecutive_threshold:
            return None

        # Fold every matched trailing round; do not keep a raw tool call/result.
        fold_start = trailing[0].start
        fold_end = trailing[-1].end
        return fold_start, fold_end, trailing

    def _collect_tool_rounds(self, messages: Sequence[BaseMessage]) -> List[_ToolRound]:
        rounds: List[_ToolRound] = []
        index = 0
        total = len(messages)
        while index < total:
            message = messages[index]
            if not isinstance(message, AssistantMessage):
                index += 1
                continue

            tool_calls = getattr(message, "tool_calls", None) or []
            if not tool_calls:
                index += 1
                continue

            reasoning = self._normalize_reasoning(getattr(message, "reasoning_content", None))
            if reasoning is None:
                index += 1
                continue

            pending_ids = {
                str(getattr(tool_call, "id", "") or "")
                for tool_call in tool_calls
                if getattr(tool_call, "id", None)
            }
            if not pending_ids:
                index += 1
                continue

            tool_names = tuple(
                sorted(
                    str(getattr(tool_call, "name", "") or "")
                    for tool_call in tool_calls
                    if str(getattr(tool_call, "name", "") or "")
                )
            )
            if not tool_names:
                index += 1
                continue

            cursor = index + 1
            while cursor < total and pending_ids:
                next_message = messages[cursor]
                if not isinstance(next_message, ToolMessage):
                    break
                tool_call_id = str(getattr(next_message, "tool_call_id", "") or "")
                if tool_call_id in pending_ids:
                    pending_ids.discard(tool_call_id)
                    cursor += 1
                    continue
                break

            if pending_ids:
                index += 1
                continue

            rounds.append(
                _ToolRound(
                    start=index,
                    end=cursor,
                    fingerprint=(reasoning, tool_names),
                    reasoning=reasoning,
                    tool_names=tool_names,
                )
            )
            index = cursor

        return rounds

    def _collect_call_and_result_sets(
            self,
            folded_messages: Sequence[BaseMessage],
    ) -> Tuple[str, str]:
        """Serialize latest-round tool calls/results as structured plain-text JSON."""
        call_items: List[Dict[str, Any]] = []
        result_items: List[Dict[str, Any]] = []
        tool_name_by_id: Dict[str, str] = {}

        for message in folded_messages:
            if isinstance(message, AssistantMessage):
                for tool_call in getattr(message, "tool_calls", None) or []:
                    name = str(getattr(tool_call, "name", "") or "").strip() or "(unknown)"
                    tool_call_id = str(getattr(tool_call, "id", "") or "")
                    if tool_call_id:
                        tool_name_by_id[tool_call_id] = name
                    call_items.append({
                        "name": name,
                        "arguments": _parse_tool_arguments(
                            getattr(tool_call, "arguments", None)
                        ),
                    })
            elif isinstance(message, ToolMessage):
                content = message.content
                if not isinstance(content, str) or not content.strip():
                    continue
                tool_call_id = str(getattr(message, "tool_call_id", "") or "")
                result_items.append({
                    "name": tool_name_by_id.get(tool_call_id, "(unknown)"),
                    "content": content.strip(),
                })

        return (
            _dumps_json_list(call_items),
            _dumps_json_list(result_items),
        )

    def _build_warning_content(
            self,
            *,
            reasoning: str,
            tool_calls: str,
            tool_results: str,
    ) -> str:
        preview = reasoning
        max_chars = self.config.reasoning_preview_max_chars
        if len(preview) > max_chars:
            preview = preview[:max_chars] + "\n...(truncated)"
        language = _normalize_language(self.config.language)
        template = _LOOP_WARNING_TEMPLATES.get(language, _LOOP_WARNING_TEMPLATE_CN)
        return template.format(
            tool_calls=tool_calls,
            tool_results=tool_results,
            reasoning_preview=preview,
        ).strip()

    def _normalize_reasoning(self, raw: Any) -> Optional[str]:
        if not isinstance(raw, str):
            return None
        normalized = raw.strip()
        if len(normalized) < self.config.reasoning_min_chars:
            return None
        return normalized


def _parse_tool_arguments(raw: Any) -> Any:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return raw
    text = raw.strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


def _dumps_json_list(items: List[Dict[str, Any]]) -> str:
    if not items:
        return "[]"
    return json.dumps(items, ensure_ascii=False, indent=2)


def _normalize_language(language: str | None) -> str:
    normalized = (language or "cn").strip().lower()
    if normalized.startswith("zh") or normalized == "cn":
        return "cn"
    if normalized == "en":
        return "en"
    return "cn"


__all__ = [
    "ReasoningToolLoopCompactProcessor",
    "ReasoningToolLoopCompactProcessorConfig",
]
