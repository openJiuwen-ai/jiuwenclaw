# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import List, Dict, Any, Literal, Tuple
from pydantic import BaseModel, Field

from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.context_engine.context_engine import ContextEngine
from openjiuwen.core.context_engine.processor.base import ContextProcessor, ContextEvent
from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.context_engine.schema.messages import OffloadMixin
from openjiuwen.core.foundation.llm import BaseMessage


class MessageOffloaderConfig(BaseModel):
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

    messages_threshold: int | None = Field(default=None, gt=0)
    """Maximum number of messages allowed in memory before offloading is triggered."""

    tokens_threshold: int = Field(default=20000, gt=0)
    """Maximum accumulated token count before offloading is triggered."""

    large_message_threshold: int = Field(default=1000, gt=0)
    """Messages whose token count exceeds this value are considered 'large' and may be offloaded."""

    offload_message_type: list[Literal["user", "assistant", "tool"]] = Field(default=["tool"])
    """Roles eligible for offloading. Messages whose role is not in this list are always kept."""

    trim_size: int = Field(default=100, gt=0)
    """Number of tokens to retain when a message is offloaded. The remainder is replaced with an omission marker."""

    messages_to_keep: int | None = Field(default=None, gt=0)
    """Guaranteed number of most-recent messages to retain, regardless of any other threshold."""

    keep_last_round: bool = Field(default=True)
    """If True, the most recent user-assistant round is always preserved even if it would otherwise be offloaded."""


OMIT_STRING = "..."


@ContextEngine.register_processor()
class MessageOffloader(ContextProcessor):
    def __init__(self, config: MessageOffloaderConfig):
        super().__init__(config)
        self._validate_config()

    async def on_add_messages(
            self,
            context: ModelContext,
            messages_to_add: List[BaseMessage],
            **kwargs
    ) -> Tuple[ContextEvent | None, List[BaseMessage]]:
        context_messages = context.get_messages() + messages_to_add
        context_size = len(context)
        event, processed_messages = await self._offload_large_messages(context_messages, context)
        context_messages, messages_to_add = (
            processed_messages[:context_size],
            processed_messages[context_size:]
        )
        context.set_messages(context_messages)
        return event, messages_to_add

    async def trigger_add_messages(
            self,
            context: ModelContext,
            messages_to_add: List[BaseMessage],
            **kwargs
    ) -> bool:
        config = self.config
        message_size = len(context) + len(messages_to_add)
        # Skip if total length is below the keep-floor
        if config.messages_to_keep and message_size <= config.messages_to_keep:
            return False

        # Trigger when message count exceeds hard ceiling
        if config.messages_threshold and message_size > config.messages_threshold:
            logger.info(f"[{self.processor_type()} triggered] context messages num {message_size} "
                        f"exceeds threshold of {config.messages_threshold}")
            return True

        # Fall back to token budget
        token_counter = context.token_counter()
        tokens = 0
        if token_counter:
            context_token = token_counter.count_messages(context.get_messages())
            messages_to_add_token = token_counter.count_messages(messages_to_add)
            tokens = messages_to_add_token + context_token
        if tokens > config.tokens_threshold:
            logger.info(f"[{self.processor_type()} triggered] context tokens {tokens} "
                        f"exceeds threshold of {config.tokens_threshold}")
            return True
        return False

    async def _offload_large_messages(
            self,
            messages: List[BaseMessage],
            context: ModelContext
    ) -> Tuple[ContextEvent, List[BaseMessage]]:
        processed_messages = messages[:]
        last_ai_msg_index = None
        if self.config.keep_last_round:
            last_ai_msg_index = ContextUtils.find_last_ai_message_without_tool_call(messages)
        keep_index = (
            len(messages)
            if not self.config.messages_to_keep
            else len(messages) - self.config.messages_to_keep
        )
        offload_range = (
            keep_index
            if last_ai_msg_index is None
            else min(last_ai_msg_index, keep_index)
        )

        event = ContextEvent(event_type=self.processor_type())
        for idx in range(offload_range - 1, -1, -1):
            msg = processed_messages[idx]
            if (
                msg.role not in self.config.offload_message_type
                or len(msg.content) <= self.config.large_message_threshold
                or isinstance(msg, OffloadMixin)
            ):
                continue
            offload_msg = await self._offload_message(msg, context)
            processed_messages = ContextUtils.replace_messages(
                processed_messages, [offload_msg], idx, idx
            )
            event.messages_to_modify.append(idx)

        return event, processed_messages

    async def _offload_message(self, message: BaseMessage, context: ModelContext) -> BaseMessage:
        trimmed_content = message.content[:self.config.trim_size] + OMIT_STRING
        extra_fields = message.model_dump()
        extra_fields.pop("role", None)
        extra_fields.pop("content", None)
        offload_message = await self.offload_messages(
            role=message.role,
            content=trimmed_content,
            messages=[message],
            context=context,
            **extra_fields
        )
        return offload_message

    def load_state(self, state: Dict[str, Any]) -> None:
        return

    def save_state(self) -> Dict[str, Any]:
        return dict()

    def _validate_config(self):
        if self.config.trim_size >= self.config.large_message_threshold:
            raise build_error(
                StatusCode.CONTEXT_EXECUTION_ERROR,
                error_msg=f"trim_size {self.config.trim_size} cannot larger than "
                          f"large_message_threshold {self.config.large_message_threshold}"
            )
        if (
            self.config.messages_to_keep
            and self.config.messages_threshold
            and self.config.messages_to_keep >= self.config.messages_threshold
        ):
            raise build_error(
                StatusCode.CONTEXT_EXECUTION_ERROR,
                error_msg=f"messages_to_keep {self.config.messages_to_keep} cannot larger than "
                          f"messages_threshold {self.config.messages_threshold}"
            )