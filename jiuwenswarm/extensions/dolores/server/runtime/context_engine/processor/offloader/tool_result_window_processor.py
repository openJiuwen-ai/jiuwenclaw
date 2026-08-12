# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import Any, List, Optional, Tuple

from pydantic import BaseModel, Field

from jiuwenswarm.extensions.dolores.server.runtime.context_engine.base import ModelContext
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.context.context_utils import ContextUtils
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.context_engine import ContextEngine
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.base import ContextEvent
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.offloader.tool_result_budget_processor import (
    ToolResultBudgetProcessor,
)
from openjiuwen.core.foundation.llm import BaseMessage, ToolMessage


class ToolResultWindowProcessorConfig(BaseModel):
    """Keep only the most recent ``keep_last_k`` results of selected tools in context.

    This processor maintains a single global sliding window across all tools in
    ``tool_names``: every time a matching tool result is added, results that fall
    outside the newest ``keep_last_k`` are offloaded (persisted) so the message
    history only ever shows the last ``keep_last_k`` full results.

    The persist logic is inherited from :class:`ToolResultBudgetProcessor` --
    the original tool output is written to the workspace offload directory and
    replaced in context by a ``<persisted-output>`` preview placeholder.
    """

    tool_names: list[str] = Field(
        default_factory=list,
        description="Tool names whose results are managed by the sliding window. Empty means no-op.",
    )
    """Tools eligible for windowed offload. A result matches when its tool name is in this list."""

    keep_last_k: int = Field(
        default=3,
        gt=0,
        description="Number of most recent matching tool results kept in context with full content.",
    )
    """Window size. Matching results older than the newest ``keep_last_k`` are offloaded."""

    trim_size: int = Field(
        default=3000,
        gt=0,
        description="Number of leading characters kept in the context placeholder after offloading.",
    )
    """Preview length retained in the placeholder message."""

    offload_file_prefix: str = Field(
        default="ToolResultWindowProcessor",
        description="Processor-specific filename prefix used under the workspace offload directory.",
    )
    """Filename prefix used to separate files produced by this processor."""

    messages_threshold: int | None = Field(default=None, gt=0)
    """Compatibility placeholder; this processor does not use message-count triggering."""

    messages_to_keep: int | None = Field(default=None, gt=0)
    """Compatibility placeholder; this processor keeps a per-tool-result window, not a message tail."""


@ContextEngine.register_processor()
class ToolResultWindowProcessor(ToolResultBudgetProcessor):
    """Offload older results of selected tools, keeping at most the newest ``keep_last_k`` in context."""

    @property
    def config(self) -> ToolResultWindowProcessorConfig:
        return self._config

    def _validate_config(self) -> None:
        # Window processor has no size-threshold invariants; skip the base offloader checks.
        return

    async def trigger_add_messages(
        self,
        context: ModelContext,
        messages_to_add: List[BaseMessage],
        **kwargs: Any,
    ) -> bool:
        all_messages = context.get_messages() + messages_to_add
        matched = self._matched_indices(all_messages)
        if len(matched) <= self.config.keep_last_k:
            return False
        outside_window = matched[: -self.config.keep_last_k]
        return any(not self._is_already_offloaded(all_messages[idx]) for idx in outside_window)

    async def on_add_messages(
        self,
        context: ModelContext,
        messages_to_add: List[BaseMessage],
        **kwargs: Any,
    ) -> Tuple[ContextEvent | None, List[BaseMessage]]:
        self.sys_operation = kwargs.get("sys_operation")

        context_messages = context.get_messages() + messages_to_add
        context_size = len(context)
        updated_messages = list(context_messages)

        matched = self._matched_indices(updated_messages)
        if len(matched) <= self.config.keep_last_k:
            return None, messages_to_add

        modified_indices: List[int] = []
        for idx in matched[: -self.config.keep_last_k]:
            message = updated_messages[idx]
            if self._is_already_offloaded(message):
                continue
            updated_messages[idx] = await self._offload_tool_message(message, context)
            modified_indices.append(idx)

        if not modified_indices:
            return None, messages_to_add

        context.set_messages(updated_messages[:context_size])
        event = ContextEvent(
            event_type=self.processor_type(),
            messages_to_modify=sorted(modified_indices),
        )
        return event, updated_messages[context_size:]

    def _matched_indices(self, messages: List[BaseMessage]) -> List[int]:
        allowlist = set(self.config.tool_names)
        if not allowlist:
            return []
        return [
            idx for idx, message in enumerate(messages) if self._is_target_tool_result(message, messages, allowlist)
        ]

    def _is_target_tool_result(
        self,
        message: BaseMessage,
        context_messages: List[BaseMessage],
        allowlist: set[str],
    ) -> bool:
        if not isinstance(message, ToolMessage):
            return False
        if not isinstance(getattr(message, "content", None), str):
            return False
        tool_name = self._resolve_target_tool_name(message, context_messages)
        return bool(tool_name and any(tool_name.endswith(suffix) for suffix in allowlist))

    @staticmethod
    def _resolve_target_tool_name(
        message: ToolMessage,
        context_messages: List[BaseMessage],
    ) -> Optional[str]:
        tool_name = ContextUtils.resolve_tool_name_from_message(message, context_messages)
        if tool_name:
            return tool_name
        name = getattr(message, "name", None)
        return name if isinstance(name, str) and name else None
