# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import List, Optional, Tuple, Dict, Any

from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.context_engine.processor.base import ContextProcessor
from openjiuwen.core.context_engine.token.base import TokenCounter
from openjiuwen.core.context_engine.schema.config import ContextEngineConfig
from openjiuwen.core.foundation.llm import BaseMessage, ToolMessage, SystemMessage
from openjiuwen.core.foundation.tool import ToolInfo, Tool, ToolCard, tool
from openjiuwen.core.context_engine.base import ModelContext, ContextWindow, ContextStats
from openjiuwen.core.context_engine.context.message_buffer import ContextMessageBuffer, OffloadMessageBuffer
from openjiuwen.core.context_engine.context.kv_cache_manager import KVCacheManager
from openjiuwen.core.runner.callback import lazy_callback_framework as _fw
from openjiuwen.core.runner.callback.events import ContextEvents


_RELOADER_SYSTEM_PROMPT = """
You may see offloaded content markers in your context: [[OFFLOAD: handle=<id>, type=<type>]].

When you see an offloaded-content marker and believe retrieving it will help your answer, 
feel free to call reload_original_context_messages:
- Call reload_original_context_messages(offload_handle="<id>", offload_type="<type>") with the exact values from the marker
- Do not guess or make up the missing content

Storage types: "in_memory" (session cache).
"""


