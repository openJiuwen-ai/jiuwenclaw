# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from typing import Awaitable, Callable, List, Optional, Tuple, Dict, Any

from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.context.context_utils import ContextUtils
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.context.processor_state_recorder import (
    ContextProcessorStateInput,
    ContextProcessorStateRecorder,
)
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.base import ContextProcessor
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.token.base import TokenCounter
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.schema.config import ContextEngineConfig
from openjiuwen.core.foundation.llm import BaseMessage, AssistantMessage
from openjiuwen.core.foundation.kv_cache import first_changed_index
from openjiuwen.core.foundation.tool import ToolInfo
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.base import ContextWindowChange, ModelContext, ContextWindow, ContextStats
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.context.message_buffer import ContextMessageBuffer, OffloadMessageBuffer
from openjiuwen.core.runner.callback import lazy_callback_framework as _fw
from openjiuwen.core.runner.callback.events import ContextEvents


_ACTIVE_COMPRESSION_RESULT_BUSY = "busy"
_ACTIVE_COMPRESSION_RESULT_COMPRESSED = "compressed"
_ACTIVE_COMPRESSION_RESULT_NOOP = "noop"

# How long a processor-lock acquire may block before it is worth a warning.
# The lock itself has no timeout: a holder wedged on a never-completing await
# silently hangs every later add_messages / get_context_window on the same
# context. The guarded acquire keeps retrying (behavior is unchanged once the
# holder releases) but logs a WARNING per interval so the stall is visible.
_PROCESSOR_LOCK_WARN_INTERVAL_SECONDS = 120.0


