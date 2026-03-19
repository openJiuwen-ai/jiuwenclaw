# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import List, Dict, Any, Tuple, Optional
from pydantic import BaseModel, Field

from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine.context_engine import ContextEngine
from openjiuwen.core.context_engine.processor.base import ContextProcessor, ContextEvent
from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.foundation.llm import (
    BaseMessage, AssistantMessage, UserMessage, SystemMessage,
    ModelRequestConfig, ModelClientConfig, Model, JsonOutputParser
)
from openjiuwen.core.context_engine.context.context_utils import ContextUtils


DEFAULT_COMPRESSION_PROMPT: str = """
You are a "tool-call compressor that relies solely on the original text". You have no knowledge base, cannot use common-sense reasoning, and cannot infer or complete; you can only process the given text.

Your task: Extract and compress the shortest information segment that fully answers the user's task requirements from the tool calls and tool responses.

Rules:
- Retain only task-relevant specific information points; delete all filler, examples, or duplicates.
- Preserve business data structures (categories, differences, feature points) in natural language.
- Do not omit any sub-question content.
- Prefix the compressed content with: "Through <tool_name> tool, obtained: <compressed_text>".

Output valid JSON:
```json
{
    "summary": "<compressed_text>"
}
```
"""


class DialogueCompressorConfig(BaseModel):
    """
    Configuration for the MessageOffloader ContextProcessor.

    The offloader keeps the conversation history within safe memory/token limits
    by trimming or offloading messages once the configured thresholds are exceeded.
    Rules are evaluated in the following order:

    1. messages_to_keep: the most recent N messages are always retained.
    2. messages_threshold: when total message count exceeds this value offloading
       is triggered.
    3. tokens_threshold: when accumulated token count exceeds this value
       offloading is triggered.

    Only messages whose role appears in `offload_message_type` and whose token
    length is greater than `large_message_threshold` are eligible for offloading.
    The last user-assistant round can be preserved independently of the above
    rules by setting `keep_last_round=True`.
    """

    messages_threshold: int = Field(default=None, gt=0)
    """Maximum number of messages allowed in memory before offloading is triggered."""

    tokens_threshold: int = Field(default=10000, gt=0)
    """Maximum accumulated token count before offloading is triggered."""

    messages_to_keep: int = Field(default=None, gt=0)
    """Guaranteed number of most-recent messages to retain, regardless of any other threshold."""

    keep_last_round: bool = Field(default=True)
    """If True, the most recent user-assistant round is always preserved even if it would otherwise be offloaded."""

    customized_compression_prompt: str | None = Field(default=None)
    """User-supplied prompt for the compression/summary step; falls back to built-in prompt if None."""

    compression_token_limit: int = Field(default=2000, gt=0)
    """Max tokens allowed in the compressed summary; shorter summaries are preferred when possible."""

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
class DialogueCompressor(ContextProcessor):
    def __init__(self, config: DialogueCompressorConfig):
        super().__init__(config)
        self._compressed_prompt = (
            config.customized_compression_prompt
            if config.customized_compression_prompt
            else DEFAULT_COMPRESSION_PROMPT
        )
        self._token_threshold = config.tokens_threshold
        self._message_num_threshold = config.messages_threshold
        self._messages_to_keep = config.messages_to_keep

        self._model_config = config.model

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
        compressed_idx = await self.get_compress_idx(context_messages)
        if compressed_idx == -1:
            return None, messages_to_add

        msg_pairs = await self.get_compress_pairs(context_messages[:compressed_idx])
        if len(msg_pairs) == 0:
            return None, messages_to_add

        event = ContextEvent(event_type=self.processor_type())
        for msg_pair in msg_pairs[::-1]:
            start_idx = msg_pair[0] + 1
            end_idx = msg_pair[1]
            dialogues = []
            for i in range(start_idx, end_idx + 1):
                dialogues.append(context_messages[i])
            compressed_context = await self._compress(dialogues, context)
            if compressed_context:
                event.messages_to_modify += list(range(start_idx, end_idx))
                context_messages = ContextUtils.replace_messages(
                    context_messages,
                    [compressed_context],
                    start_idx,
                    end_idx
                )
        context.set_messages(context_messages)
        return None, []

    async def trigger_add_messages(
            self,
            context: ModelContext,
            messages_to_add: List[BaseMessage],
            **kwargs
    ) -> bool:
        config = self.config
        message_size = len(context) + len(messages_to_add)
        if self._message_num_threshold is not None and message_size > self._message_num_threshold:
            logger.info(f"[{self.processor_type()} triggered] context messages num {message_size} "
                        f"exceeds threshold of {config.messages_threshold}")
            return True
        if self._messages_to_keep is not None and message_size < self._messages_to_keep:
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
        last_ai_msg_index = None
        if self.config.keep_last_round:
            last_ai_msg_index = ContextUtils.find_last_ai_message_without_tool_call(messages)
        keep_index = (
            len(messages)
            if not self.config.messages_to_keep
            else len(messages) - self.config.messages_to_keep
        )
        compressed_idx = (
            keep_index
            if last_ai_msg_index is None
            else min(last_ai_msg_index, keep_index)
        )

        return compressed_idx

    @staticmethod
    async def get_compress_pairs(messages: List[BaseMessage]) -> List[Tuple[int, int]]:
        current_user = -1
        result = []
        for i, msg in enumerate(messages):
            if isinstance(msg, UserMessage):
                current_user = i
            elif isinstance(msg, AssistantMessage) and not msg.tool_calls and current_user != -1:
                if i - current_user > 1:
                    result.append((current_user, i))
                    current_user = -1
            else:
                continue

        return result

    async def _compress(
            self,
            messages_to_compress: List[BaseMessage],
            context: ModelContext
    ) -> BaseMessage:
        messages = [SystemMessage(content=self._compressed_prompt)] + messages_to_compress
        response = await self._model.invoke(messages, output_parser=JsonOutputParser())
        summary = response.parser_content
        if summary:
            summary = summary.get("summary", "")
        else:
            summary = response.content
            logger.warning(
                f"JSON parsing failed, using raw LLM output as summary. "
                f"Output: {summary[:200]}..."
            )
        offload_message = await self.offload_messages(
            role="assistant",
            content=summary,
            messages=messages,
            context=context
        )
        return offload_message

    def load_state(self, state: Dict[str, Any]) -> None:
        pass

    def save_state(self) -> Dict[str, Any]:
        pass
