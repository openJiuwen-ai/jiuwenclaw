# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Tuple

from pydantic import BaseModel, Field

from jiuwenswarm.extensions.dolores.server.runtime.context_engine.base import ModelContext
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.context_engine import ContextEngine
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.context.context_utils import ContextUtils
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.base import ContextEvent, ContextProcessor
from openjiuwen.core.foundation.llm import BaseMessage, ToolMessage


MICRO_COMPACT_CLEARED_MARKER = "[Old tool result content cleared]"


class MicroCompactProcessorConfig(BaseModel):
    """Clear stale tool results while keeping recent ones per tool."""

    trigger_threshold: int = Field(
        default=5,
        gt=0,
        description=(
            "Clear stale results only after a tool has more than this many clearable results beyond the kept tail."
        ),
    )
    compactable_tool_names: list[str] = Field(
        default=["grep", "glob", "read_file", "web_search", "web_fetch"],
        description="Tool names whose older ToolMessage contents may be cleared.",
    )
    keep_recent_per_tool: int = Field(
        default=15,
        ge=0,
        description="Number of most-recent ToolMessage contents preserved for each compactable tool.",
    )
    cleared_marker: str = Field(
        default=MICRO_COMPACT_CLEARED_MARKER,
        description="Replacement content used when clearing stale ToolMessage contents.",
    )


@ContextEngine.register_processor()
class MicroCompactProcessor(ContextProcessor):
    @property
    def config(self) -> MicroCompactProcessorConfig:
        return self._config

    async def trigger_add_messages(
        self,
        context: ModelContext,
        messages_to_add: List[BaseMessage],
        **kwargs: Any,
    ) -> bool:
        all_messages = context.get_messages() + messages_to_add
        if not self._api_round(all_messages):
            return False
        return self._has_any_tool_exceed_threshold(all_messages)

    async def on_add_messages(
        self,
        context: ModelContext,
        messages_to_add: List[BaseMessage],
        **kwargs: Any,
    ) -> Tuple[ContextEvent | None, List[BaseMessage]]:
        all_messages = context.get_messages() + messages_to_add
        indices_to_clear = self._collect_flat_indices_for_compact(
            all_messages,
            force=kwargs.get("force", False),
        )

        if not indices_to_clear:
            return None, messages_to_add

        modified_indices: List[int] = []
        for index in indices_to_clear:
            message = all_messages[index]
            if not isinstance(message, ToolMessage):
                continue
            if message.content == self.config.cleared_marker:
                continue
            all_messages[index] = message.model_copy(update={"content": self.config.cleared_marker})
            modified_indices.append(index)

        context.set_messages(all_messages)
        return ContextEvent(event_type=self.processor_type(), messages_to_modify=modified_indices), []

    def _collect_compactable_indices_by_tool(
        self,
        messages: List[BaseMessage],
    ) -> dict[str, List[int]]:
        allowed_names = set(self.config.compactable_tool_names)
        result: dict[str, List[int]] = defaultdict(list)

        for index, message in enumerate(messages):
            if not isinstance(message, ToolMessage):
                continue
            if message.content == self.config.cleared_marker:
                continue
            tool_name = ContextUtils.resolve_tool_name_from_message(message, messages)
            if tool_name in allowed_names:
                result[tool_name].append(index)

        return dict(result)

    def _has_any_tool_exceed_threshold(
        self,
        messages: List[BaseMessage],
    ) -> bool:
        grouped_indices = self._collect_compactable_indices_by_tool(messages)
        return any(
            len(indices) > self._config.trigger_threshold + self._config.keep_recent_per_tool
            for indices in grouped_indices.values()
        )

    def _collect_flat_indices_for_compact(
        self,
        messages: List[BaseMessage],
        *,
        force: bool = False,
    ) -> List[int]:
        grouped = self._collect_compactable_indices_by_tool(messages)
        result = []
        for indices in grouped.values():
            threshold = self._config.keep_recent_per_tool if force else (
                self._config.trigger_threshold + self._config.keep_recent_per_tool
            )
            if len(indices) > threshold:
                result.extend(indices[:-self._config.keep_recent_per_tool] if self._config.keep_recent_per_tool
                              else indices)
        return result

    def load_state(self, state: Dict[str, Any]) -> None:
        pass

    def save_state(self) -> Dict[str, Any]:
        return {}
