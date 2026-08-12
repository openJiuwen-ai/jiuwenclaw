# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.common.logging import logger
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.base import ModelContext
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.context.context_utils import ContextUtils
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.context_engine import ContextEngine
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.base import ContextEvent, ContextProcessor
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.compressor.util import (
    build_team_collaboration_reinjected_messages,
    message_to_text,
)
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    BaseMessage,
    JsonOutputParser,
    Model,
    ModelClientConfig,
    ModelRequestConfig,
    SystemMessage,
    ToolMessage,
    UserMessage,
)


DEFAULT_COMPRESSION_PROMPT: str = """\
You are a **Task Data Preservation Expert** focused on compressing historical ReAct blocks with high fidelity.

Your output will replace only the explicitly listed target ReAct blocks.

## COMPRESSION RESPONSIBILITY

- Preserve the information most useful for correctly completing and continuing the task.
- Retain both action continuity and task-critical factual basis.
- Keep unresolved work, handoff state, decisions, constraints, corrections, key findings, and important tool results.
- Preserve the user's original requirements, constraints, acceptance criteria, and preferences as completely as possible.
- Preserve the model's final result, final answer, or completed outcome for each finished block.
- Do not weaken or over-compress the user's original request unless absolutely necessary.

## INPUT BOUNDARIES

- You will receive the full conversation context so you can understand the global task.
- You will also receive a separate list of compression targets.
- Compress ONLY the listed target blocks.
- Do NOT rewrite non-target messages.
- Treat non-target messages as reference context only.

## INFORMATION PRIORITY

Preserve information in this order:
1. Task goals and user intent
2. Critical factual basis for correct continuation
3. Open work / unfinished work
4. Handoff state at the block boundary
5. Key decisions, constraints, changes, and corrections
6. Important files, artifacts, resources, outputs, and tool results
7. Supporting details

Never drop higher-priority information to preserve lower-priority details.

## HANDOFF / BOUNDARY RULES

- Preserve the minimum handoff information needed to connect each compressed block to later context.
- If later messages supersede or correct earlier block content, reflect the corrected state appropriately.
- Do NOT absorb standalone content from non-target messages unless required to explain the target block correctly.

## TASK-TYPE ADAPTATION

- For execution-heavy tasks, prioritize action continuity, work-in-progress state, dependencies, blockers, and exact handoff status.
- For information-heavy tasks, prioritize findings, evidence, extracted structure, comparisons, conclusions, and unresolved questions.
- In all cases, preserve both what was done and what was learned.

## OUTPUT RULES

- Target length for each block summary: <= {compression_target_tokens} tokens.
- Each block is a finished historical ReAct block, not ongoing work.
- Preserve both `User Requirements` and `Final Result` explicitly in each summary when they exist.
- Return valid JSON only.
- Use this exact schema:
{
  "blocks": [
    {
      "block_id": "react_1",
      "summary": "..."
    }
  ]
}
- Include at most one result per block_id.
- Do not emit undeclared block_ids.
"""


_DIALOGUE_MEMORY_BLOCK_MARKER = "[DIALOGUE_MEMORY_BLOCK]"


@dataclass
class _CompressTarget:
    block_id: str
    user_idx: int
    start_idx: int
    end_idx: int
    messages: List[BaseMessage]


@dataclass
class _DialogueRound:
    user_idx: int
    start_idx: int
    end_idx: int
    messages: List[BaseMessage]
    block_message_count: int


