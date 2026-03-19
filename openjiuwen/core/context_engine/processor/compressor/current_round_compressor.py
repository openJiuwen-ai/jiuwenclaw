# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import List, Optional, Tuple
from pydantic import BaseModel, Field

from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.context_engine.context_engine import ContextEngine
from openjiuwen.core.context_engine.processor.base import ContextProcessor, ContextEvent
from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.foundation.llm import (
    BaseMessage, AssistantMessage, SystemMessage, UserMessage,
    ModelRequestConfig, ModelClientConfig, Model, JsonOutputParser, ToolMessage
)
from openjiuwen.core.context_engine.context.context_utils import ContextUtils


DEFAULT_COMPRESSION_PROMPT: str = """
You are a context refinement assistant.
Your sole task: Compress the following conversation history into **≤ 500 tokens**, while retaining all facts, decisions, constraints, and key values needed for future replies.

---
📌 Input Format Explanation:
Each message follows the format: "role:<role_type>, content:<actual_content>"
- role=assistant: The actual content is the AI's response/answer, which may contain tool calls (tool_calls field)
- role=tool: The actual content is the result of a tool execution

Your task is to:
1. Identify the actual content within each message (ignore the "role:" prefix)
2. Only compress and summarize the actual content

---

📌 Compression Rules (mandatory):
- Use natural language; preserve business data structures (categories, differences, features)
- Retain **all task-relevant specific points**
- Do not omit any content that answers sub-questions
---

📌 MANDATORY REQUIREMENT - Tool Call Information (must be explicitly retained):

**Step 1: Extract Tool Calls from Assistant Messages**
For all messages with role=assistant that contain tool_calls, you MUST extract:
1. **Tool ID**: The unique identifier for each tool call (e.g., `tool_call_id_001`)
2. **Tool Name**: The specific name of the tool being called (e.g., `add`, `search`, `weather`)

**Step 2: Match with Tool Results**
- Match each tool call with its corresponding tool result (role=tool message)
- Record what result was obtained from each executed tool

**Step 3: Summarize Execution Status**
You MUST explicitly state the following in your summary:
1. **Executed Tools**: List all tools that have been called AND executed, including:
   - Tool ID
   - Tool Name
   - Result obtained
2. **Pending Tools**: List any tools that were called but NOT yet executed (if any)
3. **Completion Status**: State whether ALL tool calls have been executed or there are pending ones

**Output Format Example**:
- Original assistant message contains: tool_calls: [{"id": "call_001", "name": "add", "arguments": "3+5"}]
- Original tool result: "tool: Result of call_001: 8"
- Compressed must contain: "Tool call: call_001 -> add(3+5), Executed, Result: 8"

---

📌 Output Requirements:
- Retain key information, conclusions, decisions, and answers
- **For tool messages, must explicitly state: what tool was called, what parameters were passed, what result was obtained**
- **For assistant messages with tool_calls, must track: which tools have been executed, which are pending, and the final status**
- Prefix format: "Through <tool_name> tool, obtained: <compressed_text>"

Output valid JSON: {"summary": "<compressed_text>"}
"""


class CurrentRoundCompressorConfig(BaseModel):
    messages_threshold: int = Field(default=None, gt=0)
    """Maximum number of messages allowed in memory before offloading is triggered."""

    tokens_threshold: int = Field(default=10000, gt=0)
    """Maximum accumulated token count before offloading is triggered."""

    messages_to_keep: int | None = Field(default=None, gt=0)
    """Guaranteed number of most-recent messages to retain, regardless of any other threshold."""
    large_message_threshold: int = Field(default=1000, gt=0)

    customized_compression_prompt: str | None = Field(default=None)
    """User-supplied prompt for the compression/summary step; falls back to built-in prompt if None."""

    single_multi_compression: bool = Field(default=False)
    """
    Switch between single-message and whole-block compression.
    False (default) → compress only the individual message that exceeds token limit.  
    True            → compress the entire contiguous message block as one unit.
    """

    model: ModelRequestConfig | None = Field(default=None)
    """
    Reference to the model configuration, used to obtain the correct tokenizer and context-window limits. 
    If omitted, the offloader falls back to conservative defaults.
    """

    model_client: ModelClientConfig | None = Field(default=None)
    """
    Optional client-level configuration (endpoint, timeout, retry, etc.) 
    for the model used during compression/summary generation. 
    If omitted, the offloader uses the default client settings.
    """


