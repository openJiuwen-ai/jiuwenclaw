# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import os
import uuid
from typing import Any, Dict, List, Literal, Tuple

from pydantic import BaseModel, Field

from jiuwenswarm.extensions.dolores.server.runtime.context_engine.base import ModelContext
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.context.context_utils import ContextUtils
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.context_engine import ContextEngine
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.base import ContextEvent
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.offloader.message_offloader import (
    MessageOffloader,
)
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.schema.messages import OffloadToolMessage
from openjiuwen.core.foundation.llm import BaseMessage, ToolMessage

PERSISTED_OUTPUT_TAG = "<persisted-output>"
PERSISTED_OUTPUT_CLOSING_TAG = "</persisted-output>"


class ToolResultBudgetProcessorConfig(BaseModel):
    """Per-round budget control for large tool results.

    This processor does not use the base MessageOffloader trigger/range logic.
    It keeps `messages_threshold` and `messages_to_keep` only as compatibility
    placeholders for callers that handle offloader-like configs generically.
    """

    tokens_threshold: int = Field(
        default=50000,
        gt=0,
        description="Per-round tool result token budget. A dialogue round exceeding it triggers offload.",
    )
    """Maximum accumulated tool-result tokens allowed in one dialogue round."""

    large_message_threshold: int = Field(
        default=10000,
        gt=0,
        description="Minimum size for a single tool message to be eligible for offload.",
    )
    """Tool messages at or below this size are kept in context."""

    trim_size: int = Field(
        default=3000,
        gt=0,
        description="Number of leading characters kept in the context placeholder after offloading.",
    )
    """Preview length retained in the placeholder message."""

    tool_name_allowlist: list[str] | None = Field(
        default=None,
        description="Tool names that should never be offloaded regardless of size.",
    )
    """Tool names protected from offloading. Set to None to allow all tools."""

    offload_message_type: list[Literal["tool"]] = Field(
        default=["tool"],
        description="Compatibility field. Only tool messages are supported by this processor.",
    )
    """Compatibility field retained for offloader-like config shape; only ['tool'] is meaningful."""

    offload_file_prefix: str = Field(
        default="ToolResultBudgetProcessor",
        description="Processor-specific filename prefix used under the workspace offload directory.",
    )
    """Filename prefix used to separate files produced by this processor."""

    messages_threshold: int | None = Field(default=None, gt=0)
    """Compatibility field; this processor does not use message-count triggering."""

    messages_to_keep: int | None = Field(default=None, gt=0)
    """Compatibility field; this processor does not preserve a newest-message tail by count."""