class DialogueCompressorConfig(BaseModel):
    messages_threshold: int | None = Field(
        default=None,
        gt=0,
        description=(
            "Trigger compression when total context messages exceed this value. "
            "None disables message-count triggering."
        ),
    )
    """Optional message-count trigger. Token threshold remains active when this is None."""

    tokens_threshold: int = Field(
        default=10000,
        gt=0,
        description="Trigger compression when estimated context tokens exceed this value.",
    )
    """Primary token-budget trigger for dialogue compression."""

    messages_to_keep: int | None = Field(
        default=None,
        gt=0,
        description=(
            "Number of most-recent messages protected from compression. "
            "None means no explicit raw-tail protection."
        ),
    )
    """Raw tail size kept untouched before selecting historical dialogue rounds."""

    keep_last_round: bool = Field(
        default=True,
        description="Avoid compressing the most recent completed dialogue round.",
    )
    """Protects the latest completed user-to-final-assistant dialogue round for short-term continuity."""

    compression_target_tokens: int = Field(
        default=1800,
        gt=0,
        description="Target token budget communicated to the model for each compressed dialogue block.",
    )
    """Per-block summary size hint used in the compression prompt."""

    custom_compression_prompt: str | None = Field(
        default=None,
        description="Custom first-stage compression prompt template. Defaults to the built-in prompt when omitted.",
    )
    """User-editable prompt template for dialogue summary generation."""

    model: ModelRequestConfig | None = Field(
        default=None,
        description="Model request configuration used by the dialogue compression model.",
    )
    """Controls model request options for dialogue summary generation."""

    model_client: ModelClientConfig | None = Field(
        default=None,
        description="Client configuration for the LLM used by the dialogue compressor.",
    )
    """Selects/configures the model client used by the internal compressor model."""