@ContextEngine.register_processor()
class CurrentRoundCompressor(ContextProcessor):
    def __init__(self, config: CurrentRoundCompressorConfig):
        super().__init__(config)
        self._compressed_prompt = (
            config.customized_compression_prompt
            if config.customized_compression_prompt
            else DEFAULT_COMPRESSION_PROMPT
        )
        self._token_threshold = config.tokens_threshold
        self._message_num_threshold = config.messages_threshold
        self._messages_to_keep = config.messages_to_keep
        self._single_multi_config = config.single_multi_compression
        self._model_config = config.model
        self._large_message_threshold = config.large_message_threshold

        self._model = Model(
            self.config.model_client,
            self.config.model
        )

    async def on_add_messages(
            self,
            context: ModelContext,
            messages_to_add: List[BaseMessage],
            **kwargs
    ) -> Tuple[ContextEvent | None, List[BaseMessage]]:
        context_messages = context.get_messages() + messages_to_add
        last_user_idx = await self.get_compress_idx(context_messages)
        if last_user_idx == -1:
            return None, messages_to_add
        if self._messages_to_keep:
            messages = context_messages[:-self._messages_to_keep]
        else:
            messages = context_messages
        end_idx = len(messages) - 1

        event = ContextEvent(event_type=self.processor_type())
        if self._single_multi_config:
            compressed_context = await self.multi_compress(
                context_messages,
                last_user_idx,
                end_idx,
                context
            )
            if compressed_context:
                event.messages_to_modify += list(range(last_user_idx, end_idx))
                context.set_messages(compressed_context)
                return event, []
            else:
                return None, messages_to_add
        else:
            try:
                compressed_context = await self.single_compress(
                    context_messages,
                    last_user_idx,
                    end_idx,
                    context
                )
            except Exception as e:
                raise build_error(
                    StatusCode.CONTEXT_EXECUTION_ERROR,
                    error_msg=f"compress messages failed",
                    cause=e
                ) from e
            if compressed_context:
                event.messages_to_modify += list(range(last_user_idx, end_idx))
                context.set_messages(compressed_context)
                return event, []
            else:
                return None, messages_to_add


    async def trigger_add_messages(
            self,
            context: ModelContext,
            messages_to_add: List[BaseMessage],
            **kwargs
    ) -> bool:
        config = self.config
        message_size = len(context) + len(messages_to_add)
        if message_size > self._message_num_threshold:
            logger.info(f"[{self.processor_type()} triggered] context messages num {message_size} "
                        f"exceeds threshold of {config.messages_threshold}")
            return True
        if self._messages_to_keep and message_size < self._messages_to_keep:
            return False
        token_counter = context.token_counter()
        tokens = 0
        if token_counter:
            context_token = token_counter.count_messages(context.get_messages())
            messages_to_add_token = token_counter.count_messages(messages_to_add)
            tokens = messages_to_add_token + context_token
        if tokens > self._token_threshold:
            logger.info(f"[{self.processor_type()} triggered] context tokens {tokens} "
                        f"exceeds threshold of {config.tokens_threshold}")
            return True
        return False

    async def get_compress_idx(self, messages: List[BaseMessage]) -> int:
        compressed_idx = -1
        for i in range(len(messages) - 1, -1, -1):
            if isinstance(messages[i], UserMessage):
                compressed_idx = i
                break
        if compressed_idx == len(messages) - 1:
            return -1

        if compressed_idx < 0:
            return -1

        keep_index = (
            len(messages)
            if not self._messages_to_keep
            else len(messages) - self._messages_to_keep
        )

        if compressed_idx >= keep_index:
            return -1

        return compressed_idx

    async def multi_compress(
            self,
            context_messages: List[BaseMessage],
            last_user_idx: int,
            end_idx: int,
            context: ModelContext,
    ) -> Optional[list[BaseMessage]]:
        start_idx = last_user_idx + 1
        end_idx = end_idx
        if end_idx >= start_idx:
            if isinstance(context_messages[end_idx], AssistantMessage) and context_messages[end_idx].tool_calls:
                end_idx = end_idx - 1
            if end_idx < start_idx:
                return None
        messages_to_compress = context_messages[start_idx:end_idx + 1]
        compressed_context = await self.compress(messages_to_compress, context)
        if compressed_context:
            context_messages = ContextUtils.replace_messages(
                context_messages,
                [compressed_context],
                start_idx,
                end_idx
            )

        return context_messages

    async def single_compress(
            self,
            context_messages: List[BaseMessage],
            last_user_idx: int,
            end_idx: int,
            context: ModelContext
    ) -> Optional[list[BaseMessage]]:
        start_idx = last_user_idx + 1
        end_idx = end_idx
        token_counter = context.token_counter()
        for idx in range(start_idx, end_idx + 1):
            msg = context_messages[idx]
            if isinstance(msg, AssistantMessage) and msg.tool_calls:
                continue
            context_token = token_counter.count_messages([msg])
            if context_token > self._large_message_threshold:
                compressed_context = await self.compress([msg], context)
                if compressed_context:
                    context_messages = ContextUtils.replace_messages(
                        context_messages,
                        [compressed_context], idx, idx
                    )
            else:
                continue
        return context_messages

    async def compress(
            self,
            messages_to_compress: List[BaseMessage],
            context: ModelContext
    ) -> BaseMessage:
        ai_count = 0
        tool_count = 0
        for msg in messages_to_compress:
            if isinstance(msg, AssistantMessage):
                ai_count += 1
            if isinstance(msg, ToolMessage):
                tool_count += 1
        all_count = ai_count + tool_count
        processed_messages = [
            UserMessage(content=f"role:{msg.role}, content:{msg}")
            for msg in messages_to_compress
        ]
        response = await self._model.invoke(
            [
                SystemMessage(content=self._compressed_prompt),
                *processed_messages
            ],
            output_parser=JsonOutputParser()
        )
        summary = response.parser_content
        if summary and isinstance(summary, dict):
            summary = summary.get("summary", "")

        else:
            summary = response.content
            logger.warning(
                f"JSON parsing failed, using raw LLM output as summary. "
                f"Output: {summary[:200]}..."
            )
        offload_message = await self.offload_messages(
            role="user",
            content=f"[This is the compressed message, and a total of {all_count} messages have been compressed."
                    f"It includes {ai_count} assistant messages and {tool_count} tool messages.]" + summary,
            messages=messages_to_compress,
            context=context
        )
        return offload_message
