# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from dataclasses import dataclass
from typing import List, Optional, Union, Tuple, Dict, Any
from pydantic import BaseModel, Field

from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine.context_engine import ContextEngine
from openjiuwen.core.context_engine.processor.base import ContextProcessor, ContextEvent
from openjiuwen.core.context_engine.schema.messages import OffloadMixin
from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.foundation.llm import (
    BaseMessage,
    UserMessage,
    AssistantMessage,
    SystemMessage,
    Model,
    ModelRequestConfig,
    ModelClientConfig,
    JsonOutputParser,
)
from openjiuwen.core.context_engine.context.context_utils import ContextUtils


_COMPRESS_LEVEL = "compress_level"


DEFAULT_ROUND_COMPRESSION_PROMPT: str = """
You are a Context Conversation Round Compression Assistant.
Your task: compress and summarize the following consecutive conversation rounds into **one complete round** (user + assistant):

📌 Strict Requirements:
1. For each user message: summarize the user's intentions, questions, and instructions.
2. For each assistant message: summarize the AI's responses, decisions, action results, and tool calls.
3. Preserve all specific information related to tasks, decisions, constraints, numerical values, and tool calls.
4. Do not create any new information; all content must be traceable to the original messages.
5. Compress and summarize into **one user message and one assistant message**.
6. The total token count should be 30% of the original.
7. Output must be valid JSON:
{
    "user_summary": "<refined user content>",
    "assistant_summary": "<refined assistant content>"
}
"""


def filter_out_latest_round(rounds: List["DialogueRound"], preserve: bool) -> List["DialogueRound"]:
    if not preserve or len(rounds) <= 1:
        return rounds
    return rounds[:-1]


@dataclass
class DialogueRound:
    user: BaseMessage
    ai: BaseMessage
    level: Optional[int]
    start_idx: int
    end_idx: int


class RoundLevelCompressorConfig(BaseModel):
    rounds_threshold: int = Field(default=10, gt=1)
    """
    Maximum number of consecutive dialogue rounds allowed before compression is triggered.
    Only takes effect when the number of contiguous, same-level dialogue rounds exceeds this threshold.
    """

    tokens_threshold: int = Field(default=10000, gt=0)
    """Maximum accumulated token count before offloading is triggered."""

    keep_last_round: bool = Field(default=True)
    """
    If True, the most recent user-assistant round is always preserved even if it would otherwise be compressed.
    """

    customized_compression_prompt: Optional[str] = None
    """
    User-defined prompt template for dialogue round compression; uses the built-in default prompt if None.
    """

    model: Optional[ModelRequestConfig] = None
    """
    Reference to the model configuration, used to obtain the correct tokenizer and context-window limits. 
    If omitted, the offloader falls back to conservative defaults.
    """

    model_client: Optional[ModelClientConfig] = None
    """
    Optional client-level configuration (endpoint, timeout, retry, etc.) 
    for the model used during compression/summary generation. 
    If omitted, the offloader uses the default client settings.
    """


