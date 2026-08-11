# coding: utf-8

import fnmatch
import json
import os
import uuid
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from openjiuwen.core.common.logging import context_engine_logger as logger
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.base import ContextWindow, ModelContext
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.context.context_utils import ContextUtils
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.forked.base import ContextEvent, ContextProcessor
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.forked.compressor.support.util import (
    count_messages_tokens,
    resolve_context_max,
    resolve_ratio_token_threshold,
)
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.forked.offloader.rule_compression import RuleCompressionPipeline
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.schema.messages import OffloadMixin
from openjiuwen.core.foundation.llm import BaseMessage, ToolMessage

OMIT_STRING = "..."
MESSAGE_OFFLOADER_DEBUG_LOG_DIR_ENV = "OPENJIUWEN_MESSAGE_OFFLOADER_DEBUG_LOG_DIR"
MESSAGE_OFFLOADER_DEBUG_LOG_FILE = "message_offloader_debug.jsonl"
OffloadStrategy = Literal["rule_then_truncate", "rule_only", "truncate_only"]

_OFFLOAD_STRATEGY: OffloadStrategy = "rule_then_truncate"
_OFFLOAD_PREVIEW_HEAD_TAIL_CHARS = 2000


class MessageSummaryOffloaderConfig(BaseModel):
    """Minimal configuration for context-relative tool-result offloading."""

    model_config = ConfigDict(extra="ignore")

    add_message_threshold_ratio: float = Field(default=0.1, gt=0)
    """Context-token-capacity ratio above which a newly added tool message is processed."""

    ttl_seconds: int = Field(default=300, ge=0)
    """Idle time between LLM context-window requests before TTL processing is eligible."""

    ttl_context_occupancy_ratio: float = Field(
        default=0.5,
        gt=0,
    )
    """Context-token-capacity ratio above which TTL processing is eligible."""

    ttl_message_threshold_ratio: float = Field(
        default=0.05,
        gt=0,
    )
    """Context-token-capacity ratio above which one TTL tool message is processed."""

    protected_tool_names: list[str] = Field(
        default_factory=lambda: ["read_file"]
    )
    """Tool names, or ``tool:argument-pattern`` entries, that must remain inline."""

    enable_debug_dump: bool = Field(default=False)
    """Persist rule-compression and offload debug records when enabled."""

    debug_dump_dir: str | None = Field(default=None)
    """Optional directory for MessageSummaryOffloader debug records."""


