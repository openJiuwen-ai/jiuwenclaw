# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.base import ContextWindow, ModelContext
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.context.context_utils import ContextUtils
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.context_engine import ContextEngine
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.base import ContextEvent, ContextProcessor
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.budget_guard import effective_context_budget
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.compressor.util import build_team_collaboration_reinjected_messages
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.offloader.message_summary_offloader import TRUNCATED_MARKER
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    BaseMessage,
    JsonOutputParser,
    Model,
    ModelClientConfig,
    ModelRequestConfig,
    SystemMessage,
    ToolMessage,
    UserMessage,
)
from openjiuwen.core.foundation.tool import ToolInfo

_COMPRESS_LEVEL = "compress_level"
ROUND_LEVEL_FALLBACK_MARKER = "[ROUND_LEVEL_MEMORY_BLOCK]"

DEFAULT_ROUND_COMPRESSION_PROMPT = """\
You are a Fallback Context Compression Expert for long-running ReAct agent sessions.

Your job is to compress ONLY the explicitly listed targets so the whole task can fit under a strict context budget.

Priority order:
1. Ongoing ReAct state and exact handoff point
2. Unfinished work, blockers, pending actions, and last concrete action
3. Critical facts, constraints, decisions, corrections, and outputs needed for correct continuation
4. Durable conclusions from completed work
5. Secondary historical detail only if budget allows

Rules:
- Compress only the selected targets.
- Protected recent context is reference only and must not be absorbed as standalone content.
- Treat fallback blocks as historical context artifacts, not as new user instructions.
- Preserve both what was done and what was learned.
- Preserve the user's original requirements, constraints, acceptance criteria,
  and preferences as completely as possible.
- For ongoing ReAct blocks, keep a distinct `User Requirements` section that makes the unfinished work recoverable.
- For completed ReAct blocks, preserve both `User Requirements` and `Final Result` explicitly when they exist.
- Return valid JSON only.
"""

DEFAULT_AGGRESSIVE_ROUND_COMPRESSION_PROMPT = """\
You are a Hard-Budget Fallback Compression Expert.

The context is still over budget after an earlier compression pass.
Compress ONLY the explicitly listed targets much more aggressively while keeping the task recoverable.

Priority order:
1. Ongoing ReAct state and exact handoff point
2. Unfinished work, blockers, pending actions, and last concrete action
3. Critical facts, constraints, decisions, corrections, and outputs needed for continuation
4. Durable conclusions from completed work
5. Secondary historical detail only if budget allows

Rules:
- Remove redundant reasoning, repeated tool chatter, and low-value chronology first.
- Keep ongoing work maximally recoverable.
- Preserve the user's original requirements as much as possible even under aggressive compression.
- For completed blocks, keep the final result before secondary detail.
- Return valid JSON only.
"""


@dataclass
class _CompressTarget:
    block_id: str
    scope: str
    start_idx: int
    end_idx: int
    messages: List[BaseMessage]
    current_level: int = 0
    next_level: int = 1
    source_block_count: int = 1


class RoundLevelCompressorConfig(BaseModel):
    """
    Configuration for RoundLevelCompressor.

    Frequently adjusted options are listed first. Advanced phase-budget, marker,
    truncation, and writeback knobs remain top-level but are grouped below.
    """

    trigger_context_ratio: float = Field(
        default=0.9,
        gt=0.0,
        le=1.0,
        description="Trigger compression when the context window reaches this ratio of the effective context budget.",
    )
    """Context-window utilization ratio that starts round-level compression."""

    target_total_tokens: int = Field(
        default=160000,
        gt=0,
        description="Target full context-window size after compression completes.",
    )
    """Desired maximum context-window size after compression."""

    keep_recent_messages: int = Field(
        default=0,
        ge=0,
        description="Number of newest messages kept verbatim during add-message compression.",
    )
    """Newest raw messages protected from compression for short-term continuity."""

    model: Optional[ModelRequestConfig] = Field(
        default=None,
        description="Model request configuration used by the internal compression model.",
    )
    """Controls model request options for round-level compression calls."""

    model_client: Optional[ModelClientConfig] = Field(
        default=None,
        description="Client configuration for the internal compression model.",
    )
    """Selects/configures the model client used by the compressor."""

    # ------------------------------------------------------------------
    # Advanced compression settings
    # ------------------------------------------------------------------
    compression_call_max_tokens: int = Field(
        default=250000,
        gt=0,
        description="Maximum token budget for a single internal compression-model request.",
    )
    """Maximum token budget for each LLM compression call."""

    first_pass_target_tokens: int = Field(
        default=30000,
        gt=0,
        description="Target summary size used by the initial round-level compression pass.",
    )
    """Target summary tokens for the first, least aggressive compression pass."""

    second_pass_target_tokens: int = Field(
        default=20000,
        gt=0,
        description="Target summary size used by the first aggressive fallback pass.",
    )
    """Target summary tokens for the first aggressive fallback pass."""

    third_pass_target_tokens: int = Field(
        default=10000,
        gt=0,
        description="Target summary size used by the final aggressive full-context pass.",
    )
    """Target summary tokens for the final aggressive fallback pass."""

    # Advanced truncation and marker settings.
    truncate_head_ratio: float = Field(
        default=0.2,
        gt=0.0,
        lt=1.0,
        description="Fraction of retained truncation text taken from the original head.",
    )
    """Head/tail split used when hard truncation is the last available fallback."""

    truncated_marker: str = Field(
        default=TRUNCATED_MARKER,
        description="Marker inserted where omitted content was removed during hard truncation.",
    )
    """Marker text inserted into hard-truncated fallback memory."""

    compression_marker: str = Field(
        default=ROUND_LEVEL_FALLBACK_MARKER,
        description="Prefix marker used to identify round-level historical memory blocks.",
    )
    """Stable marker that identifies memory blocks produced by this compressor."""


