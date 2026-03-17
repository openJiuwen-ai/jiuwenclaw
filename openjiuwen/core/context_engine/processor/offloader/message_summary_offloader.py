# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Literal

from pydantic import BaseModel, Field

from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.context_engine.context_engine import ContextEngine
from openjiuwen.core.foundation.llm import (
    BaseMessage, SystemMessage, UserMessage,
    ModelRequestConfig, ModelClientConfig, Model
)
from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.context_engine.processor.offloader.message_offloader import MessageOffloader


class MessageSummaryOffloaderConfig(BaseModel):
    """
    Fine-grained knobs for the **message-summary off-loader**.

    The component keeps the in-context history within **memory & token budgets**
    by **compressing or discarding** messages once the **configured thresholds are hit**.
    All fields are optional and can be tuned per deployment.

    **Evaluation order (highest → lowest priority):**
    1. `messages_to_keep` – newest N messages are **immune** to off-loading.
    2. `keep_last_round` – the latest **user + assistant** turn is **always** kept.
    3. `messages_threshold` – total message **count** trigger.
    4. `tokens_threshold` – total **token** trigger (checked after every append).
    5. `large_message_threshold` – **per-message** token size; larger messages are
       **preferentially** selected for compression.

    Only roles listed in `offload_message_type` are eligible; others are **never** touched.
    """

    messages_threshold: int | None = Field(default=None, gt=0)
    """Hard ceiling on **message count**.  Exceeding it starts off-loading."""

    tokens_threshold: int = Field(default=20000, gt=0)
    """Hard ceiling on **accumulated tokens** (tokenizer-dependent).  Checked after each append."""

    large_message_threshold: int = Field(default=1000, gt=0)
    """
    Token length above which a single message is labelled *large* and 
    becomes a **preferred** compression candidate.
    """

    offload_message_type: list[Literal["user", "assistant", "tool"]] = Field(default=["tool"])
    """White-list of **roles** that may be compressed or off-loaded.  Roles absent here are **protected**."""

    messages_to_keep: int | None = Field(default=None, gt=0)
    """Guarantee that the **newest** *N* messages are **never** off-loaded (overrides `trim_size`)."""

    keep_last_round: bool = Field(default=True)
    """If *True*, the **latest user–assistant round** (two messages) is **immune** to any off-loading."""

    model: ModelRequestConfig | None = Field(default=None)
    """Supplies **tokenizer** and **context-window** limits. If omitted, conservative fall-backs are used."""

    model_client: ModelClientConfig | None = Field(default=None)
    """
    Optional **client-level** settings (endpoint, timeout, retry, headers) 
    for the model that **performs** the summary/compression.
    """

    customized_summary_prompt: str | None = Field(default=None)
    """User-supplied **prompt** for the summary model.  If *None*, a built-in prompt is used."""


DEFAULT_OFFLOAD_SUMMARY_PROMPT: str = \
"""
You are a "high-density summarizer".
Your task is to shrink the overly long message below into 2–4 concise sentences that:
Contain ≤ 15 % of the original token count;
Keep all critical facts, figures, conclusions, requests or decisions verbatim;
Remove greetings, repetition, filler, examples, jokes, and ornamental language;
Speak in neutral, third-person style;
Do NOT explain, comment, or add extra information—output the summary only.
Begin:
"""


@ContextEngine.register_processor()
class MessageSummaryOffloader(MessageOffloader):
    def __init__(self, config: MessageSummaryOffloaderConfig):
        super().__init__(config)

        self._model = Model(
            model_client_config=self.config.model_client,
            model_config=self.config.model
        )

    async def _offload_message(self, message: BaseMessage, context: ModelContext) -> BaseMessage:
        prompt = self.config.customized_summary_prompt or DEFAULT_OFFLOAD_SUMMARY_PROMPT
        system_message = SystemMessage(content=prompt)
        response = await self._model.invoke(
            [
                system_message,
                UserMessage(content=message.content)
            ]
        )
        summarized_content = response.content
        extra_fields = message.model_dump()
        extra_fields.pop("role", None)
        extra_fields.pop("content", None)
        offload_message = await self.offload_messages(
            role=message.role,
            content=summarized_content,
            messages=[message],
            context=context,
            **extra_fields
        )
        return offload_message

    def _validate_config(self):
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