class SessionModelContext(ModelContext):
    def __init__(
            self,
            context_id: str,
            session_id: str,
            config: ContextEngineConfig,
            *,
            history_messages: List[BaseMessage] = None,
            processors: List[ContextProcessor] = None,
            token_counter: TokenCounter = None
    ):
        self._message_id = 0
        self._validate_and_init_messages(history_messages)
        self._context_id = context_id
        self._session_id = session_id
        self._message_buffer = ContextMessageBuffer(history_messages or [], config.max_context_message_num)
        self._default_window_size = config.default_window_message_num
        self._enable_reload = config.enable_reload
        self._default_dialogue_round = config.default_window_round_num
        self._token_counter = token_counter
        self._processors = processors
        self._kv_cache_manager = KVCacheManager(session_id) if config.enable_kv_cache_release else None
        self._offload_message_buffer = OffloadMessageBuffer()
        self._reloader_tool_card = ToolCard(
            id=f"reload_{session_id}_{context_id}",
            name="reload_original_context_messages",
            description="Retrieve messages that were previously offloaded from the context window."
                        "Provide the exact handle and storage type returned when the content was offloaded;"
                        "the tool will fetch the complete original message list and inject "
                        "it back into the conversation, allowing the model to see the full text "
                        "as if it had never been removed.",
            input_params={
                "type": "object",
                "properties": {
                    "offload_handle": {
                        "description": "A unique identifier or file path pointing to the offloaded content. "
                                       "Accepts either a UUID string (e.g., 'abc123-def456') for memory-based storage.",
                        "type": "string",
                    },
                    "offload_type": {
                        "description": "The storage backend used when the content was offloaded. Must be one of: "
                                       "'in_memory': Content was stored in in-memory cache. "
                                       "Requires offload_handle to be a UUID or key string.",
                        "type": "string",
                    },
                },
                "required": ["offload_handle", "offload_type"],
            },
        )

    def __len__(self):
        return self._message_buffer.size()

    def session_id(self) -> str:
        return self._session_id

    def context_id(self) -> str:
        return self._context_id

    @_fw.emit_after(ContextEvents.CONTEXT_UPDATED, result_key="messages")
    async def add_messages(
            self,
            messages: BaseMessage | List[BaseMessage],
            **kwargs
    ) -> List[BaseMessage]:
        self._validate_and_init_messages(messages)
        messages_to_add = messages if isinstance(messages, list) else [messages]
        for processor in self._processors:
            try:
                if await processor.trigger_add_messages(self, messages_to_add, **kwargs):
                    logger.info(f"trigger context processor {processor.processor_type()} on ADD")
                    event, messages_to_add = await processor.on_add_messages(self, messages_to_add, **kwargs)
            except Exception as e:
                logger.warning(
                    f"Failed to process ADD messages by using processor {processor.processor_type()},"
                    f"reason: {str(e)}"
                )
        self._message_buffer.add_back(messages_to_add)
        return messages_to_add

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
        self._validate_and_init_messages(messages)
        self._message_buffer.set_messages(messages, with_history)

    @_fw.emit_before(ContextEvents.CONTEXT_CLEARED, pass_args=False)
    async def clear_messages(self, with_history: bool = True):
        self.pop_messages(len(self), with_history=with_history)
        self._offload_message_buffer = OffloadMessageBuffer()
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

        system_messages = (system_messages or [])[:]
        if self._enable_reload:
            system_messages.append(SystemMessage(content=_RELOADER_SYSTEM_PROMPT))

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

        kwargs.update({"window_size": window_size})
        for processor in self._processors:
            try:
                if await processor.trigger_get_context_window(self, window):
                    logger.info(f"trigger context processor {processor.processor_type()} on GET")
                    event, window = await processor.on_get_context_window(self, window, **kwargs)
            except Exception as e:
                logger.warning(
                    f"Failed to process GET messages by using processor {processor.processor_type()},"
                    f"reason: {str(e)}"
                )

        self._validate_and_fix_context_window(window)
        if self._kv_cache_manager:
            await self._kv_cache_manager.release(window, **kwargs)
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

    def _stat_tools(self, stat: ContextStats, tools: List[ToolInfo]):
        def count_tools(tools: List[ToolInfo]) -> int:
            if self._token_counter:
                return self._token_counter.count_tools(tools)
            return 0

        stat.tools = len(tools)
        stat.tool_tokens = count_tools(tools)
        stat.total_tokens += stat.tool_tokens

    def _stat_messages(self, stat: ContextStats, messages: List[BaseMessage]):
        def count_message(message: BaseMessage) -> int:
            if self._token_counter:
                return self._token_counter.count_messages([message])
            return 0

        stat.total_messages = len(messages)
        for msg in messages:
            if msg.role == "assistant":
                stat.assistant_messages += 1
                stat.assistant_message_tokens += count_message(msg)
            elif msg.role == "user":
                stat.user_messages += 1
                stat.user_message_tokens += count_message(msg)
            elif msg.role == "system":
                stat.system_messages += 1
                stat.system_message_tokens += count_message(msg)
            elif msg.role == "tool":
                stat.tool_messages += 1
                stat.tool_message_tokens += count_message(msg)
        stat.total_tokens += (
            stat.assistant_message_tokens +
            stat.user_message_tokens +
            stat.system_message_tokens +
            stat.tool_message_tokens
        )
        stat.total_dialogues = len(ContextUtils.find_all_dialogue_round(messages))

    @staticmethod
    def _validate_and_init_messages(messages: BaseMessage | List[BaseMessage]):
        if isinstance(messages, BaseMessage):
            return
        if isinstance(messages, list):
            for msg in messages:
                if not isinstance(msg, BaseMessage):
                    raise build_error(
                        StatusCode.CONTEXT_MESSAGE_INVALID,
                        error_msg="messages should be a BaseMessage or a list of BaseMessage"
                    )
            return
        raise build_error(
            StatusCode.CONTEXT_MESSAGE_INVALID,
                error_msg="messages should be a BaseMessage or a list of BaseMessage"
        )

    @staticmethod
    def _validate_and_fix_context_window(context_window: ContextWindow):
        messages: List[BaseMessage] = context_window.context_messages
        if not messages:  # empty window, nothing to do
            return

        # locate the first non-ToolMessage
        first_non_tool = 0
        while first_non_tool < len(messages) and isinstance(messages[first_non_tool], ToolMessage):
            first_non_tool += 1

        # entirely tool messages → invalid window
        if first_non_tool == len(messages):
            context_window.context_messages = []
            return

        # slice away leading tool messages (if any)
        if first_non_tool > 0:
            context_window.context_messages = messages[first_non_tool:]

    def token_counter(self) -> TokenCounter:
        return self._token_counter

    def reloader_tool(self) -> Tool:
        @tool(card=self._reloader_tool_card)
        def reload_original_context_messages(offload_handle: str, offload_type: str) -> str:
            reloaded_messages = self._offload_message_buffer.reload(offload_handle, offload_type)
            if not reloaded_messages:
                return f"Failed to reload messages with offload_handle={offload_handle} and offload_type={offload_type}"
            return ContextUtils.format_reloaded_messages(offload_handle, reloaded_messages)
        return reload_original_context_messages

    def offload_messages(self, offload_handle: str, messages: List[BaseMessage]):
        self._offload_message_buffer.offload(offload_handle, "in_memory", messages)

    def save_state(self) -> Dict[str, Any]:
        return {
            "messages": self._message_buffer.get_back(),
            "offload_messages": self._offload_message_buffer.get_all()
        }

    def load_state(self, state: Dict[str, Any]):
        messages = state.get(self._context_id, {}).get("messages", [])
        self._validate_and_init_messages(messages)
        self._message_buffer.rebulid(messages)
        offload_messages = state.get(self._context_id, {}).get("offload_messages")
        self._offload_message_buffer = OffloadMessageBuffer()
        if offload_messages:
            for _, msg_list in offload_messages.items():
                self._validate_and_init_messages(msg_list)
            self._offload_message_buffer = OffloadMessageBuffer(offload_messages)