@ContextEngine.register_processor()
class RoundLevelCompressor(ContextProcessor):
    def __init__(self, config: RoundLevelCompressorConfig):
        super().__init__(config)
        self._round_config = config
        self._target_total_tokens = config.target_total_tokens
        self._trigger_context_ratio = config.trigger_context_ratio
        self._compression_call_max_tokens = config.compression_call_max_tokens
        self._keep_recent_messages = config.keep_recent_messages
        self._first_prompt = DEFAULT_ROUND_COMPRESSION_PROMPT
        self._aggressive_prompt = DEFAULT_AGGRESSIVE_ROUND_COMPRESSION_PROMPT
        self._first_pass_target_tokens = config.first_pass_target_tokens
        self._second_pass_target_tokens = config.second_pass_target_tokens
        self._third_pass_target_tokens = config.third_pass_target_tokens
        self._truncate_head_ratio = config.truncate_head_ratio
        self._truncated_marker = config.truncated_marker
        self._compression_marker = config.compression_marker
        self._model: Optional[Model] = None

    async def trigger_add_messages(
        self,
        context: ModelContext,
        messages_to_add: List[BaseMessage],
        **kwargs,
    ) -> bool:
        total_tokens = self._count_context_window_tokens(
            system_messages=kwargs.get("system_messages"),
            context_messages=context.get_messages() + messages_to_add,
            tools=kwargs.get("tools"),
            context=context,
        )
        trigger_threshold = self._trigger_token_threshold(context)
        if total_tokens >= trigger_threshold:
            logger.info(
                f"[{self.processor_type()} triggered] estimated context window tokens {total_tokens} "
                f"reaches trigger_context_ratio {self._trigger_context_ratio} threshold {trigger_threshold}"
            )
            return True
        return False

    async def on_add_messages(
        self,
        context: ModelContext,
        messages_to_add: List[BaseMessage],
        **kwargs,
    ) -> Tuple[ContextEvent | None, List[BaseMessage]]:
        all_messages = context.get_messages() + messages_to_add
        force = kwargs.get("force", False)
        self._reset_compression_usage()

        compressed_messages = await self._compress_until_target(
            context_messages=all_messages,
            context=context,
            system_messages=kwargs.get("system_messages"),
            tools=kwargs.get("tools"),
            keep_recent=self._keep_recent_messages,
            force=force,
        )
        if compressed_messages == all_messages:
            return None, messages_to_add

        context.set_messages(compressed_messages)
        event = ContextEvent(
            event_type=self.processor_type(),
            messages_to_modify=list(range(len(all_messages))),
            compact_summary=self._extract_compact_summary(compressed_messages),
            compression_usage=self._current_compression_usage(),
        )
        return event, []

    async def trigger_get_context_window(
        self,
        context: ModelContext,
        context_window: ContextWindow,
        **kwargs,
    ) -> bool:
        total_tokens = self._count_context_window_tokens(
            system_messages=context_window.system_messages,
            context_messages=context_window.context_messages,
            tools=context_window.tools,
            context=context,
        )
        return total_tokens >= self._trigger_token_threshold(context)

    async def on_get_context_window(
        self,
        context: ModelContext,
        context_window: ContextWindow,
        **kwargs,
    ) -> Tuple[ContextEvent | None, ContextWindow]:
        self._reset_compression_usage()
        total_tokens = self._count_context_window_tokens(
            system_messages=context_window.system_messages,
            context_messages=context_window.context_messages,
            tools=context_window.tools,
            context=context,
        )
        if total_tokens <= self._target_total_tokens:
            return None, context_window

        compressed_messages = await self._compress_until_target(
            context_messages=context_window.context_messages,
            context=context,
            system_messages=context_window.system_messages,
            tools=context_window.tools,
            keep_recent=0,
        )
        original_context_len = len(context_window.context_messages)
        context_window.context_messages = compressed_messages
        
        context.set_messages(compressed_messages)
        event = ContextEvent(
            event_type=self.processor_type(),
            messages_to_modify=list(range(original_context_len)),
            compact_summary=self._extract_compact_summary(compressed_messages),
            compression_usage=self._current_compression_usage(),
        )
        return event, context_window

    async def _compress_until_target(
        self,
        context_messages: List[BaseMessage],
        context: ModelContext,
        *,
        system_messages: Optional[List[BaseMessage]],
        tools: Optional[List[ToolInfo]],
        keep_recent: int,
        force: bool = False,
    ) -> List[BaseMessage]:
        working = list(context_messages)
        if not force and self._is_under_context_window_budget(system_messages, working, tools, context):
            return working

        recursive_updated = await self._run_recursive_compression(
            messages=working,
            context=context,
            system_messages=system_messages,
            tools=tools,
            keep_recent=keep_recent,
        )
        if recursive_updated is not None:
            working = recursive_updated

        if self._is_under_context_window_budget(system_messages, working, tools, context):
            return working

        aggressive_keep_recent = await self._run_aggressive_phase(
            messages=working,
            context=context,
            system_messages=system_messages,
            tools=tools,
            keep_recent=keep_recent,
            target_tokens=self._second_pass_target_tokens,
            phase_name="aggressive_keep_recent",
        )
        if aggressive_keep_recent is not None:
            working = aggressive_keep_recent

        if self._is_under_context_window_budget(system_messages, working, tools, context):
            return working

        aggressive_full = await self._run_aggressive_phase(
            messages=working,
            context=context,
            system_messages=system_messages,
            tools=tools,
            keep_recent=0,
            target_tokens=self._third_pass_target_tokens,
            phase_name="aggressive_full_context",
        )
        if aggressive_full is not None:
            working = aggressive_full

        if self._is_under_context_window_budget(system_messages, working, tools, context):
            return working

        return self._truncate_to_target(
            context_messages=working,
            context=context,
            system_messages=system_messages,
            tools=tools,
        )

    async def _run_recursive_compression(
        self,
        messages: List[BaseMessage],
        context: ModelContext,
        *,
        system_messages: Optional[List[BaseMessage]],
        tools: Optional[List[ToolInfo]],
        keep_recent: int,
    ) -> Optional[List[BaseMessage]]:
        working = list(messages)
        changed = False

        compress_end = len(working) - keep_recent - 1
        if compress_end >= 0:
            raw_targets = self._build_raw_targets(working, compress_end)
            if raw_targets:
                updated = await self._apply_llm_phase(
                    messages=working,
                    context=context,
                    system_messages=system_messages,
                    tools=tools,
                    targets=raw_targets,
                    target_tokens=self._first_pass_target_tokens,
                    aggressive=False,
                    phase_name="l0_to_l1",
                    keep_recent_messages=keep_recent,
                )
                if updated is not None:
                    working = updated
                    changed = True

        while not self._is_under_context_window_budget(system_messages, working, tools, context):
            compress_end = len(working) - keep_recent - 1
            if compress_end < 0:
                break
            merge_targets = self._build_recursive_merge_targets(working, compress_end)
            if not merge_targets:
                break
            updated = await self._apply_llm_phase(
                messages=working,
                context=context,
                system_messages=system_messages,
                tools=tools,
                targets=merge_targets,
                target_tokens=self._first_pass_target_tokens,
                aggressive=False,
                phase_name=f"recursive_merge_l{merge_targets[0].current_level}_to_l{merge_targets[0].next_level}",
                keep_recent_messages=keep_recent,
            )
            if updated is None or updated == working:
                break
            working = updated
            changed = True

        return working if changed else None

    async def _run_aggressive_phase(
        self,
        messages: List[BaseMessage],
        context: ModelContext,
        *,
        system_messages: Optional[List[BaseMessage]],
        tools: Optional[List[ToolInfo]],
        keep_recent: int,
        target_tokens: int,
        phase_name: str,
    ) -> Optional[List[BaseMessage]]:
        compress_end = len(messages) - keep_recent - 1
        if compress_end < 0:
            return None

        targets = self._build_aggressive_targets(messages, compress_end)
        if not targets:
            return None
        return await self._apply_llm_phase(
            messages=messages,
            context=context,
            system_messages=system_messages,
            tools=tools,
            targets=targets,
            target_tokens=target_tokens,
            aggressive=True,
            phase_name=phase_name,
            keep_recent_messages=keep_recent,
        )

    def _build_raw_targets(self, messages: List[BaseMessage], compress_end: int) -> List[_CompressTarget]:
        targets: List[_CompressTarget] = []
        block_no = 1
        cursor = 0
        while cursor <= compress_end:
            if self._is_round_level_fallback_block(messages[cursor]):
                cursor = self._find_round_level_block_end(messages, cursor, compress_end) + 1
                continue

            start_idx = cursor
            end_idx, scope = self._find_l0_block_end(messages, start_idx, compress_end)
            if end_idx < start_idx:
                cursor += 1
                continue

            protected_end_idx = self._protect_tool_call_boundary(messages, start_idx, end_idx)
            if protected_end_idx != end_idx and protected_end_idx <= start_idx:
                break
            end_idx = protected_end_idx

            targets.append(
                _CompressTarget(
                    block_id=f"block_{block_no}",
                    scope=scope,
                    start_idx=start_idx,
                    end_idx=end_idx,
                    messages=messages[start_idx:end_idx + 1],
                    current_level=0,
                    next_level=1,
                )
            )
            block_no += 1
            cursor = end_idx + 1
        return targets

    @staticmethod
    def _protect_tool_call_boundary(
        messages: List[BaseMessage],
        start_idx: int,
        end_idx: int,
    ) -> int:
        if end_idx < start_idx:
            return end_idx

        protected_end_idx = end_idx
        tail_tool_ids = {
            getattr(message, "tool_call_id", None)
            for message in messages[end_idx + 1:]
            if isinstance(message, ToolMessage) and getattr(message, "tool_call_id", None)
        }
        if not tail_tool_ids:
            if isinstance(messages[end_idx], AssistantMessage) and messages[end_idx].tool_calls:
                return end_idx - 1
            return end_idx

        for idx in range(start_idx, end_idx + 1):
            message = messages[idx]
            if not isinstance(message, AssistantMessage) or not message.tool_calls:
                continue
            tool_call_ids = {
                getattr(tool_call, "id", None)
                for tool_call in message.tool_calls
                if getattr(tool_call, "id", None)
            }
            if tool_call_ids & tail_tool_ids:
                protected_end_idx = min(protected_end_idx, idx - 1)

        if (
            protected_end_idx == end_idx
            and isinstance(messages[end_idx], AssistantMessage)
            and messages[end_idx].tool_calls
        ):
            protected_end_idx = end_idx - 1
        return protected_end_idx

    def _find_l0_block_end(
        self,
        messages: List[BaseMessage],
        start_idx: int,
        compress_end: int,
    ) -> Tuple[int, str]:
        last_non_round_level_idx = start_idx - 1
        for idx in range(start_idx, compress_end + 1):
            if self._is_round_level_fallback_block(messages[idx]):
                break
            last_non_round_level_idx = idx
            if isinstance(messages[idx], AssistantMessage) and not messages[idx].tool_calls:
                return idx, "completed_react"
        return last_non_round_level_idx, "ongoing_react"

    def _build_aggressive_targets(self, messages: List[BaseMessage], compress_end: int) -> List[_CompressTarget]:
        raw_targets = self._build_raw_targets(messages, compress_end)
        if raw_targets:
            return raw_targets
        return self._collect_round_level_memory_targets(messages, compress_end)

    def _collect_round_level_memory_targets(
        self,
        messages: List[BaseMessage],
        compress_end: int,
    ) -> List[_CompressTarget]:
        targets: List[_CompressTarget] = []
        block_no = 1
        idx = 0
        while idx <= compress_end:
            if not self._is_round_level_fallback_block(messages[idx]):
                idx += 1
                continue
            end_idx = self._find_round_level_block_end(messages, idx, compress_end)
            level = max((self._get_compress_level(message) for message in messages[idx:end_idx + 1]), default=1)
            targets.append(
                _CompressTarget(
                    block_id=f"memory_{block_no}",
                    scope="existing_round_level_block",
                    start_idx=idx,
                    end_idx=end_idx,
                    messages=messages[idx:end_idx + 1],
                    current_level=level,
                    next_level=level + 1,
                )
            )
            block_no += 1
            idx = end_idx + 1
        return targets

    def _build_recursive_merge_targets(self, messages: List[BaseMessage], compress_end: int) -> List[_CompressTarget]:
        memory_targets = self._collect_round_level_memory_targets(messages, compress_end)
        if len(memory_targets) < 2:
            return []

        target_by_id = {target.block_id: target for target in memory_targets}
        effective_levels, candidate_level = self._resolve_effective_merge_levels(memory_targets)
        if candidate_level is None:
            return []

        selected_targets = [
            target_by_id[block_id]
            for block_id, effective_level in effective_levels.items()
            if effective_level == candidate_level
        ]
        selected_targets.sort(key=lambda item: item.start_idx)

        merged_targets: List[_CompressTarget] = []
        group: List[_CompressTarget] = []
        for target in selected_targets:
            if not group:
                group = [target]
                continue
            if target.start_idx == group[-1].end_idx + 1:
                group.append(target)
                continue
            if len(group) >= 2:
                merged_targets.append(
                    self._build_merge_target(
                        group,
                        messages,
                        candidate_level,
                        len(merged_targets) + 1,
                    )
                )
            group = [target]

        if len(group) >= 2:
            merged_targets.append(
                self._build_merge_target(
                    group,
                    messages,
                    candidate_level,
                    len(merged_targets) + 1,
                )
            )
        return merged_targets

    def _build_merge_target(
        self,
        group: List[_CompressTarget],
        messages: List[BaseMessage],
        candidate_level: int,
        group_no: int,
    ) -> _CompressTarget:
        return _CompressTarget(
            block_id=f"merge_{candidate_level}_{group_no}",
            scope="recursive_merge",
            start_idx=group[0].start_idx,
            end_idx=group[-1].end_idx,
            messages=messages[group[0].start_idx:group[-1].end_idx + 1],
            current_level=candidate_level,
            next_level=candidate_level + 1,
            source_block_count=len(group),
        )

    def _resolve_effective_merge_levels(
        self,
        memory_targets: List[_CompressTarget],
    ) -> Tuple[Dict[str, int], Optional[int]]:
        effective_levels = {target.block_id: max(target.current_level, 1) for target in memory_targets}
        while True:
            level_counts: Dict[int, int] = {}
            for level in effective_levels.values():
                level_counts[level] = level_counts.get(level, 0) + 1

            ordered_levels = sorted(level_counts)
            if not ordered_levels:
                return effective_levels, None

            highest_level = ordered_levels[-1]
            changed = False
            for level in ordered_levels:
                if level == highest_level or level_counts[level] != 1:
                    continue
                next_higher_level = next(candidate for candidate in ordered_levels if candidate > level)
                block_id = next(block_id for block_id, value in effective_levels.items() if value == level)
                effective_levels[block_id] = next_higher_level
                changed = True
                break
            if changed:
                continue

            candidate_level = next((level for level in ordered_levels if level_counts[level] >= 2), None)
            return effective_levels, candidate_level

    async def _apply_llm_phase(
        self,
        messages: List[BaseMessage],
        context: ModelContext,
        *,
        system_messages: Optional[List[BaseMessage]],
        tools: Optional[List[ToolInfo]],
        targets: List[_CompressTarget],
        target_tokens: int,
        aggressive: bool,
        phase_name: str,
        keep_recent_messages: int,
    ) -> Optional[List[BaseMessage]]:
        model_messages = self._prepare_round_compression_messages(
            context_messages=messages,
            targets=targets,
            context=context,
            phase_name=phase_name,
            target_tokens=target_tokens,
            aggressive=aggressive,
            keep_recent_messages=keep_recent_messages,
            system_messages=system_messages,
            tools=tools,
        )
        if model_messages is None:
            logger.warning(
                f"[RoundLevelCompressor] phase={phase_name} skipped "
                "because compression call budget is impossible"
            )
            return None

        try:
            response = await self._get_model().invoke(model_messages, output_parser=JsonOutputParser())
            self._record_compression_usage(response)
        except Exception as exc:
            raise build_error(
                StatusCode.MODEL_CALL_FAILED,
                error_msg=(
                    f"{self.processor_type()} failed to invoke compression model "
                    f"during phase={phase_name}"
                ),
                cause=exc,
            ) from exc
        replacements = await self._build_json_replacements(context, targets, response.parser_content)
        if not replacements and isinstance(response.content, str) and response.content.strip():
            fallback = await self._build_raw_fallback_replacement(context, targets, response.content.strip())
            if fallback:
                replacements = [fallback]

        if not replacements:
            logger.warning(f"[RoundLevelCompressor] phase={phase_name} produced no valid replacements")
            return None

        updated_messages = self._apply_replacements(messages, replacements)
        logger.info(
            f"[RoundLevelCompressor] phase={phase_name} context_window_tokens "
            f"{self._count_context_window_tokens(system_messages, messages, tools, context)} -> "
            f"{self._count_context_window_tokens(system_messages, updated_messages, tools, context)}"
        )
        return updated_messages

    def _prepare_round_compression_messages(
        self,
        *,
        context_messages: List[BaseMessage],
        targets: List[_CompressTarget],
        context: ModelContext,
        phase_name: str,
        target_tokens: int,
        aggressive: bool,
        keep_recent_messages: int,
        system_messages: Optional[List[BaseMessage]],
        tools: Optional[List[ToolInfo]],
    ) -> Optional[List[BaseMessage]]:
        system_prompt = self._aggressive_prompt if aggressive else self._first_prompt
        prompt_text = self._build_compression_user_prompt(
            context_messages=context_messages,
            targets=targets,
            context=context,
            phase_name=phase_name,
            target_tokens=target_tokens,
            keep_recent_messages=keep_recent_messages,
            system_messages=system_messages,
            tools=tools,
        )
        if self._is_under_compression_call_budget(system_prompt, prompt_text, context):
            return [SystemMessage(content=system_prompt), UserMessage(content=prompt_text)]

        compact_prompt = self._truncate_prompt_to_budget(system_prompt, prompt_text, context)
        if compact_prompt is None:
            return None
        return [SystemMessage(content=system_prompt), UserMessage(content=compact_prompt)]

    def _build_compression_user_prompt(
        self,
        *,
        context_messages: List[BaseMessage],
        targets: List[_CompressTarget],
        context: ModelContext,
        phase_name: str,
        target_tokens: int,
        keep_recent_messages: int,
        system_messages: Optional[List[BaseMessage]],
        tools: Optional[List[ToolInfo]],
    ) -> str:
        target_indices = {
            index
            for target in targets
            for index in range(target.start_idx, target.end_idx + 1)
        }
        first_target_idx = min(target.start_idx for target in targets)
        last_target_idx = max(target.end_idx for target in targets)
        protected_recent_start = max(len(context_messages) - keep_recent_messages, last_target_idx + 1)

        reference_lines = [
            self._serialize_message(index, message)
            for index, message in enumerate(context_messages)
            if index not in target_indices and index < protected_recent_start
        ]

        target_lines: List[str] = []
        for target in targets:
            target_lines.extend(
                [
                    f"[Block: {target.block_id}]",
                    f"- scope: {target.scope}",
                    f"- replace_range: [{target.start_idx}, {target.end_idx}]",
                    f"- current_level: l{target.current_level}",
                    f"- next_level: l{target.next_level}",
                    f"- source_block_count: {target.source_block_count}",
                ]
            )
            for offset, message in enumerate(target.messages, start=target.start_idx):
                target_lines.append(self._serialize_message(offset, message))
            target_lines.append("")

        recent_lines = [
            self._serialize_message(index, message)
            for index, message in enumerate(context_messages[protected_recent_start:], start=protected_recent_start)
        ]
        current_window_tokens = self._count_context_window_tokens(
            system_messages=system_messages,
            context_messages=context_messages,
            tools=tools,
            context=context,
        )
        compression_call_budget_limit = effective_context_budget(
            context,
            model_config=self._round_config.model,
            call_budget=self._compression_call_max_tokens,
        )

        return "\n".join(
            [
                "[Compression Task]",
                f"- phase: {phase_name}",
                f"- target_summary_tokens: {target_tokens}",
                f"- keep_recent_messages: {keep_recent_messages}",
                f"- selected_blocks: {len(targets)}",
                f"- current_context_window_tokens: {current_window_tokens}",
                f"- compression_call_budget_limit: {compression_call_budget_limit}",
                f"- selected_range: [{first_target_idx}, {last_target_idx}]",
                "",
                "[Reference Context]",
                "\n".join(reference_lines) or "(none)",
                "",
                "[Selected Targets]",
                "\n".join(target_lines).rstrip() or "(none)",
                "",
                "[Protected Recent Context]",
                "\n".join(recent_lines) or "(none)",
                "",
                "[Output Contract]",
                "- Return valid JSON only.",
                "- Use schema: {\"blocks\": [{\"block_id\": \"...\", \"summary\": \"...\"}]}",
                "- Emit exactly one summary for each selected block_id.",
                "- Do not emit undeclared block_ids.",
                "- Target content must appear only in [Selected Targets], not elsewhere.",
                "- Preserve the user's original requirements, constraints, "
                "acceptance criteria, and preferences as completely as possible.",
                "- Do not weaken or over-compress the user's original request unless absolutely necessary.",
                "- If a selected block is ongoing_react, include a distinct "
                "`User Requirements` section tied to the unfinished work.",
                "- If a selected block is completed_react, explicitly preserve "
                "both `User Requirements` and `Final Result` when they exist.",
            ]
        )

    def _truncate_prompt_to_budget(
        self,
        system_prompt: str,
        prompt_text: str,
        context: ModelContext,
    ) -> Optional[str]:
        minimum_prompt = "[Compression Task]\n...[TRUNCATED]...\n[Output Contract]\nReturn valid JSON only."
        if not self._is_under_compression_call_budget(system_prompt, minimum_prompt, context):
            return None

        low, high = 0, len(prompt_text)
        best = minimum_prompt
        while low <= high:
            middle = (low + high) // 2
            candidate = self._build_head_tail_truncated_text(prompt_text, middle)
            if self._is_under_compression_call_budget(system_prompt, candidate, context):
                best = candidate
                low = middle + 1
            else:
                high = middle - 1
        return best

    async def _build_json_replacements(
        self,
        context: ModelContext,
        targets: List[_CompressTarget],
        parser_content: Any,
    ) -> List[Tuple[int, int, List[BaseMessage]]]:
        if not self._is_valid_blocks_payload(parser_content):
            return []

        block_map: Dict[str, str] = {}
        for item in parser_content["blocks"]:
            if not isinstance(item, dict):
                continue
            block_id = item.get("block_id")
            summary = item.get("summary")
            if not isinstance(block_id, str) or not block_id:
                continue
            if not isinstance(summary, str):
                continue
            summary = summary.strip()
            if not summary:
                continue
            block_map[block_id] = summary

        replacements: List[Tuple[int, int, List[BaseMessage]]] = []
        for target in targets:
            summary = block_map.get(target.block_id)
            if not summary:
                continue
            replacement_message = await self._build_memory_message(summary, target, context)
            if replacement_message is None:
                continue
            base_replacement_messages = [replacement_message]
            if not self._is_replacement_under_budget(context, base_replacement_messages):
                continue
            if not self._has_compression_benefit(context, target.messages, base_replacement_messages):
                continue
            replacement_messages = list(base_replacement_messages)
            replacement_messages.extend(build_team_collaboration_reinjected_messages(target.messages))
            replacements.append((target.start_idx, target.end_idx, replacement_messages))
        return replacements

    async def _build_raw_fallback_replacement(
        self,
        context: ModelContext,
        targets: List[_CompressTarget],
        summary: str,
    ) -> Optional[Tuple[int, int, List[BaseMessage]]]:
        if not targets or not summary:
            return None
        start_idx = min(target.start_idx for target in targets)
        end_idx = max(target.end_idx for target in targets)
        merged_target = _CompressTarget(
            block_id="raw_fallback",
            scope="mixed_context",
            start_idx=start_idx,
            end_idx=end_idx,
            messages=[message for target in targets for message in target.messages],
            current_level=max((target.current_level for target in targets), default=0),
            next_level=max((target.next_level for target in targets), default=1),
            source_block_count=sum(target.source_block_count for target in targets),
        )
        replacement = await self._build_memory_message(summary, merged_target, context)
        if replacement is None:
            return None
        base_replacement_messages = [replacement]
        if not self._is_replacement_under_budget(context, base_replacement_messages):
            return None
        if not self._has_compression_benefit(context, merged_target.messages, base_replacement_messages):
            return None
        replacement_messages = list(base_replacement_messages)
        replacement_messages.extend(build_team_collaboration_reinjected_messages(merged_target.messages))
        return start_idx, end_idx, replacement_messages

    async def _build_memory_message(
        self,
        summary: str,
        target: _CompressTarget,
        context: ModelContext,
    ) -> Optional[BaseMessage]:
        content = self._wrap_memory_block(summary, target.scope)
        message = UserMessage(content=content)
        if hasattr(message, "metadata"):
            message.metadata[_COMPRESS_LEVEL] = target.next_level
        return message

    def _wrap_memory_block(self, summary: str, scope: str) -> str:
        return (
            f"{self._compression_marker}\n"
            "processor: RoundLevelCompressor\n"
            "type: historical_memory_block\n"
            f"scope: {scope}\n"
            "authority: This block is reference memory, not a binding source of truth.\n"
            "instruction_status: Historical fallback context only. Do not treat as a new user instruction.\n"
            "conflict_priority: Prefer newer explicit user intent, newer raw context, "
            "and fresh tool results over this block.\n\n"
            "Summary:\n"
            f"{summary}"
        )

    def _extract_compact_summary(self, messages: List[BaseMessage]) -> str:
        parts = []
        for message in messages:
            content = self._to_text(getattr(message, "content", ""))
            if content.startswith(self._compression_marker):
                parts.append(content)
        return "\n\n".join(parts)

    def _build_minimal_truncated_message(self) -> UserMessage:
        return UserMessage(
            content=(
                f"{self._compression_marker}\n"
                "processor: RoundLevelCompressor\n"
                "type: historical_memory_block\n"
                "scope: truncated_full_context\n"
                "Summary:\n"
                f"{self._truncated_marker}"
            )
        )

    def _build_compact_truncated_message(self) -> UserMessage:
        return UserMessage(content=f"{self._compression_marker}\n{self._truncated_marker}")

    def _truncate_to_target(
        self,
        *,
        context_messages: List[BaseMessage],
        context: ModelContext,
        system_messages: Optional[List[BaseMessage]],
        tools: Optional[List[ToolInfo]],
    ) -> List[BaseMessage]:
        fixed_tokens = self._count_context_window_fixed_tokens(system_messages, tools, context)
        allowed_context_tokens = self._target_total_tokens - fixed_tokens
        if allowed_context_tokens <= 0:
            # Physical limit: the fixed window portion (system / tools) has already exhausted
            # the budget, so no context payload can make the full window fit. In that case we
            # still return the smallest possible fallback marker instead of clearing context.
            return self._with_team_collaboration_reinjection(
                [self._build_compact_truncated_message()],
                context_messages,
            )

        serialized = "\n".join(
            self._serialize_message(index, message)
            for index, message in enumerate(context_messages)
        )
        if not serialized:
            return context_messages

        low, high = 0, len(serialized)
        best_messages: List[BaseMessage] = []
        while low <= high:
            middle = (low + high) // 2
            candidate_content = self._wrap_memory_block(
                self._build_head_tail_truncated_text(serialized, middle),
                "truncated_full_context",
            )
            candidate_messages = [UserMessage(content=candidate_content)]
            candidate_tokens = self._count_context_window_tokens(
                system_messages,
                candidate_messages,
                tools,
                context,
            )
            if candidate_tokens <= self._target_total_tokens:
                best_messages = candidate_messages
                low = middle + 1
            else:
                high = middle - 1

        if best_messages:
            return self._with_team_collaboration_reinjection(best_messages, context_messages)
        minimal_message = self._build_minimal_truncated_message()
        minimal_tokens = self._count_context_window_tokens(
            system_messages,
            [minimal_message],
            tools,
            context,
        )
        if minimal_tokens <= self._target_total_tokens:
            return self._with_team_collaboration_reinjection([minimal_message], context_messages)
        # If even the minimal structured fallback block does not fit, fall back to the
        # tightest marker-only truncated block. This keeps the "never clear context just
        # because truncation is hard" contract while acknowledging the remaining budget
        # is nearly exhausted by fixed window content.
        return self._with_team_collaboration_reinjection(
            [self._build_compact_truncated_message()],
            context_messages,
        )

    @staticmethod
    def _with_team_collaboration_reinjection(
        replacement_messages: List[BaseMessage],
        source_messages: List[BaseMessage],
    ) -> List[BaseMessage]:
        return list(replacement_messages) + build_team_collaboration_reinjected_messages(source_messages)

    def _build_head_tail_truncated_text(self, text: str, kept_chars: int) -> str:
        if kept_chars <= 0:
            return self._truncated_marker

        head_chars = max(int(kept_chars * self._truncate_head_ratio), 0)
        tail_chars = max(kept_chars - head_chars, 0)
        head = text[:head_chars]
        tail = text[-tail_chars:] if tail_chars > 0 else ""
        if head and tail:
            return f"{head}\n{self._truncated_marker}\n{tail}"
        return head or tail or self._truncated_marker

    def _count_context_window_tokens(
        self,
        system_messages: Optional[List[BaseMessage]],
        context_messages: Optional[List[BaseMessage]],
        tools: Optional[List[ToolInfo]],
        context: ModelContext,
    ) -> int:
        token_counter = context.token_counter()
        total = 0
        all_messages = list(system_messages or []) + list(context_messages or [])
        if token_counter:
            try:
                total += token_counter.count_messages(all_messages)
                total += token_counter.count_tools(list(tools or []))
                return total
            except Exception as exc:  # pragma: no cover
                logger.warning(f"[{self.processor_type()}] token_counter failed, fallback to estimate: {exc}")
        total += sum(self._estimate_content_tokens(getattr(message, "content", "")) for message in all_messages)
        total += sum(self._estimate_content_tokens(self._serialize_tool(tool)) for tool in (tools or []))
        return total

    def _count_context_window_fixed_tokens(
        self,
        system_messages: Optional[List[BaseMessage]],
        tools: Optional[List[ToolInfo]],
        context: ModelContext,
    ) -> int:
        return self._count_context_window_tokens(system_messages, [], tools, context)

    def _count_compression_call_tokens(
        self,
        system_prompt: str,
        prompt_text: str,
        context: ModelContext,
    ) -> int:
        token_counter = context.token_counter()
        messages = [SystemMessage(content=system_prompt), UserMessage(content=prompt_text)]
        if token_counter:
            try:
                return token_counter.count_messages(messages)
            except Exception as exc:  # pragma: no cover
                logger.warning(f"[{self.processor_type()}] compression token counting fallback: {exc}")
        return sum(self._estimate_content_tokens(message.content) for message in messages)

    def _is_under_context_window_budget(
        self,
        system_messages: Optional[List[BaseMessage]],
        context_messages: Optional[List[BaseMessage]],
        tools: Optional[List[ToolInfo]],
        context: ModelContext,
    ) -> bool:
        total_tokens = self._count_context_window_tokens(
            system_messages,
            context_messages,
            tools,
            context,
        )
        return total_tokens <= self._target_total_tokens

    def _trigger_token_threshold(self, context: ModelContext) -> int:
        budget = effective_context_budget(context, model_config=self._round_config.model)
        return max(int(budget * self._trigger_context_ratio), 1)

    def _is_under_compression_call_budget(
        self,
        system_prompt: str,
        prompt_text: str,
        context: ModelContext,
    ) -> bool:
        total_tokens = self._count_compression_call_tokens(
            system_prompt,
            prompt_text,
            context,
        )
        budget = effective_context_budget(
            context,
            model_config=self._round_config.model,
            call_budget=self._compression_call_max_tokens,
        )
        return total_tokens <= budget

    def _has_compression_benefit(
        self,
        context: ModelContext,
        original_messages: List[BaseMessage],
        replacement_messages: List[BaseMessage],
    ) -> bool:
        original_tokens = self._count_message_tokens(original_messages, context)
        replacement_tokens = self._count_message_tokens(replacement_messages, context)
        return original_tokens > replacement_tokens

    def _is_replacement_under_budget(
        self,
        context: ModelContext,
        replacement_messages: List[BaseMessage],
    ) -> bool:
        budget = effective_context_budget(
            context,
            model_config=self._round_config.model,
            call_budget=self._compression_call_max_tokens,
        )
        return self._count_message_tokens(replacement_messages, context) <= budget

    def _count_message_tokens(self, messages: List[BaseMessage], context: ModelContext) -> int:
        token_counter = context.token_counter()
        if token_counter:
            try:
                return token_counter.count_messages(messages)
            except Exception as exc:  # pragma: no cover - defensive fallback
                logger.warning(
                    f"[{self.processor_type()}] token_counter failed, fallback to estimate: {exc}"
                )
        return sum(self._estimate_content_tokens(getattr(message, "content", "")) for message in messages)

    def _serialize_message(self, index: int, message: BaseMessage) -> str:
        parts = [f"[{index}] role={message.role}"]
        if isinstance(message, AssistantMessage) and message.tool_calls:
            parts.append("tool_calls=" + ", ".join(tool_call.name for tool_call in message.tool_calls))
        if isinstance(message, ToolMessage):
            parts.append(f"tool_call_id={message.tool_call_id}")
        level = self._get_compress_level(message)
        if level > 0:
            parts.append(f"compress_level=l{level}")
        parts.append(f"content={self._to_text(message.content)}")
        return " | ".join(parts)

    @staticmethod
    def _serialize_tool(tool: ToolInfo) -> str:
        return json.dumps(tool.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _estimate_content_tokens(content: Any) -> int:
        if isinstance(content, str):
            return len(content) // 3
        try:
            return len(json.dumps(content, ensure_ascii=False)) // 3
        except TypeError:
            return len(str(content)) // 3

    @staticmethod
    def _to_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        return str(content)

    def _is_round_level_fallback_block(self, message: BaseMessage) -> bool:
        return isinstance(message, UserMessage) and self._to_text(message.content).startswith(self._compression_marker)

    def _find_round_level_block_end(self, messages: List[BaseMessage], start: int, compress_end: int) -> int:
        end_idx = start
        while (
            end_idx + 1 <= compress_end
            and isinstance(messages[end_idx + 1], AssistantMessage)
            and not messages[end_idx + 1].tool_calls
            and self._looks_like_ack(messages[end_idx + 1])
        ):
            end_idx += 1
        return end_idx

    @staticmethod
    def _looks_like_ack(message: BaseMessage) -> bool:
        return isinstance(message, AssistantMessage) and RoundLevelCompressor._to_text(message.content).strip() in {
            "Understood. I have recorded this compressed context.",
        }

    @staticmethod
    def _is_valid_blocks_payload(parser_content: Any) -> bool:
        return isinstance(parser_content, dict) and isinstance(parser_content.get("blocks"), list)

    @staticmethod
    def _apply_replacements(
        messages: List[BaseMessage],
        replacements: List[Tuple[int, int, List[BaseMessage]]],
    ) -> List[BaseMessage]:
        updated = list(messages)
        for start_idx, end_idx, replacement_messages in sorted(replacements, key=lambda item: item[0], reverse=True):
            updated = ContextUtils.replace_messages(updated, replacement_messages, start_idx, end_idx)
        return updated

    def _get_compress_level(self, message: BaseMessage) -> int:
        if hasattr(message, "metadata") and isinstance(message.metadata, dict):
            return int(message.metadata.get(_COMPRESS_LEVEL, 0) or 0)
        if self._is_round_level_fallback_block(message):
            return 1
        return 0

    def _get_model(self) -> Model:
        if self._model is None:
            self._model = Model(self._round_config.model_client, self._round_config.model)
        return self._model

    def load_state(self, state: Dict[str, Any]) -> None:
        pass

    def save_state(self) -> Dict[str, Any]:
        return {}