class SessionModelContext(ModelContext):
    def __init__(
            self,
            context_id: str,
            session_id: str,
            config: ContextEngineConfig,
            *,
            history_messages: List[BaseMessage] = None,
            processors: List[ContextProcessor] = None,
            token_counter: TokenCounter = None,
            session_ref=None,
            workspace=None,
            sys_operation=None,
            window_mutators: List[
                Callable[[ModelContext, ContextWindow], Awaitable[ContextWindow]]
            ] = None,
    ):
        self._message_id = 0
        ContextUtils.validate_messages(history_messages)
        history_messages = ContextUtils.ensure_context_message_ids(history_messages or [])
        self._context_id = context_id
        self._session_id = session_id
        self._message_buffer = ContextMessageBuffer(history_messages or [], config.max_context_message_num)
        self._default_window_size = config.default_window_message_num
        self._context_window_tokens = config.context_window_tokens
        self._model_name = config.model_name
        self._model_context_window_tokens = ContextUtils.build_model_context_window_tokens(
            config.model_context_window_tokens,
            enable_openrouter_model_context_window_tokens=config.enable_openrouter_model_context_window_tokens,
            openrouter_request_timeout=config.openrouter_request_timeout,
        )
        self._workspace = workspace
        self._sys_operation = sys_operation
        self._window_mutators = window_mutators if window_mutators is not None else []
        self._session_ref = session_ref
        self._default_dialogue_round = config.default_window_round_num
        self._token_counter = token_counter
        self._processors = processors
        self._processor_state_recorder = ContextProcessorStateRecorder(
            session_id=session_id,
            context_id=context_id,
            get_session_ref=self.get_session_ref,
            token_counter=token_counter,
        )
        self._processor_lock = asyncio.Lock()
        self._active_compression_in_progress = False
        self._last_context_window_access_at: float | None = None
        self._last_llm_bound_context_window: ContextWindow | None = None
        self._offload_message_buffer = OffloadMessageBuffer()
        self._configure_offload_message_buffer()

    def _configure_offload_message_buffer(self) -> None:
        self._offload_message_buffer.set_sys_operation(self._sys_operation)
        self._offload_message_buffer.set_workspace_info(
            self._workspace.root_path if self._workspace else "",
            self._session_id,
        )

    def __len__(self):
        return self._message_buffer.size()

    def session_id(self) -> str:
        return self._session_id

    def context_id(self) -> str:
        return self._context_id

    def last_context_window_access_at(self) -> float | None:
        return self._last_context_window_access_at

    def set_last_context_window_access_at(self, timestamp: float) -> None:
        self._last_context_window_access_at = timestamp

    def context_window_tokens(self) -> int:
        return ContextUtils.resolve_context_max(
            model_name=self._model_name,
            fallback_context_window_tokens=self._context_window_tokens,
            model_context_window_tokens=self._model_context_window_tokens,
        )

    def workspace_dir(self) -> str:
        if self._workspace:
            return self._workspace.root_path
        return ""

    def get_session_ref(self):
        return self._session_ref

    def set_session_ref(self, session_ref):
        self._session_ref = session_ref

    def _resolve_context_model_name(self, kwargs: dict) -> Optional[str]:
        return kwargs.get("model_name") or self._model_name

    async def _acquire_processor_lock(self, caller: str) -> None:
        """Acquire ``_processor_lock``, warning periodically while blocked.

        Args:
            caller: Short label of the acquiring operation, included in the
                warning so a stall points at who is blocked (the holder is
                then found via the surrounding log context / a stack dump).
        """
        waited = 0.0
        while True:
            try:
                await asyncio.wait_for(
                    self._processor_lock.acquire(),
                    timeout=_PROCESSOR_LOCK_WARN_INTERVAL_SECONDS,
                )
                if waited:
                    logger.warning(
                        "context %s (session %s): %s acquired the processor lock "
                        "after waiting %.0fs",
                        self._context_id,
                        self._session_id,
                        caller,
                        waited,
                    )
                return
            except asyncio.TimeoutError:
                waited += _PROCESSOR_LOCK_WARN_INTERVAL_SECONDS
                logger.warning(
                    "context %s (session %s): %s has been blocked %.0fs waiting for "
                    "the processor lock; its holder may be wedged",
                    self._context_id,
                    self._session_id,
                    caller,
                    waited,
                )

    @asynccontextmanager
    async def _guarded_processor_lock(self, caller: str):
        """``async with`` wrapper around :meth:`_acquire_processor_lock`."""
        await self._acquire_processor_lock(caller)
        try:
            yield
        finally:
            self._processor_lock.release()

    @_fw.emit_after(ContextEvents.CONTEXT_UPDATED, result_key="messages")
    async def add_messages(
            self,
            messages: BaseMessage | List[BaseMessage],
            **kwargs
    ) -> List[BaseMessage]:
        ContextUtils.validate_messages(messages)
        messages_to_add = messages if isinstance(messages, list) else [messages]
        messages_to_add = ContextUtils.ensure_context_message_ids(messages_to_add)
        kwargs.setdefault("sys_operation", self._sys_operation)

        # Active compaction wins the lock, so concurrent appends bypass passive processors and only enqueue messages.
        if self._active_compression_in_progress and self._processor_lock.locked():
            logger.info("skip passive compression because active compression is already in progress")
            self._message_buffer.add_back(messages_to_add)
            return messages_to_add

        async with self._guarded_processor_lock("add_messages"):
            _, messages_to_add = await self._run_add_processors(
                messages_to_add,
                force=False,
                processor_types=None,
                **kwargs,
            )
            self._message_buffer.add_back(messages_to_add)
            return messages_to_add

    async def compress_context(
            self,
            processor_types: List[str] = None,
            **kwargs,
    ) -> str | dict[str, Any]:
        return_state = bool(kwargs.pop("return_state", False))
        if self._processor_lock.locked():
            history_start = len(self._processor_state_recorder.history())
            logger.info("skip active compression because context processor is already running")
            await self._build_and_emit_compression_state(ContextProcessorStateInput(
                operation_id=uuid.uuid4().hex,
                status="skipped",
                phase="active_compress",
                trigger="manual",
                processor=None,
                reason="busy",
                before_messages=self.get_messages(),
                after_messages=None,
                started_at=time.time(),
                ended_at=time.time(),
                error=None,
                messages_to_modify=[],
                force=True,
                context_max=ContextUtils.resolve_context_max(
                    model_name=self._resolve_context_model_name(kwargs),
                    fallback_context_window_tokens=self._context_window_tokens,
                    model_context_window_tokens=self._model_context_window_tokens,
                ),
            ))
            return self._build_active_compression_result(
                _ACTIVE_COMPRESSION_RESULT_BUSY,
                return_state,
                history_start=history_start,
            )

        await self._acquire_processor_lock("compress_context")
        try:
            self._active_compression_in_progress = True
            history_start = len(self._processor_state_recorder.history())
            kwargs.setdefault("sys_operation", self._sys_operation)
            offload_processors = self._select_processors(
                processor_types=processor_types,
                offload_only=True,
            )
            compression_processors = self._select_processors(
                processor_types=processor_types,
                compression_only=True,
            )
            processors = [*offload_processors, *compression_processors]
            if not processors:
                logger.info(
                    "skip active compression because no matching compact processor is available; "
                    "possible reasons: no offload/compression processor is registered on this context, "
                    "or requested processor_types do not overlap with registered compression processors; "
                    f"requested={processor_types}, "
                    f"registered={[processor.processor_type() for processor in self._processors]}"
                )
                await self._build_and_emit_compression_state(
                    ContextProcessorStateInput(
                        operation_id=uuid.uuid4().hex,
                        status="skipped",
                        phase="active_compress",
                        trigger="manual",
                        processor=None,
                        reason="no_matching_processor",
                        before_messages=self.get_messages(),
                        after_messages=None,
                        started_at=time.time(),
                        ended_at=time.time(),
                        error=None,
                        messages_to_modify=[],
                        force=True,
                        context_max=ContextUtils.resolve_context_max(
                            model_name=self._resolve_context_model_name(kwargs),
                            fallback_context_window_tokens=self._context_window_tokens,
                            model_context_window_tokens=self._model_context_window_tokens,
                        ),
                    )
                )
                return self._build_active_compression_result(
                    _ACTIVE_COMPRESSION_RESULT_NOOP,
                    return_state,
                    history_start=history_start,
                )

            changed = await self._run_active_compression_processors(
                processors,
                **kwargs,
            )
            if changed:
                logger.info("active compression finished and changed context messages")
                return self._build_active_compression_result(
                    _ACTIVE_COMPRESSION_RESULT_COMPRESSED,
                    return_state,
                    history_start=history_start,
                )
            logger.info("active compression finished without changing context messages")
            return self._build_active_compression_result(
                _ACTIVE_COMPRESSION_RESULT_NOOP,
                return_state,
                history_start=history_start,
            )
        finally:
            self._active_compression_in_progress = False
            self._processor_lock.release()

    def pop_messages(self, size: int = 1, with_history: bool = True) -> List[BaseMessage]:
        if size is not None and size < 0:
            raise build_error(
                StatusCode.CONTEXT_EXECUTION_ERROR,
                error_msg="pop size should be larger than 0"
            )

        popped_messages = self._message_buffer.pop_back(size, with_history)

        return popped_messages

    def get_messages(self, size: Optional[int] = None, with_history: bool = True) -> List[BaseMessage]:
        if size is not None and size < 0:
            raise build_error(
                StatusCode.CONTEXT_EXECUTION_ERROR,
                error_msg="get size should be larger than 0"
            )

        messages = self._message_buffer.get_back(size, with_history=with_history)
        return messages

    def set_messages(self, messages: List[BaseMessage], with_history: bool = True):
        ContextUtils.validate_messages(messages)
        messages = ContextUtils.ensure_context_message_ids(messages)
        self._message_buffer.set_messages(messages, with_history)

    def detect_context_window_change(
            self,
            new_window: ContextWindow,
    ) -> ContextWindowChange | None:
        """
        Compare the current LLM-bound window with the previous tracked window.

        This method is an explicit KV cache diff primitive. Callers decide
        whether any KV cache management path is enabled before invoking it.
        """
        old_window = self._last_llm_bound_context_window
        self._last_llm_bound_context_window = new_window.model_copy(deep=True)
        if old_window is None:
            return None

        old_messages = old_window.get_messages()
        new_messages = new_window.get_messages()
        old_tools = old_window.get_tools()
        new_tools = new_window.get_tools()
        msg_start = first_changed_index(old_messages, new_messages)
        tools_start = first_changed_index(old_tools, new_tools)

        # Tool definitions are serialized immediately after the system prompt.
        # first_changed_index intentionally treats append-only lists as unchanged,
        # which is correct for messages but not for tools: appending a tool moves
        # every following conversation token. In that case invalidate the old
        # message suffix following the system prompt. There is no old tool index
        # to use as a legal old-window range for the appended item itself.
        tools_appended = (
            len(new_tools) > len(old_tools)
            and new_tools[:len(old_tools)] == old_tools
        )
        if tools_appended:
            first_context_message = len(old_window.system_messages)
            if first_context_message < len(old_messages):
                if msg_start is None:
                    msg_start = first_context_message
                else:
                    msg_start = min(msg_start, first_context_message)
            tools_start = None

        if msg_start is None and tools_start is None:
            return None

        return ContextWindowChange(
            old_messages=old_messages,
            old_tools=old_tools,
            msg_start=msg_start,
            msg_end=len(old_messages) if msg_start is not None else None,
            tools_start=tools_start,
            tools_end=len(old_tools) if tools_start is not None else None,
        )

    @_fw.emit_before(ContextEvents.CONTEXT_CLEARED, pass_args=False)
    async def clear_messages(self, with_history: bool = True):
        self.pop_messages(len(self), with_history=with_history)
        self._offload_message_buffer = OffloadMessageBuffer()
        self._configure_offload_message_buffer()
        return

    @_fw.emit_after(ContextEvents.CONTEXT_RETRIEVED, result_key="window")
    async def get_context_window(
            self,
            system_messages: List[BaseMessage] = None,
            tools: List[ToolInfo] = None,
            window_size: Optional[int] = None,
            dialogue_round: Optional[int] = None,
            **kwargs
    ) -> ContextWindow:
        if window_size is not None and window_size <= 0:
            raise build_error(
                StatusCode.CONTEXT_EXECUTION_ERROR,
                error_msg="window size should be larger than 0"
            )

        if dialogue_round is not None and dialogue_round <= 0:
            raise build_error(
                StatusCode.CONTEXT_EXECUTION_ERROR,
                error_msg="dialogue round should be larger than 0"
            )

        async with self._guarded_processor_lock("get_context_window"):
            system_messages = (system_messages or [])[:]

            # with specific context size
            system_messages, context_messages = self._get_window_messages(
                system_messages,
                window_size,
                dialogue_round
            )

            window = ContextWindow(
                system_messages=system_messages,
                context_messages=context_messages,
                tools=tools or []
            )

            call_window_mutators = kwargs.pop("window_mutators", None) or []
            kwargs.update({"window_size": window_size})
            kwargs.setdefault("sys_operation", self._sys_operation)
            for processor in self._processors:
                operation_id = None
                started_at = None
                before_messages = None
                started_emitted = False
                try:
                    if await processor.trigger_get_context_window(self, window):
                        logger.info(f"trigger context processor {processor.processor_type()} on GET")
                        operation_id = uuid.uuid4().hex
                        started_at = time.time()
                        before_messages = list(window.context_messages)
                        context_max = ContextUtils.resolve_context_max(
                            model_name=self._resolve_context_model_name(kwargs),
                            fallback_context_window_tokens=self._context_window_tokens,
                            model_context_window_tokens=self._model_context_window_tokens,
                        )
                        await self._build_and_emit_compression_state(
                            ContextProcessorStateInput(
                                operation_id=operation_id,
                                status="started",
                                phase="get_context_window",
                                trigger="passive",
                                processor=processor,
                                reason="processor_triggered",
                                before_messages=before_messages,
                                after_messages=None,
                                started_at=started_at,
                                ended_at=None,
                                error=None,
                                messages_to_modify=[],
                                force=False,
                                context_max=context_max,
                            )
                        )
                        started_emitted = True
                        event, window = await processor.on_get_context_window(self, window, **kwargs)
                        status = "completed" if event is not None else "noop"
                        await self._build_and_emit_compression_state(
                            ContextProcessorStateInput(
                                operation_id=operation_id,
                                status=status,
                                phase="get_context_window",
                                trigger="passive",
                                processor=processor,
                                reason="processor_completed" if event is not None else "processor_noop",
                                before_messages=before_messages,
                                after_messages=list(window.context_messages),
                                started_at=started_at,
                                ended_at=time.time(),
                                error=None,
                                messages_to_modify=list(getattr(event, "messages_to_modify", []) or []),
                                force=False,
                                context_max=context_max,
                                compact_summary=str(getattr(event, "compact_summary", "") or ""),
                                compression_usage=getattr(event, "compression_usage", None),
                            )
                        )
                except asyncio.CancelledError:
                    if started_emitted:
                        await self._build_and_emit_processor_cancelled_state(
                            operation_id=operation_id,
                            phase="get_context_window",
                            trigger="passive",
                            processor=processor,
                            before_messages=before_messages or list(window.context_messages),
                            after_messages=list(window.context_messages),
                            started_at=started_at,
                            force=False,
                            context_max=ContextUtils.resolve_context_max(
                                model_name=self._resolve_context_model_name(kwargs),
                                fallback_context_window_tokens=self._context_window_tokens,
                                model_context_window_tokens=self._model_context_window_tokens,
                            ),
                        )
                    raise
                except Exception as e:
                    await self._build_and_emit_compression_state(
                        ContextProcessorStateInput(
                            operation_id=operation_id or uuid.uuid4().hex,
                            status="failed",
                            phase="get_context_window",
                            trigger="passive",
                            processor=processor,
                            reason="processor_error",
                            before_messages=before_messages or list(window.context_messages),
                            after_messages=list(window.context_messages),
                            started_at=started_at or time.time(),
                            ended_at=time.time(),
                            error=str(e),
                            messages_to_modify=[],
                            force=False,
                            context_max=ContextUtils.resolve_context_max(
                                model_name=self._resolve_context_model_name(kwargs),
                                fallback_context_window_tokens=self._context_window_tokens,
                                model_context_window_tokens=self._model_context_window_tokens,
                            ),
                        )
                    )
                    logger.warning(
                        f"Failed to process GET messages by using processor {processor.processor_type()},"
                        f"reason: {str(e)}"
                    )

            ContextUtils.validate_and_fix_context_window(window)
            window_mutators = [*self._window_mutators, *call_window_mutators]
            for mutator in window_mutators:
                try:
                    window = await mutator(self, window)
                except Exception as e:
                    logger.warning(
                        f"Failed to mutate context window before KV release by using {mutator}, reason: {str(e)}"
                    )
            ContextUtils.validate_and_fix_context_window(window)
            window.statistic = self._stat_context_window(window)
            return window

    def _get_window_messages(
            self,
            system_messages: List[BaseMessage],
            window_size: Optional[int] = None,
            dialogue_round: Optional[int] = None
    ) -> Tuple[List[BaseMessage], List[BaseMessage]]:
        if dialogue_round is not None or self._default_dialogue_round is not None:
            dialogue_round = (
                dialogue_round
                if dialogue_round is not None
                else self._default_dialogue_round
            )
            context_messages = self._message_buffer.get_back()
            round_index = ContextUtils.find_last_n_dialogue_round(context_messages, dialogue_round)
            if round_index >= 0: 
                context_messages = context_messages[round_index:]
        else:
            context_messages = self._message_buffer.get_back()

        # with specific context size
        if window_size is not None or self._default_window_size is not None:
            window_size = (
                window_size
                if window_size is not None
                else self._default_window_size
            )

            system_messages_size = min(len(system_messages), window_size)
            system_messages = system_messages[-system_messages_size:]

            context_messages_size = window_size - system_messages_size
            context_messages = (
                context_messages[-context_messages_size:]
                if context_messages_size > 0
                else []
            )

        return system_messages, context_messages

    def statistic(self) -> ContextStats:
        messages = self.get_messages()
        stat = ContextStats()
        self._stat_messages(stat, messages)
        return stat

    def _stat_context_window(self, context_window: ContextWindow) -> ContextStats:
        messages = context_window.get_messages()
        tools = context_window.get_tools()
        stat = ContextStats()
        self._stat_messages(stat, messages)
        self._stat_tools(stat, tools)
        stat.total_dialogues = len(ContextUtils.find_all_dialogue_round(messages))
        return stat

    def _count_tool_tokens(self, tool_info: ToolInfo) -> int:
        """
        Calculate token count for a single tool, using two-level priority:
        1. Use _token_counter (if available)
        2. Fallback: text string length / 4
        """
        # Priority 1: Use _token_counter
        if self._token_counter is not None:
            return self._token_counter.count_tools([tool_info])

        # Priority 2: Fallback - estimate based on text string length / 4
        # Calculate text length of tool name + description + parameters
        text_content = f"{tool_info.name or ''} {tool_info.description or ''}"
        if tool_info.parameters:
            # Convert parameters dict to string for estimation
            text_content += json.dumps(tool_info.parameters, ensure_ascii=False)
        return len(text_content) // 4

    def _stat_tools(self, stat: ContextStats, tools: List[ToolInfo]):
        stat.tools = len(tools)
        for t in tools:
            stat.tool_tokens += self._count_tool_tokens(t)
        stat.total_tokens += stat.tool_tokens

    def _count_single_message_tokens(self, message: BaseMessage) -> int:
        """
        Calculate token count for a single message (without usage_metadata):
        1. Use _token_counter (if available)
        2. Fallback: text string length / 4
        """
        if self._token_counter is not None:
            return self._token_counter.count_messages([message])

        content = message.content
        if isinstance(content, str):
            return len(content) // 4
        elif isinstance(content, list):
            total = 0
            for part in content:
                if isinstance(part, str):
                    total += len(part) // 4
                elif isinstance(part, dict) and "text" in part:
                    total += len(part["text"]) // 4
            return total
        return 0

    def _get_last_assistant_usage_tokens(self, messages: List[BaseMessage]) -> Optional[int]:
        """
        Get cumulative token count from the usage_metadata of the last AssistantMessage.
        usage_metadata.input_tokens is the cumulative value of all conversation inputs
        (including all messages and tools).

        Returns:
            input_tokens value (cumulative input tokens), or None if not available
        """
        # Search backwards for the last AssistantMessage
        for msg in reversed(messages):
            if isinstance(msg, AssistantMessage) and msg.usage_metadata is not None:
                usage = msg.usage_metadata
                if usage.total_tokens > 0:
                    return usage.total_tokens
        return None

    def _stat_messages(self, stat: ContextStats, messages: List[BaseMessage]):
        stat.total_messages = len(messages)
        stat.total_dialogues = len(ContextUtils.find_all_dialogue_round(messages))
        for msg in messages:
            if msg.role == "assistant":
                stat.assistant_messages += 1
            elif msg.role == "user":
                stat.user_messages += 1
            elif msg.role == "system":
                stat.system_messages += 1
            elif msg.role == "tool":
                stat.tool_messages += 1

        # Priority 1: Get cumulative tokens from usage_metadata of the last AssistantMessage
        usage_tokens = self._get_last_assistant_usage_tokens(messages)

        if usage_tokens is not None:
            # usage_metadata.input_tokens is cumulative for entire conversation input, use directly
            stat.total_tokens = usage_tokens
            return

        # Priority 2/3: Count tokens message by message (TiktokenCounter or fallback)
        for msg in messages:
            msg_tokens = self._count_single_message_tokens(msg)
            if msg.role == "assistant":
                stat.assistant_message_tokens += msg_tokens
            elif msg.role == "user":
                stat.user_message_tokens += msg_tokens
            elif msg.role == "system":
                stat.system_message_tokens += msg_tokens
            elif msg.role == "tool":
                stat.tool_message_tokens += msg_tokens

        stat.total_tokens += (
            stat.assistant_message_tokens +
            stat.user_message_tokens +
            stat.system_message_tokens +
            stat.tool_message_tokens
        )

    def token_counter(self) -> TokenCounter:
        return self._token_counter

    async def _run_add_processors(
            self,
            messages_to_add: List[BaseMessage],
            *,
            force: bool,
            processor_types: List[str] = None,
            compression_only: bool = False,
            **kwargs,
    ) -> Tuple[bool, List[BaseMessage]]:
        processors = self._select_processors(
            processor_types=processor_types,
            compression_only=compression_only,
        )
        changed = False
        for processor in processors:
            operation_id = None
            started_at = None
            before_messages = None
            started_emitted = False
            phase = "active_compress" if force else "add_messages"
            trigger = kwargs.get("compression_trigger") or ("manual" if force else "passive")
            try:
                should_run = force or await processor.trigger_add_messages(self, messages_to_add, **kwargs)
                if should_run:
                    logger.info(
                        f"{'force trigger' if force else 'trigger'} "
                        f"context processor {processor.processor_type()} on ADD"
                    )
                    operation_id = uuid.uuid4().hex
                    started_at = time.time()
                    before_messages = self.get_messages() + messages_to_add
                    context_max = ContextUtils.resolve_context_max(
                        model_name=self._resolve_context_model_name(kwargs),
                        fallback_context_window_tokens=self._context_window_tokens,
                        model_context_window_tokens=self._model_context_window_tokens,
                    )
                    await self._build_and_emit_compression_state(
                        ContextProcessorStateInput(
                            operation_id=operation_id,
                            status="started",
                            phase=phase,
                            trigger=trigger,
                            processor=processor,
                            reason="processor_triggered",
                            before_messages=before_messages,
                            after_messages=None,
                            started_at=started_at,
                            ended_at=None,
                            error=None,
                            messages_to_modify=[],
                            force=force,
                            context_max=context_max,
                        )
                    )
                    started_emitted = True
                    event, messages_to_add = await processor.on_add_messages(
                        self,
                        messages_to_add,
                        force=force,
                        **kwargs,
                    )
                    after_messages = self.get_messages() + messages_to_add
                    status = "completed" if event is not None else "noop"
                    await self._build_and_emit_compression_state(
                        ContextProcessorStateInput(
                            operation_id=operation_id,
                            status=status,
                            phase=phase,
                            trigger=trigger,
                            processor=processor,
                            reason="processor_completed" if event is not None else "processor_noop",
                            before_messages=before_messages,
                            after_messages=after_messages,
                            started_at=started_at,
                            ended_at=time.time(),
                            error=None,
                            messages_to_modify=list(getattr(event, "messages_to_modify", []) or []),
                            force=force,
                            context_max=context_max,
                            compact_summary=str(getattr(event, "compact_summary", "") or ""),
                            compression_usage=getattr(event, "compression_usage", None),
                        )
                    )
                    if event is not None:
                        changed = True
            except asyncio.CancelledError:
                if started_emitted:
                    await self._build_and_emit_processor_cancelled_state(
                        operation_id=operation_id,
                        phase=phase,
                        trigger=trigger,
                        processor=processor,
                        before_messages=before_messages or self.get_messages() + messages_to_add,
                        after_messages=self.get_messages() + messages_to_add,
                        started_at=started_at,
                        force=force,
                        context_max=ContextUtils.resolve_context_max(
                            model_name=self._resolve_context_model_name(kwargs),
                            fallback_context_window_tokens=self._context_window_tokens,
                            model_context_window_tokens=self._model_context_window_tokens,
                        ),
                    )
                raise
            except Exception as e:
                await self._build_and_emit_compression_state(
                    ContextProcessorStateInput(
                        operation_id=operation_id or uuid.uuid4().hex,
                        status="failed",
                        phase=phase,
                        trigger=trigger,
                        processor=processor,
                        reason="processor_error",
                        before_messages=before_messages or self.get_messages() + messages_to_add,
                        after_messages=self.get_messages() + messages_to_add,
                        started_at=started_at or time.time(),
                        ended_at=time.time(),
                        error=str(e),
                        messages_to_modify=[],
                        force=force,
                        context_max=ContextUtils.resolve_context_max(
                            model_name=self._resolve_context_model_name(kwargs),
                            fallback_context_window_tokens=self._context_window_tokens,
                            model_context_window_tokens=self._model_context_window_tokens,
                        ),
                    )
                )
                logger.warning(
                    f"Failed to process ADD messages by using processor {processor.processor_type()},"
                    f"reason: {str(e)}"
                )
        return changed, messages_to_add

    async def _run_active_compression_processors(
            self,
            processors: List[ContextProcessor],
            **kwargs,
    ) -> bool:
        changed = False
        window = ContextWindow(
            system_messages=[],
            context_messages=self.get_messages(),
            tools=[],
        )
        trigger = kwargs.get("compression_trigger") or "manual"
        for processor in processors:
            operation_id = None
            started_at = None
            before_messages = None
            started_emitted = False
            use_window_hook = self._processor_overrides_get_context_window(processor)
            is_offload_processor = ContextUtils.is_offload_processor(processor)
            should_check_active_trigger = (
                use_window_hook
                and (
                    is_offload_processor
                    or processor.processor_type() == "DialogueCompressor"
                )
            )
            try:
                if should_check_active_trigger:
                    should_run = await processor.trigger_get_context_window(self, window, **kwargs)
                    if not should_run:
                        logger.info(
                            "skip active processor %s because trigger conditions are not met",
                            processor.processor_type(),
                        )
                        continue
                logger.info(
                    "force trigger context processor %s on %s",
                    processor.processor_type(),
                    "GET" if use_window_hook else "ADD",
                )
                operation_id = uuid.uuid4().hex
                started_at = time.time()
                before_messages = list(window.context_messages if use_window_hook else self.get_messages())
                context_max = ContextUtils.resolve_context_max(
                    model_name=self._resolve_context_model_name(kwargs),
                    fallback_context_window_tokens=self._context_window_tokens,
                    model_context_window_tokens=self._model_context_window_tokens,
                )
                await self._build_and_emit_compression_state(
                    ContextProcessorStateInput(
                        operation_id=operation_id,
                        status="started",
                        phase="active_compress",
                        trigger=trigger,
                        processor=processor,
                        reason="processor_triggered",
                        before_messages=before_messages,
                        after_messages=None,
                        started_at=started_at,
                        ended_at=None,
                        error=None,
                        messages_to_modify=[],
                        force=True,
                        context_max=context_max,
                    )
                )
                started_emitted = True

                if use_window_hook:
                    event, window = await processor.on_get_context_window(
                        self,
                        window,
                        force=True,
                        **kwargs,
                    )
                    if event is not None:
                        ContextUtils.validate_and_fix_context_window(window)
                        self.set_messages(window.context_messages)
                    after_messages = list(window.context_messages)
                else:
                    event, _ = await processor.on_add_messages(
                        self,
                        [],
                        force=True,
                        **kwargs,
                    )
                    after_messages = self.get_messages()
                    window.context_messages = after_messages

                status = "completed" if event is not None else "noop"
                await self._build_and_emit_compression_state(
                    ContextProcessorStateInput(
                        operation_id=operation_id,
                        status=status,
                        phase="active_compress",
                        trigger=trigger,
                        processor=processor,
                        reason="processor_completed" if event is not None else "processor_noop",
                        before_messages=before_messages,
                        after_messages=after_messages,
                        started_at=started_at,
                        ended_at=time.time(),
                        error=None,
                        messages_to_modify=list(getattr(event, "messages_to_modify", []) or []),
                        force=True,
                        context_max=context_max,
                        compact_summary=str(getattr(event, "compact_summary", "") or ""),
                        compression_usage=getattr(event, "compression_usage", None),
                    )
                )
                if event is not None:
                    changed = True
            except asyncio.CancelledError:
                if started_emitted:
                    await self._build_and_emit_processor_cancelled_state(
                        operation_id=operation_id,
                        phase="active_compress",
                        trigger=trigger,
                        processor=processor,
                        before_messages=before_messages or self.get_messages(),
                        after_messages=list(window.context_messages if use_window_hook else self.get_messages()),
                        started_at=started_at,
                        force=True,
                        context_max=ContextUtils.resolve_context_max(
                            model_name=self._resolve_context_model_name(kwargs),
                            fallback_context_window_tokens=self._context_window_tokens,
                            model_context_window_tokens=self._model_context_window_tokens,
                        ),
                    )
                raise
            except Exception as e:
                await self._build_and_emit_compression_state(
                    ContextProcessorStateInput(
                        operation_id=operation_id or uuid.uuid4().hex,
                        status="failed",
                        phase="active_compress",
                        trigger=trigger,
                        processor=processor,
                        reason="processor_error",
                        before_messages=before_messages or self.get_messages(),
                        after_messages=self.get_messages(),
                        started_at=started_at or time.time(),
                        ended_at=time.time(),
                        error=str(e),
                        messages_to_modify=[],
                        force=True,
                        context_max=ContextUtils.resolve_context_max(
                            model_name=self._resolve_context_model_name(kwargs),
                            fallback_context_window_tokens=self._context_window_tokens,
                            model_context_window_tokens=self._model_context_window_tokens,
                        ),
                    )
                )
                logger.warning(
                    "Failed to actively compress context by using processor %s, reason: %s",
                    processor.processor_type(),
                    str(e),
                )
        return changed

    @staticmethod
    def _processor_overrides_get_context_window(processor: ContextProcessor) -> bool:
        return type(processor).on_get_context_window is not ContextProcessor.on_get_context_window

    async def _build_and_emit_compression_state(
            self,
            state_input: ContextProcessorStateInput,
    ) -> None:
        state = self._processor_state_recorder.build_state(state_input)
        await self._processor_state_recorder.emit(self, state)

    async def _build_and_emit_processor_cancelled_state(
            self,
            *,
            operation_id: str,
            phase: str,
            trigger: str,
            processor: ContextProcessor,
            before_messages: list[BaseMessage],
            after_messages: list[BaseMessage],
            started_at: float,
            force: bool,
            context_max: Optional[int],
    ) -> None:
        await self._build_and_emit_compression_state(
            ContextProcessorStateInput(
                operation_id=operation_id,
                status="failed",
                phase=phase,
                trigger=trigger,
                processor=processor,
                reason="processor_cancelled",
                before_messages=before_messages,
                after_messages=after_messages,
                started_at=started_at,
                ended_at=time.time(),
                error="cancelled",
                messages_to_modify=[],
                force=force,
                context_max=context_max,
            )
        )

    def _build_active_compression_result(
            self,
            result: str,
            include_state: bool,
            *,
            history_start: int = 0,
    ) -> str | dict[str, Any]:
        if not include_state:
            return result
        history = self._processor_state_recorder.history()
        active_history = history[history_start:] if history_start > 0 else history
        state = self._select_active_compression_result_state(active_history)
        payload: dict[str, Any] = {"result": result}
        if isinstance(state, dict):
            payload["state"] = state
            compact_summary = state.get("compact_summary")
            if isinstance(compact_summary, str) and compact_summary:
                payload["compact_summary"] = compact_summary
        return payload

    @staticmethod
    def _select_active_compression_result_state(
            history: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        for state in reversed(history):
            compact_summary = state.get("compact_summary")
            if state.get("status") == "completed" and isinstance(compact_summary, str) and compact_summary:
                return state
        return history[-1] if history else None

    def _select_processors(
            self,
            *,
            processor_types: List[str] = None,
            compression_only: bool = False,
            offload_only: bool = False,
    ) -> List[ContextProcessor]:
        processors = list(self._processors or [])
        if processor_types is not None:
            processor_types = set(processor_types)
            processors = [
                processor for processor in processors
                if processor.processor_type() in processor_types
            ]
        if offload_only:
            processors = [
                processor for processor in processors
                if ContextUtils.is_offload_processor(processor)
            ]
        if compression_only:
            processors = [
                processor for processor in processors
                if ContextUtils.is_compression_processor(processor)
            ]
        return processors

    def offload_messages(self, offload_handle: str, messages: List[BaseMessage]):
        self._offload_message_buffer.offload(offload_handle, "in_memory", messages)

    def save_state(self) -> Dict[str, Any]:
        return {
            "messages": self._message_buffer.get_back(),
            "offload_messages": self._offload_message_buffer.get_all(),
            "last_context_window_access_at": self._last_context_window_access_at,
        }

    def load_state(self, state: Dict[str, Any]):
        context_state = state.get(self._context_id, {})
        messages = context_state.get("messages", [])
        ContextUtils.validate_messages(messages)
        messages = ContextUtils.ensure_context_message_ids(messages)
        self._message_buffer.rebulid(messages)
        last_access_at = context_state.get("last_context_window_access_at")
        self._last_context_window_access_at = (
            float(last_access_at) if isinstance(last_access_at, (int, float)) else None
        )
        offload_messages = context_state.get("offload_messages")
        self._offload_message_buffer = OffloadMessageBuffer()
        if offload_messages:
            for _, msg_list in offload_messages.items():
                ContextUtils.validate_messages(msg_list)
            self._offload_message_buffer = OffloadMessageBuffer(offload_messages)
        self._configure_offload_message_buffer()