@ContextEngine.register_processor()
class ToolResultBudgetProcessor(MessageOffloader):
    """Offload oversized tool results round-by-round until each round fits budget."""

    @property
    def config(self) -> ToolResultBudgetProcessorConfig:
        return self._config

    async def trigger_add_messages(
        self,
        context: ModelContext,
        messages_to_add: List[BaseMessage],
        **kwargs: Any,
    ) -> bool:
        all_messages = context.get_messages() + messages_to_add
        return any(self._round_budget_exceeded(all_messages, context))

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
        modified_indices: List[int] = []

        for round_range in self._iter_round_ranges(updated_messages):
            changed, new_indices = await self._shrink_round_to_budget(
                updated_messages,
                round_range,
                context,
            )
            if changed:
                modified_indices.extend(new_indices)

        if not modified_indices:
            return None, messages_to_add

        context.set_messages(updated_messages[:context_size])
        event = ContextEvent(
            event_type=self.processor_type(),
            messages_to_modify=sorted(set(modified_indices)),
        )
        return event, updated_messages[context_size:]

    def _round_budget_exceeded(
        self,
        messages: List[BaseMessage],
        context: ModelContext,
    ) -> List[Tuple[int, int]]:
        exceeded: List[Tuple[int, int]] = []
        for start_idx, end_idx in self._iter_round_ranges(messages):
            total_size = self._round_tool_result_size(messages, start_idx, end_idx, context)
            if total_size > self.config.tokens_threshold:
                candidates = self._collect_round_candidates(messages, start_idx, end_idx, context)
                if candidates:
                    exceeded.append((start_idx, end_idx))
        return exceeded

    def _iter_round_ranges(self, messages: List[BaseMessage]) -> List[Tuple[int, int]]:
        rounds = list(reversed(ContextUtils.find_all_dialogue_round(messages)))
        if not rounds:
            return []
        ranges: List[Tuple[int, int]] = []
        for user_idx, assistant_idx in rounds:
            start_idx = user_idx
            end_idx = assistant_idx if assistant_idx is not None else len(messages) - 1
            if start_idx is None or end_idx is None or start_idx > end_idx:
                continue
            ranges.append((start_idx, end_idx))
        return ranges

    def _round_tool_result_size(
        self,
        messages: List[BaseMessage],
        start_idx: int,
        end_idx: int,
        context: ModelContext,
    ) -> int:
        size = 0
        for idx in range(start_idx, end_idx + 1):
            msg = messages[idx]
            if isinstance(msg, ToolMessage):
                size += self._message_size(msg, context)
        return size

    def _message_size(self, message: ToolMessage, context: ModelContext) -> int:
        token_counter = context.token_counter()
        if token_counter is not None:
            try:
                return token_counter.count_messages([message])
            except Exception:
                return self._estimate_size(getattr(message, "content", ""))
        return self._estimate_size(getattr(message, "content", ""))

    @staticmethod
    def _estimate_size(content: Any) -> int:
        from jiuwenswarm.extensions.dolores.server.runtime.context_engine.context.context_utils import ContextUtils as _ContextUtils

        return _ContextUtils.estimate_tokens(content)

    async def _shrink_round_to_budget(
        self,
        messages: List[BaseMessage],
        round_range: Tuple[int, int],
        context: ModelContext,
    ) -> Tuple[bool, List[int]]:
        start_idx, end_idx = round_range
        modified_indices: List[int] = []
        changed = False

        while self._round_tool_result_size(messages, start_idx, end_idx, context) > self.config.tokens_threshold:
            candidates = self._collect_round_candidates(messages, start_idx, end_idx, context)
            if not candidates:
                break
            candidates.sort(key=lambda item: item[1], reverse=True)
            target_idx, _ = candidates[0]
            offloaded = await self._offload_tool_message(messages[target_idx], context)
            messages[target_idx] = offloaded
            modified_indices.append(target_idx)
            changed = True

        return changed, modified_indices

    def _collect_round_candidates(
        self,
        messages: List[BaseMessage],
        start_idx: int,
        end_idx: int,
        context: ModelContext,
    ) -> List[Tuple[int, int]]:
        candidates: List[Tuple[int, int]] = []
        for idx in range(start_idx, end_idx + 1):
            msg = messages[idx]
            if self._should_offload_message(msg, messages, context):
                candidates.append((idx, self._message_size(msg, context)))
        return candidates

    def _should_offload_message(
        self,
        message: BaseMessage,
        context_messages: List[BaseMessage],
        context: ModelContext,
    ) -> bool:
        if not isinstance(message, ToolMessage):
            return False
        if self._is_already_offloaded(message):
            return False
        if not isinstance(getattr(message, "content", None), str):
            return False
        if self._is_allowlisted_tool_message(message, context_messages):
            return False
        return self._message_size(message, context) > self.config.large_message_threshold

    def _is_allowlisted_tool_message(
        self,
        message: BaseMessage,
        context_messages: List[BaseMessage],
    ) -> bool:
        allowlist = set(self.config.tool_name_allowlist or [])
        if not allowlist:
            return False
        tool_name = self._resolve_tool_name_from_message(message, context_messages)
        return bool(tool_name and tool_name in allowlist)

    @staticmethod
    def _is_already_offloaded(message: ToolMessage) -> bool:
        return isinstance(message, OffloadToolMessage)

    def _new_offload_handle_and_path(self, context: ModelContext) -> tuple[str, str | None]:
        offload_handle = uuid.uuid4().hex
        session_id = context.session_id()
        workspace_dir = context.workspace_dir()
        file_prefix = self.config.offload_file_prefix or self.processor_type()
        file_name = f"{file_prefix}_{offload_handle}.json"
        if workspace_dir:
            offload_path = os.path.join(workspace_dir, "context", f"{session_id}_context", "offload", file_name)
            return offload_handle, offload_path
        return offload_handle, None

    async def _offload_tool_message(
        self,
        message: ToolMessage,
        context: ModelContext,
    ) -> ToolMessage:
        content = message.content
        if not isinstance(content, str):
            return message

        offload_handle, offload_path = self._new_offload_handle_and_path(context)

        preview = content[: self.config.trim_size]
        has_more = len(content) > self.config.trim_size
        persisted_content = self._build_persisted_output_message(
            original_size=len(content),
            offload_handle="pending",
            preview=preview,
            has_more=has_more,
        )

        offload_message = await self.offload_messages(
            role="tool",
            content=persisted_content,
            messages=[message],
            context=context,
            tool_call_id=message.tool_call_id,
            name=message.name,
            metadata=dict(getattr(message, "metadata", {}) or {}),
            sys_operation=self.sys_operation,
            offload_handle=offload_handle,
            offload_path=offload_path,
        )
        if offload_message is not None:
            actual_handle = getattr(offload_message, "offload_handle", "unknown")
            actual_offload_type = getattr(offload_message, "offload_type", "unknown")
            offload_message.content = self._build_persisted_output_message(
                original_size=len(content),
                offload_handle=f"[[OFFLOAD: handle={actual_handle}, type={actual_offload_type}, path={offload_path}]]",
                preview=preview,
                has_more=has_more,
            )
            return offload_message  # type: ignore[return-value]
        return message

    @staticmethod
    def _build_persisted_output_message(
        *,
        original_size: int,
        offload_handle: str,
        preview: str,
        has_more: bool,
    ) -> str:
        suffix = "\n...\n" if has_more else "\n"
        return (
            f"{PERSISTED_OUTPUT_TAG}\n"
            f"Output too large ({original_size} bytes)."
            f"\n{offload_handle}\n"
            f"Preview (first {len(preview)} chars):\n"
            f"{preview}{suffix}"
            f"{PERSISTED_OUTPUT_CLOSING_TAG}"
        )

    def load_state(self, state: Dict[str, Any]) -> None:
        return

    def save_state(self) -> Dict[str, Any]:
        return {}