@ContextEngine.register_processor()
class RoundLevelCompressor(ContextProcessor):
    def __init__(self, config: RoundLevelCompressorConfig):
        super().__init__(config)
        self._rounds_threshold = config.rounds_threshold
        self._prompt = (
            config.customized_compression_prompt
            if config.customized_compression_prompt
            else DEFAULT_ROUND_COMPRESSION_PROMPT
        )
        self._keep_last_round = config.keep_last_round

        self._model = Model(
            self.config.model_client,
            self.config.model
        )

    async def trigger_add_messages(
            self,
            context: ModelContext,
            messages_to_add: list[BaseMessage],
            **kwargs
    ) -> bool:
        all_messages = context.get_messages() + messages_to_add
        rounds = self._iter_rounds(all_messages)
        filtered_rounds = filter_out_latest_round(rounds, self._keep_last_round)
        token_counter = context.token_counter()
        tokens = 0
        is_exceed_token_limit = False
        if token_counter:
            context_token = token_counter.count_messages(context.get_messages())
            messages_to_add_token = token_counter.count_messages(messages_to_add)
            tokens = messages_to_add_token + context_token
        if tokens > self._config.tokens_threshold:
            is_exceed_token_limit = True
        return self._find_best_round_window(filtered_rounds) and is_exceed_token_limit

    async def on_add_messages(
            self,
            context: ModelContext,
            messages_to_add: List[BaseMessage],
            **kwargs
    ) -> tuple[ContextEvent | None, list[BaseMessage]]:

        all_messages = context.get_messages() + messages_to_add

        rounds = self._iter_rounds(all_messages)

        filtered_rounds = filter_out_latest_round(rounds, self._keep_last_round)

        target_rounds = self._find_best_round_window(filtered_rounds)

        if not target_rounds:
            logger.warning(
                "[RoundLevelCompressor] trigger fired but no compressible window found"
            )
            return None, messages_to_add

        new_messages, start_idx, end_idx = await self._compress_rounds(all_messages, target_rounds, context)

        event = ContextEvent(event_type=self.processor_type())
        for start, end in zip(start_idx, end_idx):
            event.messages_to_modify += list(range(start, end + 1))
        context.set_messages(new_messages)
        return event, []

    def _iter_rounds(self, messages: List[BaseMessage]):
        result = []
        i = 0
        while i < len(messages) - 1:
            u, a = messages[i], messages[i + 1]

            if self._is_valid_dialogue_round(u, a):
                result.append(
                    DialogueRound(
                        user=u,
                        ai=a,
                        level=self._get_compress_level(a),
                        start_idx=i,
                        end_idx=i + 1
                    )
                )
                i += 2
            else:
                i += 1

        return result

    @staticmethod
    def _is_valid_dialogue_round(u: BaseMessage, a: BaseMessage) -> bool:
        return (
            u.role == "user"
            and a.role == "assistant"
            and not a.tool_calls
        )

    def _find_best_round_window(self, rounds: List[DialogueRound]) -> List[List[DialogueRound]]:
        all_qualified_windows = []
        window: List[DialogueRound] = []

        for r in rounds:
            if not window:
                window = [r]
                continue

            last = window[-1]

            if r.start_idx != last.end_idx + 1:
                window = [r]
                continue

            if r.level != last.level:
                window = [r]
                continue

            window.append(r)

            if len(window) >= self._rounds_threshold:
                candidate = window[-self._rounds_threshold:]
                all_qualified_windows.append(candidate)
                window = []

        return all_qualified_windows

    async def _compress_round_pairs(
            self,
            rounds: List[DialogueRound],
            context: ModelContext
    ) -> Tuple[Optional[BaseMessage], Optional[BaseMessage]]:
        conversation_pairs = []
        for r in rounds:
            conversation_pairs.append({
                "user": r.user.content,
                "assistant": r.ai.content
            })

        messages = [
            SystemMessage(content=self._prompt),
            UserMessage(content=f"conversation_rounds:{conversation_pairs}")
        ]

        response = await self._model.invoke(messages, output_parser=JsonOutputParser())

        summary = response.parser_content
        if summary and isinstance(summary, dict):
            user_summary = summary.get("user_summary", "")
            assistant_summary = summary.get("assistant_summary", "")

            if user_summary and assistant_summary:
                new_user = await self.offload_messages(
                    role="user",
                    content=user_summary,
                    messages=[r.user for r in rounds],
                    context=context
                )
                new_ai = await self.offload_messages(
                    role="assistant",
                    content=assistant_summary,
                    messages=[r.ai for r in rounds],
                    context=context
                )
                return new_user, new_ai

        logger.warning("[RoundLevelCompressor] Round pair compression failed")
        return None, None

    async def _compress_rounds(
            self,
            messages: List[BaseMessage],
            rounds: Union[List[DialogueRound], List[List[DialogueRound]]],
            context: ModelContext
    ) -> Tuple[List[BaseMessage], List[int], List[int]]:

        if isinstance(rounds[0], DialogueRound):
            target_windows = [rounds]
        else:
            target_windows = rounds

        new_messages = messages.copy()
        all_starts = []
        all_ends = []

        for window in target_windows[::-1]:
            base_level = window[0].level or 0
            new_level = base_level + 1

            new_user, new_ai = await self._compress_round_pairs(window, context)

            if new_user is None or new_ai is None:
                logger.warning("[RoundLevelCompressor] Compression failed, return original messages")
                all_starts.append(window[0].start_idx)
                all_ends.append(window[-1].end_idx)
                continue

            new_user.metadata[_COMPRESS_LEVEL] = new_level
            new_ai.metadata[_COMPRESS_LEVEL] = new_level

            start, end = window[0].start_idx, window[-1].end_idx

            new_messages = ContextUtils.replace_messages(
                new_messages,
                [new_user, new_ai],
                start,
                end
            )

            all_starts.append(start)
            all_ends.append(end)

        all_starts.reverse()
        all_ends.reverse()
        return new_messages, all_starts, all_ends

    async def _compress_messages(
            self,
            messages: List[BaseMessage],
            role: str,
            context: ModelContext
    ) -> Optional[BaseMessage]:
        processed = [
            UserMessage(content=f"role:{m.role}, content:{m.content}")
            for m in messages
        ]

        response = await self._model.invoke(
            [
                SystemMessage(content=self._prompt),
                *processed
            ],
            output_parser=JsonOutputParser()
        )

        summary = response.parser_content
        if summary and isinstance(summary, dict):
            summary = summary.get("summary", "")
            offload_message = await self.offload_messages(
                role=role,
                content=summary,
                messages=messages,
                context=context
            )
            return offload_message
        else:
            logger.warning("[RoundLevelCompressor] Invalid summary from model")
            return None

    @staticmethod
    def _get_compress_level(message: BaseMessage) -> int:
        if not isinstance(message, OffloadMixin):
            return 0
        return message.metadata.get(_COMPRESS_LEVEL, 0)

    def load_state(self, state: Dict[str, Any]) -> None:
        pass

    def save_state(self) -> Dict[str, Any]:
        pass