class MessageSummaryOffloader(ContextProcessor):
    def __init__(self, config: MessageSummaryOffloaderConfig):
        super().__init__(config)
        self._rule_pipeline = RuleCompressionPipeline(
            enable_dump=bool(getattr(config, "enable_debug_dump", False)),
            dump_dir=getattr(config, "debug_dump_dir", None),
        )

    async def trigger_add_messages(
        self,
        context: ModelContext,
        messages_to_add: list[BaseMessage],
        **kwargs: Any,
    ) -> bool:
        _ = kwargs
        threshold_tokens = self._add_message_token_threshold(context)
        triggered = False
        for index, message in enumerate(messages_to_add):
            if not isinstance(message, ToolMessage) or not isinstance(getattr(message, "content", None), str):
                continue
            content_tokens = self._count_messages_tokens(context, [message])
            if content_tokens > threshold_tokens:
                triggered = True
                self._write_debug_log(
                    context,
                    event="add_message_triggered_offload",
                    pass_name="add",
                    message=message,
                    message_index=index,
                    threshold_tokens=threshold_tokens,
                    content_tokens=content_tokens,
                    content_chars=len(message.content),
                    original_content=message.content,
                )
        return triggered

    async def on_add_messages(
        self,
        context: ModelContext,
        messages_to_add: list[BaseMessage],
        **kwargs: Any,
    ) -> tuple[Optional[ContextEvent], list[BaseMessage]]:
        all_messages = context.get_messages() + messages_to_add
        threshold_tokens = self._add_message_token_threshold(context)
        max_chars = self._add_message_max_chars(context)
        processed = list(messages_to_add)
        event = ContextEvent(event_type=self.processor_type())
        context_size = len(context)

        for index, message in enumerate(messages_to_add):
            replacement = await self._process_added_message(
                message,
                context,
                all_messages,
                threshold_tokens,
                max_chars,
                **kwargs,
            )
            if replacement is message:
                continue
            processed[index] = replacement
            event.messages_to_modify.append(context_size + index)

        if event.messages_to_modify:
            return event, processed
        return None, processed

    async def _process_added_message(
        self,
        message: BaseMessage,
        context: ModelContext,
        context_messages: list[BaseMessage],
        threshold_tokens: int,
        max_chars: int,
        **kwargs: Any,
    ) -> BaseMessage:
        if not self._is_processable_tool_message(message, context_messages):
            return message
        if self._count_messages_tokens(context, [message]) <= threshold_tokens:
            return message

        if self._should_try_rule_compression():
            self._write_debug_log(
                context,
                event="rule_compression_triggered",
                pass_name="add",
                message=message,
                threshold_tokens=threshold_tokens,
                max_chars=max_chars,
                original_content=message.content,
            )
            compressed = self._rule_pipeline.compress(
                message,
                context,
                pass_name="add",
                max_chars=max_chars,
                context_messages=context_messages,
            )
            if self._is_rule_compressed_message(compressed):
                self._write_debug_log(
                    context,
                    event="rule_compression_completed",
                    pass_name="add",
                    message=compressed,
                    threshold_tokens=threshold_tokens,
                    max_chars=max_chars,
                    original_content=message.content,
                    compressed_content=compressed.content,
                    compressed_metadata=getattr(compressed, "metadata", None) or {},
                )
                return await self._finalize_rule_compressed_message(
                    compressed,
                    context,
                    original_message=message,
                    max_chars=max_chars,
                    offload_original=True,
                    truncate_offloaded_preview=self._should_truncate_rule_preview(),
                    **kwargs,
                )

        if self._should_fallback_to_plain_offload():
            return await self._offload_message(
                message,
                context,
                original_message=message,
                **kwargs,
            )
        return message

    async def _finalize_rule_compressed_message(
        self,
        message: BaseMessage,
        context: ModelContext,
        *,
        original_message: BaseMessage,
        max_chars: int,
        offload_original: bool,
        truncate_offloaded_preview: bool,
        **kwargs: Any,
    ) -> BaseMessage:
        if not offload_original and len(message.content) <= max_chars:
            return message

        offloaded = await self._offload_message(
            message,
            context,
            original_message=original_message,
            **kwargs,
        )
        if not truncate_offloaded_preview:
            return offloaded
        if self._should_keep_full_rule_compressed_preview(message):
            return offloaded
        if len(offloaded.content) <= max_chars:
            return offloaded
        return offloaded.model_copy(
            update={
                "content": self._truncate_preserving_offload_marker(
                    offloaded.content,
                    max_chars,
                )
            }
        )

    async def trigger_get_context_window(
        self,
        context: ModelContext,
        context_window: ContextWindow,
        **kwargs: Any,
    ) -> bool:
        _ = context_window
        now = self._rule_pipeline.current_time()
        previous_access = context.last_context_window_access_at()
        context.set_last_context_window_access_at(now)
        if self.config.ttl_seconds <= 0:
            return False
        if previous_access is None:
            return False
        if now - previous_access < self.config.ttl_seconds:
            return False
        return self._context_occupancy_tokens(context) >= self._ttl_occupancy_token_threshold(context)

    async def on_get_context_window(
        self,
        context: ModelContext,
        context_window: ContextWindow,
        **kwargs: Any,
    ) -> tuple[Optional[ContextEvent], ContextWindow]:
        messages = context.get_messages()
        processed = list(messages)
        event = ContextEvent(event_type=self.processor_type())
        ttl_message_threshold_tokens = self._ttl_message_token_threshold(context)
        ttl_max_chars = self._ttl_message_max_chars(context)

        for index, message in enumerate(processed):
            if not self._is_processable_tool_message(message, processed):
                continue
            if self._count_messages_tokens(context, [message]) <= ttl_message_threshold_tokens:
                continue
            replacement = await self._process_ttl_message(
                message,
                context,
                ttl_message_threshold_tokens,
                ttl_max_chars,
                **kwargs,
            )
            if replacement is message:
                continue
            processed[index] = replacement
            event.messages_to_modify.append(index)

        if event.messages_to_modify:
            context.set_messages(processed)
            self._replace_window_messages(context_window, processed)
            return event, context_window
        return None, context_window

    async def _process_ttl_message(
        self,
        message: ToolMessage,
        context: ModelContext,
        ttl_message_threshold_tokens: int,
        max_chars: int,
        **kwargs: Any,
    ) -> BaseMessage:
        self._write_debug_log(
            context,
            event="ttl_message_triggered_offload",
            pass_name="ttl",
            message=message,
            threshold_tokens=ttl_message_threshold_tokens,
            max_chars=max_chars,
            content_tokens=self._count_messages_tokens(context, [message]),
            content_chars=len(message.content),
            original_content=message.content,
        )
        if not self._should_try_rule_compression():
            if not self._should_fallback_to_plain_offload():
                return message
            return await self._offload_message(
                message,
                context,
                original_message=message,
                **kwargs,
            )
        if self._is_rule_compressed_message(message):
            return message
        self._write_debug_log(
            context,
            event="rule_compression_triggered",
            pass_name="ttl",
            message=message,
            threshold_tokens=ttl_message_threshold_tokens,
            max_chars=max_chars,
            original_content=message.content,
        )
        processed = self._rule_pipeline.compress(
            message,
            context,
            pass_name="ttl",
            max_chars=max_chars,
            force=True,
            context_messages=context.get_messages(),
        )
        if self._is_rule_compressed_message(processed):
            self._write_debug_log(
                context,
                event="rule_compression_completed",
                pass_name="ttl",
                message=processed,
                threshold_tokens=ttl_message_threshold_tokens,
                max_chars=max_chars,
                original_content=message.content,
                compressed_content=processed.content,
                compressed_metadata=getattr(processed, "metadata", None) or {},
            )
            return await self._finalize_rule_compressed_message(
                processed,
                context,
                original_message=message,
                max_chars=max_chars,
                offload_original=True,
                truncate_offloaded_preview=self._should_truncate_rule_preview(),
                **kwargs,
            )
        if len(processed.content) <= max_chars:
            return processed
        if not self._should_fallback_to_plain_offload():
            return message
        return await self._offload_message(
            processed,
            context,
            original_message=message,
            **kwargs,
        )

    async def _offload_message(
        self,
        message: BaseMessage,
        context: ModelContext,
        *,
        original_message: BaseMessage,
        **kwargs: Any,
    ) -> BaseMessage:
        content = message.content
        if not self._is_rule_compressed_message(message):
            content = self._head_tail_preview(
                content,
                description="Content truncated and offloaded.",
            )
        elif self._rule_compression_requests_original_offload(message):
            content = f"{content}\n[Original content offloaded.]"
        extra_fields = message.model_dump()
        extra_fields.pop("role", None)
        extra_fields.pop("content", None)
        offload_handle, offload_path = self._new_offload_handle_and_path(context)
        offloaded = await self.offload_messages(
            role=message.role,
            content=content,
            messages=[original_message],
            context=context,
            offload_handle=offload_handle,
            offload_path=offload_path,
            **extra_fields,
            **kwargs,
        )
        if offloaded is not None:
            self._write_debug_log(
                context,
                event="message_offloaded",
                message=offloaded,
                original_message=original_message,
                offload_handle=getattr(offloaded, "offload_handle", None),
                offload_type=getattr(offloaded, "offload_type", None),
                offload_path=offload_path,
                offloaded_content=getattr(offloaded, "content", None),
            )
        return offloaded or original_message

    def _is_processable_tool_message(
        self,
        message: BaseMessage,
        context_messages: list[BaseMessage],
    ) -> bool:
        return (
            isinstance(message, ToolMessage)
            and isinstance(message.content, str)
            and not isinstance(message, OffloadMixin)
            and not self._is_protected_tool_message(message, context_messages)
        )

    @staticmethod
    def _is_rule_compressed_message(message: BaseMessage) -> bool:
        return bool((getattr(message, "metadata", None) or {}).get("rule_compressed_at"))

    @staticmethod
    def _truncate_preserving_offload_marker(content: str, max_chars: int) -> str:
        marker_start = content.rfind("[[OFFLOAD:")
        if marker_start < 0:
            return content[:max_chars]

        marker = content[marker_start:].strip()
        body = content[:marker_start].rstrip()
        marker_separator = "\n...\n"
        body_budget = max_chars - len(marker) - len(marker_separator)
        if body_budget <= 0:
            return marker
        if len(body) <= body_budget:
            return f"{body}{marker_separator}{marker}"

        body_separator = "\n...\n"
        tail_budget = body_budget // 2
        head_budget = body_budget - len(body_separator) - tail_budget
        if head_budget <= 0:
            return f"{body[-body_budget:].lstrip()}{marker_separator}{marker}"
        return (
            f"{body[:head_budget].rstrip()}"
            f"{body_separator}"
            f"{body[-tail_budget:].lstrip()}"
            f"{marker_separator}"
            f"{marker}"
        )

    @staticmethod
    def _rule_compression_requests_original_offload(message: BaseMessage) -> bool:
        metadata = getattr(message, "metadata", None) or {}
        details = metadata.get("rule_compression_details") or {}
        return (
            metadata.get("rule_compression_type") == "GIT_DIFF"
            and bool(details.get("should_offload_original"))
            and not isinstance(message, OffloadMixin)
        )

    def _should_keep_full_rule_compressed_preview(self, message: BaseMessage) -> bool:
        return self._rule_compression_requests_original_offload(message)

    def _context_occupancy_tokens(self, context: ModelContext) -> int:
        return self._count_messages_tokens(context, context.get_messages())

    def _ttl_occupancy_token_threshold(self, context: ModelContext) -> int:
        return resolve_ratio_token_threshold(self._context_max(context), self._ttl_context_occupancy_ratio())

    def _add_message_token_threshold(self, context: ModelContext) -> int:
        return resolve_ratio_token_threshold(self._context_max(context), self._add_message_threshold_ratio())

    def _ttl_message_token_threshold(self, context: ModelContext) -> int:
        return resolve_ratio_token_threshold(self._context_max(context), self._ttl_message_threshold_ratio())

    def _add_message_max_chars(self, context: ModelContext) -> int:
        return self._character_budget(context, self._add_message_threshold_ratio())

    def _ttl_message_max_chars(self, context: ModelContext) -> int:
        return self._character_budget(context, self._ttl_message_threshold_ratio())

    def _character_budget(self, context: ModelContext, ratio: float) -> int:
        return max(int(self._context_max(context) * 3 * ratio), 1)

    def _context_max(self, context: ModelContext) -> int:
        return resolve_context_max(context)

    def _count_messages_tokens(self, context: ModelContext, messages: list[BaseMessage]) -> int:
        return count_messages_tokens(messages, context.token_counter(), self.processor_type())

    def _head_tail_preview(self, content: str, *, description: str) -> str:
        keep_chars = self._offload_preview_head_tail_chars()
        if keep_chars <= 0:
            return f"[{description}]"
        if len(content) <= keep_chars * 2:
            return content
        return (
            f"{content[:keep_chars]}"
            f"\n{OMIT_STRING} [{description}] {OMIT_STRING}\n"
            f"{content[-keep_chars:]}"
        )

    def _offload_strategy(self) -> OffloadStrategy:
        return _OFFLOAD_STRATEGY

    def _should_try_rule_compression(self) -> bool:
        return self._offload_strategy() != "truncate_only"

    def _should_fallback_to_plain_offload(self) -> bool:
        return self._offload_strategy() != "rule_only"

    def _should_truncate_rule_preview(self) -> bool:
        return self._offload_strategy() == "rule_then_truncate"

    def _add_message_threshold_ratio(self) -> float:
        return float(getattr(self.config, "add_message_threshold_ratio", 0.2))

    def _ttl_context_occupancy_ratio(self) -> float:
        return float(getattr(self.config, "ttl_context_occupancy_ratio", 0.5))

    def _ttl_message_threshold_ratio(self) -> float:
        return float(getattr(self.config, "ttl_message_threshold_ratio", 0.1))

    def _offload_preview_head_tail_chars(self) -> int:
        return _OFFLOAD_PREVIEW_HEAD_TAIL_CHARS

    def _write_debug_log(
        self,
        context: ModelContext,
        *,
        event: str,
        message: BaseMessage | None = None,
        **payload: Any,
    ) -> None:
        if not getattr(self.config, "enable_debug_dump", False):
            return
        log_path = self._debug_log_path(context)
        record = {
            "event": event,
            "session_id": self._safe_context_value(context, "session_id", "unknown_session"),
            "context_id": self._safe_context_value(context, "context_id", "unknown_context"),
        }
        if message is not None:
            record.update(
                {
                    "role": getattr(message, "role", None),
                    "tool_call_id": getattr(message, "tool_call_id", None),
                    "message_metadata": getattr(message, "metadata", None) or {},
                }
            )
        record.update(payload)
        try:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as handle:
                json.dump(record, handle, ensure_ascii=False, default=str)
                handle.write("\n")
        except Exception as exc:
            logger.warning("[MessageSummaryOffloader] failed to write debug log: %s", exc)

    def _debug_log_path(self, context: ModelContext) -> str:
        return os.path.join(self._debug_log_dir(context), MESSAGE_OFFLOADER_DEBUG_LOG_FILE)

    def _debug_log_dir(self, context: ModelContext) -> str:
        debug_dump_dir = getattr(self.config, "debug_dump_dir", None)
        if debug_dump_dir:
            return os.path.abspath(self._expand_debug_dir_template(debug_dump_dir, context))
        env_dir = os.getenv(MESSAGE_OFFLOADER_DEBUG_LOG_DIR_ENV)
        if env_dir:
            return os.path.abspath(self._expand_debug_dir_template(env_dir, context))
        workspace_dir = self._workspace_dir(context)
        if workspace_dir:
            return os.path.join(
                workspace_dir,
                "context",
                f"{self._safe_context_value(context, 'session_id', 'unknown_session')}_context",
                "message_offloader_debug_logs",
            )
        return os.path.abspath(os.path.join("context", "message_offloader_debug_logs"))

    def _expand_debug_dir_template(self, path: str, context: ModelContext) -> str:
        if "{session_id}" not in path and "{context_id}" not in path:
            return path
        return path.format(
            session_id=self._safe_filename_part(self._safe_context_value(context, "session_id", "unknown_session")),
            context_id=self._safe_filename_part(self._safe_context_value(context, "context_id", "unknown_context")),
        )

    @staticmethod
    def _workspace_dir(context: ModelContext) -> str:
        workspace_dir = getattr(context, "workspace_dir", None)
        if not callable(workspace_dir):
            return ""
        try:
            return os.path.abspath(str(workspace_dir() or ""))
        except Exception:
            return ""

    @staticmethod
    def _safe_filename_part(value: str) -> str:
        safe = "".join(char if char.isalnum() or char in "_.-" else "-" for char in value).strip("-")
        return safe[:80] or "unknown"

    @staticmethod
    def _safe_context_value(context: ModelContext, method_name: str, fallback: str) -> str:
        method = getattr(context, method_name, None)
        if not callable(method):
            return fallback
        try:
            value = method()
        except Exception:
            return fallback
        return str(value or fallback)

    @staticmethod
    def _replace_window_messages(
        context_window: ContextWindow,
        processed_messages: list[BaseMessage],
    ) -> None:
        replacements = {
            MessageSummaryOffloader._message_id(message): message
            for message in processed_messages
            if MessageSummaryOffloader._message_id(message)
        }
        context_window.context_messages = [
            replacements.get(MessageSummaryOffloader._message_id(message), message)
            for message in context_window.context_messages
        ]

    @staticmethod
    def _message_id(message: BaseMessage) -> str | None:
        return (getattr(message, "metadata", None) or {}).get("context_message_id")

    def _new_offload_handle_and_path(self, context: ModelContext) -> tuple[str, str | None]:
        offload_handle = uuid.uuid4().hex
        workspace_dir = context.workspace_dir()
        if not workspace_dir:
            return offload_handle, None
        file_name = f"{self.processor_type()}_{offload_handle}.json"
        return (
            offload_handle,
            os.path.join(
                workspace_dir,
                "context",
                f"{context.session_id()}_context",
                "offload",
                file_name,
            ),
        )

    def _is_protected_tool_message(
        self,
        message: BaseMessage,
        context_messages: list[BaseMessage],
    ) -> bool:
        if not isinstance(message, ToolMessage):
            return False
        tool_call = ContextUtils.resolve_tool_call_from_message(message, context_messages)
        if not tool_call:
            return False
        tool_name = ContextUtils.extract_tool_name(tool_call)
        tool_args = self._extract_tool_args(tool_call)
        for protected in getattr(self.config, "protected_tool_names", ["read_file"]):
            if ":" not in protected:
                if tool_name == protected:
                    return True
                continue
            protected_tool, protected_pattern = protected.split(":", 1)
            if tool_name == protected_tool and self._match_pattern(tool_args, protected_pattern):
                return True
        return False

    @staticmethod
    def _extract_tool_args(tool_call: Any) -> dict[str, Any]:
        if isinstance(tool_call, dict):
            function = tool_call.get("function")
            arguments = function.get("arguments") if isinstance(function, dict) else None
            arguments = arguments if arguments is not None else tool_call.get("arguments")
        else:
            function = getattr(tool_call, "function", None)
            arguments = (
                getattr(function, "arguments", None) if function is not None else getattr(tool_call, "arguments", None)
            )
        if isinstance(arguments, dict):
            return arguments
        if isinstance(arguments, str):
            try:
                parsed = json.loads(arguments)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @staticmethod
    def _match_pattern(args: dict[str, Any], pattern: str) -> bool:
        return any(isinstance(value, str) and fnmatch.fnmatch(value, pattern) for value in args.values())

    def load_state(self, state: dict[str, Any]) -> None:
        _ = state

    def save_state(self) -> dict[str, Any]:
        return {}