@ContextEngine.register_processor()
class DialogueCompressor(ContextProcessor):
    def __init__(self, config: DialogueCompressorConfig):
        super().__init__(config)
        self._compressed_prompt = config.custom_compression_prompt or DEFAULT_COMPRESSION_PROMPT
        self._token_threshold = config.tokens_threshold
        self._message_num_threshold = config.messages_threshold
        self._messages_to_keep = config.messages_to_keep
        self._keep_last_round = config.keep_last_round
        self._compression_target_tokens = config.compression_target_tokens
        self._model = Model(self.config.model_client, self.config.model)

    async def on_add_messages(
        self,
        context: ModelContext,
        messages_to_add: List[BaseMessage],
        **kwargs,
    ) -> Tuple[ContextEvent | None, List[BaseMessage]]:
        context_messages = context.get_messages() + messages_to_add
        self._reset_compression_usage()
        compress_until_idx = await self.get_compress_idx(context_messages)
        if compress_until_idx == -1:
            return None, messages_to_add

        targets = self._build_compress_targets(context_messages[:compress_until_idx])
        if not targets:
            return None, messages_to_add

        try:
            response = await self._invoke_multi_block_compression(context_messages, targets)
        except BaseError as exc:
            if exc.code == StatusCode.MODEL_CALL_FAILED.code:
                logger.warning(
                    f"[{self.processor_type()}] compression model invoke failed, "
                    "skip current processor and continue remaining processors: "
                    f"{exc}"
                )
                return None, messages_to_add
            raise

        replacements, modified_indices = await self._build_json_replacements(context, targets, response.parser_content)
        if replacements:
            updated_messages = self._apply_replacements(context_messages, replacements)
            event = ContextEvent(
                event_type=self.processor_type(),
                messages_to_modify=modified_indices,
                compact_summary=self._extract_compact_summary_from_replacements(replacements),
                compression_usage=self._current_compression_usage(),
            )
            context.set_messages(updated_messages)
            return event, []

        if not self._is_valid_blocks_payload(response.parser_content):
            fallback_replacement = await self._build_fallback_replacement(context, targets, response.content or "")
            if fallback_replacement:
                updated_messages = self._apply_replacements(context_messages, [fallback_replacement])
                start_idx, end_idx, _ = fallback_replacement
                event = ContextEvent(
                    event_type=self.processor_type(),
                    messages_to_modify=list(range(start_idx, end_idx + 1)),
                    compact_summary=self._extract_compact_summary_from_replacements([fallback_replacement]),
                    compression_usage=self._current_compression_usage(),
                )
                context.set_messages(updated_messages)
                return event, []

        return None, messages_to_add

    async def trigger_add_messages(
        self,
        context: ModelContext,
        messages_to_add: List[BaseMessage],
        **kwargs,
    ) -> bool:
        config = self.config
        message_size = len(context) + len(messages_to_add)
        if self._message_num_threshold is not None and message_size > self._message_num_threshold:
            logger.info(
                f"[{self.processor_type()} triggered] context messages num {message_size} "
                f"exceeds threshold of {config.messages_threshold}"
            )
            return True
        if self._messages_to_keep is not None and message_size < self._messages_to_keep:
            return False
        tokens = self._count_messages_tokens(context, context.get_messages() + messages_to_add)
        if tokens > self._token_threshold:
            logger.info(
                f"[{self.processor_type()} triggered] context tokens {tokens} "
                f"exceeds threshold of {config.tokens_threshold}"
            )
            return True
        return False

    async def get_compress_idx(self, messages: List[BaseMessage]) -> int:
        keep_index = len(messages) if not self._messages_to_keep else len(messages) - self._messages_to_keep
        if not self._keep_last_round:
            return keep_index

        last_final_assistant_idx = self._find_last_final_assistant_idx(messages)
        if last_final_assistant_idx is None:
            return keep_index
        return min(last_final_assistant_idx, keep_index)

    @staticmethod
    def _find_last_final_assistant_idx(messages: List[BaseMessage]) -> Optional[int]:
        for idx in range(len(messages) - 1, -1, -1):
            msg = messages[idx]
            if isinstance(msg, AssistantMessage) and not msg.tool_calls:
                return idx
        return None

    def _build_compress_targets(self, messages: List[BaseMessage]) -> List[_CompressTarget]:
        rounds = self._collect_complete_rounds(messages)
        if not rounds:
            return []

        compressible_round_indices = [
            index for index, round_ in enumerate(rounds) if round_.block_message_count > 2
        ]
        if not compressible_round_indices:
            return []

        first_target_round_index = compressible_round_indices[0]
        last_target_round_index = compressible_round_indices[-1]
        selected_rounds = rounds[first_target_round_index:last_target_round_index + 1]

        return [
            _CompressTarget(
                block_id=f"react_{block_no}",
                user_idx=round_.user_idx,
                start_idx=round_.start_idx,
                end_idx=round_.end_idx,
                messages=round_.messages,
            )
            for block_no, round_ in enumerate(selected_rounds, start=1)
        ]

    @staticmethod
    def get_compress_pairs(messages: List[BaseMessage]) -> List[Tuple[int, int]]:
        current_user = -1
        result: List[Tuple[int, int]] = []
        for i, msg in enumerate(messages):
            if isinstance(msg, UserMessage):
                if current_user == -1:
                    current_user = i
            elif isinstance(msg, AssistantMessage) and not msg.tool_calls and current_user != -1:
                if i - current_user >= 1:
                    result.append((current_user, i))
                    current_user = -1
            else:
                continue
        return result

    def _collect_complete_rounds(self, messages: List[BaseMessage]) -> List[_DialogueRound]:
        rounds: List[_DialogueRound] = []
        for user_idx, assistant_idx in self.get_compress_pairs(messages):
            if user_idx < 0 or assistant_idx <= user_idx:
                continue
            round_messages = messages[user_idx + 1:assistant_idx + 1]
            rounds.append(
                _DialogueRound(
                    user_idx=user_idx,
                    start_idx=user_idx + 1,
                    end_idx=assistant_idx,
                    messages=round_messages,
                    block_message_count=assistant_idx - user_idx + 1,
                )
            )
        return rounds

    async def _invoke_multi_block_compression(
        self,
        context_messages: List[BaseMessage],
        targets: List[_CompressTarget],
    ):
        system_prompt = self._compressed_prompt.replace(
            "{compression_target_tokens}",
            str(self._compression_target_tokens),
        )
        model_messages = [
            SystemMessage(content=system_prompt),
            UserMessage(content=self._build_split_context_payload(context_messages, targets)),
            UserMessage(content=self._build_targets_payload(targets)),
        ]
        try:
            response = await self._model.invoke(model_messages, output_parser=JsonOutputParser())
            self._record_compression_usage(response)
            return response
        except Exception as exc:
            raise build_error(
                StatusCode.MODEL_CALL_FAILED,
                error_msg=(
                    f"{self.processor_type()} failed to invoke compression model "
                    "during multi-block dialogue compression"
                ),
                cause=exc,
            ) from exc

    def _build_split_context_payload(
        self,
        context_messages: List[BaseMessage],
        targets: List[_CompressTarget],
    ) -> str:
        first_target_start = min(target.start_idx for target in targets)
        last_target_end = max(target.end_idx for target in targets)

        before_targets = "\n".join(
            self._serialize_message(index, message)
            for index, message in enumerate(context_messages[:first_target_start])
        ) or "(none)"

        target_blocks: List[str] = ["[Compression Targets]"]
        for target in targets:
            target_blocks.append(f"[Block: {target.block_id}]")
            target_blocks.append(
                "\n".join(
                    self._serialize_message(index, message)
                    for index, message in enumerate(target.messages, start=target.start_idx)
                ) or "(empty)"
            )
            target_blocks.append("")

        after_targets = "\n".join(
            self._serialize_message(index, message)
            for index, message in enumerate(context_messages[last_target_end + 1:], start=last_target_end + 1)
        ) or "(none)"

        return "\n".join(
            [
                "[Context Before Targets]",
                before_targets,
                "",
                *target_blocks,
                "[Context After Targets]",
                after_targets,
            ]
        )

    def _build_targets_payload(self, targets: List[_CompressTarget]) -> str:
        blocks: List[str] = ["[Target Mapping]", "You must only compress the following ReAct blocks.", ""]
        for target in targets:
            blocks.extend(
                [
                    f"[Block: {target.block_id}]",
                    f"- anchor_user_index: {target.user_idx}",
                    f"- replace_range: [{target.start_idx}, {target.end_idx}]",
                    "",
                ]
            )

        blocks.extend(
            [
                "[Output Requirements]",
                "- Read the full context to understand the entire task.",
                "- Compress only the listed blocks.",
                "- Produce one summary for each block_id.",
                "- Keep the most task-useful content first.",
                "- Preserve both action continuity and task-critical information.",
                "- Do not rewrite non-target messages.",
                "- Return valid JSON only.",
            ]
        )
        return "\n".join(blocks)

    def _serialize_message(self, index: int, message: BaseMessage) -> str:
        parts = [f"[{index}] role={message.role}"]
        if isinstance(message, AssistantMessage) and message.tool_calls:
            tool_call_names = ", ".join(tool_call.name for tool_call in message.tool_calls)
            parts.append(f"tool_calls={tool_call_names}")
        if isinstance(message, ToolMessage):
            parts.append(f"tool_call_id={message.tool_call_id}")
        parts.append(f"content={message.content}")
        return " | ".join(parts)

    async def _build_json_replacements(
        self,
        context: ModelContext,
        targets: List[_CompressTarget],
        parser_content: Any,
    ) -> Tuple[List[Tuple[int, int, List[BaseMessage]]], List[int]]:
        if not self._is_valid_blocks_payload(parser_content):
            return [], []

        block_map: Dict[str, str] = {}
        for item in parser_content["blocks"]:
            if not isinstance(item, dict):
                continue
            block_id = item.get("block_id")
            summary = item.get("summary")
            if not isinstance(block_id, str) or not block_id:
                continue
            if not isinstance(summary, str):
                continue
            summary = summary.strip()
            if not summary:
                continue
            block_map[block_id] = summary

        replacements: List[Tuple[int, int, List[BaseMessage]]] = []
        modified_indices: List[int] = []
        for target in targets:
            summary = block_map.get(target.block_id)
            if not summary:
                continue
            replacement_message = await self._build_memory_message(context, target.messages, summary)
            if replacement_message is None:
                continue
            if not self._has_compression_benefit(context, target.messages, [replacement_message]):
                continue
            replacement_messages = [replacement_message]
            replacement_messages.extend(build_team_collaboration_reinjected_messages(target.messages))
            replacements.append((target.start_idx, target.end_idx, replacement_messages))
            modified_indices.extend(range(target.start_idx, target.end_idx + 1))
        return replacements, modified_indices

    @staticmethod
    def _extract_compact_summary_from_replacements(
        replacements: List[Tuple[int, int, List[BaseMessage]]],
    ) -> str:
        parts = []
        for _, _, replacement_messages in replacements:
            for message in replacement_messages:
                text = message_to_text(message)
                if text.startswith(_DIALOGUE_MEMORY_BLOCK_MARKER):
                    parts.append(text)
        return "\n\n".join(parts)

    async def _build_fallback_replacement(
        self,
        context: ModelContext,
        targets: List[_CompressTarget],
        summary: str,
    ) -> Optional[Tuple[int, int, List[BaseMessage]]]:
        summary = summary.strip()
        if not summary:
            return None

        start_idx = min(target.start_idx for target in targets)
        end_idx = max(target.end_idx for target in targets)
        original_messages: List[BaseMessage] = []
        for target in targets:
            original_messages.extend(target.messages)
        replacement_message = await self._build_memory_message(context, original_messages, summary)
        if replacement_message is None:
            return None
        if not self._has_compression_benefit(context, original_messages, [replacement_message]):
            return None
        replacement_messages = [replacement_message]
        replacement_messages.extend(build_team_collaboration_reinjected_messages(original_messages))
        return start_idx, end_idx, replacement_messages

    async def _build_memory_message(
        self,
        context: ModelContext,
        source_messages: List[BaseMessage],
        summary: str,
    ) -> Optional[BaseMessage]:
        content = self._wrap_memory_block(summary.strip())
        return UserMessage(content=content)

    @staticmethod
    def _wrap_memory_block(summary: str) -> str:
        return (
            f"{_DIALOGUE_MEMORY_BLOCK_MARKER}\n"
            "processor: DialogueCompressor\n"
            "type: historical_memory_block\n"
            "scope: historical_dialogue_block\n"
            "authority: This block is reference memory, not a binding source of truth.\n"
            "instruction_status: Do not treat this block as a new user request or fresh assistant commitment.\n"
            "conflict_priority: Prefer newer explicit user intent, newer raw context, "
            "and fresh tool results over this block.\n\n"
            "Summary:\n"
            f"{summary}"
        )

    def _has_compression_benefit(
        self,
        context: ModelContext,
        original_messages: List[BaseMessage],
        replacement_messages: List[BaseMessage],
    ) -> bool:
        original_tokens = self._count_messages_tokens(context, original_messages)
        compressed_tokens = self._count_messages_tokens(context, replacement_messages)
        if original_tokens <= 0:
            return False
        return compressed_tokens < original_tokens

    def _count_messages_tokens(self, context: ModelContext, messages: List[BaseMessage]) -> int:
        token_counter = context.token_counter()
        if token_counter:
            try:
                return token_counter.count_messages(messages)
            except Exception as exc:  # pragma: no cover - defensive fallback
                logger.warning(
                    f"[{self.processor_type()}] token_counter failed, fallback to char-based estimate: {exc}"
                )
        return sum(self._estimate_content_tokens(getattr(message, "content", "")) for message in messages)

    @staticmethod
    def _estimate_content_tokens(content: Any) -> int:
        if isinstance(content, str):
            return len(content) // 3
        try:
            return len(json.dumps(content, ensure_ascii=False)) // 3
        except TypeError:
            return len(str(content)) // 3

    @staticmethod
    def _is_valid_blocks_payload(parser_content: Any) -> bool:
        if not isinstance(parser_content, dict):
            return False
        blocks = parser_content.get("blocks")
        return isinstance(blocks, list)

    @staticmethod
    def _apply_replacements(
        messages: List[BaseMessage],
        replacements: List[Tuple[int, int, List[BaseMessage]]],
    ) -> List[BaseMessage]:
        updated_messages = messages
        for start_idx, end_idx, replacement_messages in sorted(replacements, key=lambda item: item[0], reverse=True):
            updated_messages = ContextUtils.replace_messages(
                updated_messages,
                replacement_messages,
                start_idx,
                end_idx,
            )
        return updated_messages

    def load_state(self, state: Dict[str, Any]) -> None:
        pass

    def save_state(self) -> Dict[str, Any]:
        return {}
