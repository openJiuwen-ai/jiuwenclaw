from __future__ import annotations

from pydantic import Field

from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.forked.compressor.base import (
    PrefixCompactProcessorConfig,
    PrefixCompactProcessor,
    PrefixCompactSpan,
    adjust_keep_recent_for_tool_boundaries,
    find_last_real_user_message_index,
)
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.forked.compressor.prompts.prompts import CURRENT_COMPACT_PROMPT
from openjiuwen.core.foundation.llm import BaseMessage


MEMORY_BLOCK_CURRENT_OPEN = "<memory_block_current>"
MEMORY_BLOCK_CURRENT_CLOSE = "</memory_block_current>"


DEFAULT_CURRENT_COMPRESSION_PROMPT = CURRENT_COMPACT_PROMPT


class CurrentRoundCompressorConfig(PrefixCompactProcessorConfig):
    trigger_context_ratio: float = Field(default=0.8, gt=0.0, lt=1.0)
    min_target_context_ratio: float = Field(default=0.1, ge=0.0, lt=1.0)
    keep_recent_messages: int = Field(default=0, ge=0)


class CurrentRoundCompressor(PrefixCompactProcessor):
    memory_block_open = MEMORY_BLOCK_CURRENT_OPEN
    memory_block_close = MEMORY_BLOCK_CURRENT_CLOSE
    default_prompt = DEFAULT_CURRENT_COMPRESSION_PROMPT
    processor_label = "CurrentRoundCompressor"
    memory_block_meaning = (
        "This is a compressed summary of work already performed after the latest user request. "
        "It is not a new user request. "
        "Use it to recover completed analysis, tool calls, code changes, test results, "
        "and next steps for the current task."
    )
    memory_block_conflict_policy = (
        "Newer raw messages, fresh tool results, and the latest user instructions override this summary."
    )
    reinject_builder_names = ["plan_mode", "plan", "task_status", "todo", "skills", "read_file"]

    def __init__(self, config: CurrentRoundCompressorConfig):
        super().__init__(config)

    @property
    def config(self) -> CurrentRoundCompressorConfig:
        return self._config

    def _build_span(self, messages: list[BaseMessage]) -> PrefixCompactSpan:
        effective_keep = adjust_keep_recent_for_tool_boundaries(messages, self.config.keep_recent_messages)
        split_index = max(len(messages) - effective_keep, 0)
        last_user_index = find_last_real_user_message_index(messages)
        if last_user_index < 0:
            return PrefixCompactSpan([], [], list(messages[split_index:]))
        target_start = last_user_index + 1
        if split_index <= target_start:
            return PrefixCompactSpan(list(messages[:target_start]), [], list(messages[target_start:]))
        return PrefixCompactSpan(
            preserved_prefix=list(messages[:target_start]),
            messages_to_compress=list(messages[target_start:split_index]),
            protected_tail=list(messages[split_index:]),
        )
