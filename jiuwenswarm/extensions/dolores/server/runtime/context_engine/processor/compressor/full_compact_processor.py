# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field
from openjiuwen.core.common.logging import logger
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.base import ModelContext
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.context.context_utils import ContextUtils
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.context.session_memory_manager import (
    find_last_completed_api_round_end,
    find_message_index_by_context_message_id,
    group_completed_api_rounds,
    invalidate_session_memory_anchor,
)
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.context_engine import ContextEngine
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.base import ContextEvent, ContextProcessor
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.compressor.util import (
    FullCompactStateReinjector,
    build_plan_mode_reinjected_content,
    build_task_status_reinjected_content,
    build_skill_reinjected_content,
)
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    BaseMessage,
    Model,
    ModelClientConfig,
    ModelRequestConfig,
    SystemMessage,
    ToolMessage,
    UserMessage,
)

NO_TOOLS_PREAMBLE = """CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.

- Do NOT use Read, Bash, Grep, Glob, Edit, Write, or ANY other tool.
- You already have all the context you need in the conversation above.
- Tool calls will be REJECTED and will waste your only turn — you will fail the task.
- Your entire response must be plain text: an <analysis> block followed by a <summary> block.

"""

DETAILED_ANALYSIS_INSTRUCTION = """Before providing your final summary, wrap your analysis in <analysis> 
tags to organize your thoughts and ensure you've covered all necessary points. In your analysis process:

1. Chronologically analyze each message and section of the conversation. For each section thoroughly identify:
   - The user's explicit requests and intents
   - Your approach to addressing the user's requests
   - Key decisions, technical concepts and code patterns
   - Specific details like:
     - file names
     - full code snippets
     - function signatures
     - file edits
   - Errors that you ran into and how you fixed them
   - Pay special attention to specific user feedback that you received, especially if the user told you to do something 
     differently.
2. Double-check for technical accuracy and completeness, addressing each required element thoroughly.
"""

BASE_COMPACT_PROMPT = (
    NO_TOOLS_PREAMBLE
    + """Your task is to create a detailed summary of the conversation so far, 
    paying close attention to the user's explicit requests and your previous actions.
This summary should be thorough in capturing technical details, code patterns, 
and architectural decisions that would be essential for continuing development work without losing context.

"""
    + DETAILED_ANALYSIS_INSTRUCTION
    + """
Your summary should include the following sections:

1. Primary Request and Intent: Capture all of the user's explicit requests and intents in detail
2. Key Technical Concepts: List all important technical concepts, technologies, and frameworks discussed.
3. Files and Code Sections: Enumerate specific files and code sections examined, modified, or created. 
    Pay special attention to the most recent messages and include full code snippets where applicable and 
    include a summary of why this file read or edit is important.
4. Errors and fixes: List all errors that you ran into, and how you fixed them. 
    Pay special attention to specific user feedback that you received, 
    especially if the user told you to do something differently.
5. Problem Solving: Document problems solved and any ongoing troubleshooting efforts.
6. All user messages: List ALL user messages that are not tool results. 
    These are critical for understanding the users' feedback and changing intent.
7. Pending Tasks: Outline any pending tasks that you have explicitly been asked to work on.
8. Current Work: Describe in detail precisely what was being worked on immediately before this summary request, 
    paying special attention to the most recent messages from both user and assistant. 
    Include file names and code snippets where applicable.
9. Optional Next Step: List the next step that you will take that is related to the most recent work you were doing. 
    IMPORTANT: ensure that this step is DIRECTLY in line with the user's most recent explicit requests, 
    and the task you were working on immediately before this summary request. If your last task was concluded, 
    then only list next steps if they are explicitly in line with the users request. 
    Do not start on tangential requests or really old requests that were already completed without 
    confirming with the user first.
    If there is a next step, include direct quotes from the most recent conversation showing exactly what task you were
    working on and where you left off. 
    This should be verbatim to ensure there's no drift in task interpretation.

Here's an example of how your output should be structured:

<example>
<analysis>
[Your thought process, ensuring all points are covered thoroughly and accurately]
</analysis>

<summary>
1. Primary Request and Intent:
   [Detailed description]

2. Key Technical Concepts:
   - [Concept 1]
   - [Concept 2]
   - [...]

3. Files and Code Sections:
   - [File Name 1]
      - [Summary of why this file is important]
      - [Summary of the changes made to this file, if any]
      - [Important Code Snippet]
   - [File Name 2]
      - [Important Code Snippet]
   - [...]

4. Errors and fixes:
    - [Detailed description of error 1]:
      - [How you fixed the error]
      - [User feedback on the error if any]
    - [...]

5. Problem Solving:
   [Description of solved problems and ongoing troubleshooting]

6. All user messages:
    - [Detailed non tool use user message]
    - [...]

7. Pending Tasks:
   - [Task 1]
   - [Task 2]
   - [...]

8. Current Work:
   [Precise description of current work]

9. Optional Next Step:
   [Optional Next step to take]

</summary>
</example>

Please provide your summary based on the conversation so far, 
following this structure and ensuring precision and thoroughness in your response.
"""
    + "\n\nREMINDER: Do NOT call any tools. "
    "Respond with plain text only — an <analysis> block followed by a <summary> block. "
    "Tool calls will be rejected and you will fail the task."
)


FULL_COMPACT_BOUNDARY_MARKER = "[FULL_COMPACT_BOUNDARY]"
FULL_COMPACT_STATE_MARKER = "[FULL_COMPACT_STATE]"
SESSION_MEMORY_BOUNDARY_MARKER = "[SESSION_MEMORY_BOUNDARY]"
FULL_COMPACT_SYNTHETIC_USER_MARKER = "[earlier conversation truncated for compaction retry]"
FULL_COMPACT_SUMMARY_INTRO = (
    "This session is being continued from a previous conversation that "
    "ran out of context. The summary below covers the earlier portion "
    "of the conversation."
)
FULL_COMPACT_RECENT_MESSAGES_NOTICE = "Recent messages are preserved verbatim."
SESSION_MEMORY_SUMMARY_INTRO = (
    "Earlier conversation has been replaced with the session memory file. "
    "Use it as the canonical summary of prior work."
)


class FullCompactProcessorConfig(BaseModel):
    trigger_total_tokens: int = Field(
        default=180000,
        gt=0,
        description="Trigger full compaction when the estimated context window exceeds this token count.",
    )
    compression_call_max_tokens: int = Field(
        default=200000,
        gt=0,
        description="Maximum token budget for the internal summary-generation prompt.",
    )
    messages_to_keep: int = Field(
        default=10,
        ge=0,
        description="Number of most-recent active messages preserved verbatim after full compaction.",
    )
    session_memory_enabled: bool = Field(
        default=True,
        description="Prefer committed session memory notes before falling back to LLM full compaction.",
    )

    model: ModelRequestConfig | None = Field(default=None)
    """Model request configuration used by the full-compaction summarizer."""

    model_client: ModelClientConfig | None = Field(default=None)
    """Client configuration used to create the full-compaction summarizer model."""

    # Advanced retention and state reinjection settings.
    keep_tool_message_pairs: bool = Field(
        default=True,
        description="When preserving recent tool results, also keep their matching assistant tool-call messages.",
    )
    state_snapshot_max_chars: int = Field(
        default=4000,
        gt=0,
        description="Maximum characters retained for each reinjected state snapshot.",
    )
    reinject_recent_skills: int = Field(
        default=3,
        ge=0,
        description="Maximum number of recent skill-read rounds reinjected after full compaction.",
    )
    reinject_file_tool_names: List[str] = Field(
        default=["read_file", "write_file", "edit_file", "glob", "grep"],
        description="Tool names eligible for file-related state reinjection.",
    )
    reinject_tool_result_hint_names: List[str] = Field(
        default=["read_file", "write_file", "edit_file", "glob", "grep"],
        description="Tool names eligible for compact tool-result hints.",
    )

    # Advanced marker and prompt text settings.
    marker: str = Field(default=FULL_COMPACT_BOUNDARY_MARKER)
    state_marker: str = Field(default=FULL_COMPACT_STATE_MARKER)
    synthetic_user_marker: str = Field(default=FULL_COMPACT_SYNTHETIC_USER_MARKER)
    summary_intro: str = Field(default=FULL_COMPACT_SUMMARY_INTRO)
    recent_messages_notice: str = Field(default=FULL_COMPACT_RECENT_MESSAGES_NOTICE)
    session_memory_marker: str = Field(default=SESSION_MEMORY_BOUNDARY_MARKER)
    session_memory_intro: str = Field(default=SESSION_MEMORY_SUMMARY_INTRO)


@ContextEngine.register_processor()
class FullCompactProcessor(ContextProcessor):
    """Fallback compactor aligned with Claude Code's full compact flow."""

    def __init__(self, config: FullCompactProcessorConfig):
        super().__init__(config)
        self._trigger_total_tokens = config.trigger_total_tokens
        self._compression_call_max_tokens = config.compression_call_max_tokens
        self._messages_to_keep = config.messages_to_keep
        self._keep_tool_message_pairs = config.keep_tool_message_pairs
        self._state_snapshot_max_chars = config.state_snapshot_max_chars
        self._marker = config.marker
        self._state_marker = config.state_marker
        self._synthetic_user_marker = config.synthetic_user_marker
        self._summary_intro = config.summary_intro
        self._recent_messages_notice = config.recent_messages_notice
        self._session_memory_enabled = config.session_memory_enabled
        self._session_memory_marker = config.session_memory_marker
        self._session_memory_intro = config.session_memory_intro
        self._state_reinjector = FullCompactStateReinjector()
        self._state_reinjector.register_builder(
            name="skills",
            label="SKILLS",
            builder=build_skill_reinjected_content,
        )
        self._state_reinjector.register_builder(
            name="task_status",
            label="TASK_STATUS",
            builder=build_task_status_reinjected_content,
        )
        self._state_reinjector.register_builder(
            name="plan_mode",
            label="PLAN_MODE",
            builder=build_plan_mode_reinjected_content,
        )
        self._model: Model | None = None
        if config.model is not None and config.model_client is not None:
            self._model = Model(config.model_client, config.model)

    @property
    def config(self) -> FullCompactProcessorConfig:
        return self._config

    @property
    def state_marker(self) -> str:
        return self._state_marker

    @property
    def advanced_config(self) -> FullCompactProcessorConfig:
        return self.config

    async def trigger_add_messages(
        self,
        context: ModelContext,
        messages_to_add: List[BaseMessage],
        **kwargs: Any,
    ) -> bool:
        candidate_messages = context.get_messages() + list(messages_to_add or [])
        if not self._api_round(candidate_messages):
            return False
        candidate_tokens = self._count_context_window_tokens(
            system_messages=[],
            context_messages=candidate_messages,
            tools=[],
            context=context,
        )
        triggered = candidate_tokens > self._trigger_total_tokens
        return triggered

    async def on_add_messages(
        self,
        context: ModelContext,
        messages_to_add: List[BaseMessage],
        **kwargs: Any,
    ) -> Tuple[ContextEvent | None, List[BaseMessage]]:
        all_messages = context.get_messages() + list(messages_to_add or [])
        self._reset_compression_usage()
        event, new_context_messages, session_memory_message = await self._build_replacement_messages(
            context,
            all_messages,
        )
        if new_context_messages is None:
            return None, messages_to_add
        context.set_messages(new_context_messages)
        if event is not None:
            event.compression_usage = self._current_compression_usage()
        if session_memory_message is None:
            self._invalidate_session_memory_anchor(context)
        return event, []

    async def _build_replacement_messages(
        self,
        context: ModelContext,
        all_messages: List[BaseMessage],
    ) -> Tuple[ContextEvent | None, Optional[List[BaseMessage]], Optional[UserMessage]]:
        boundary_index = self._find_last_compaction_boundary_index(all_messages)
        prefix, active_messages = self._split_messages_at_compaction_boundary(
            all_messages,
            boundary_index=boundary_index,
        )
        if not active_messages:
            logger.info("[FullCompact] replacement skipped: no active messages after boundary")
            return None, None, None

        session_memory_messages, session_memory_message = await self._build_session_memory_messages(
            context=context,
            prefix=prefix,
            active_messages=active_messages,
            has_compaction_boundary=boundary_index >= 0,
        )
        if session_memory_messages is not None:
            session_memory_tokens = self._count_context_window_tokens(
                system_messages=[],
                context_messages=session_memory_messages,
                tools=[],
                context=context,
            )
            if session_memory_tokens <= self._trigger_total_tokens:
                logger.info("[FullCompact] using session_memory replacement")
                return (
                    ContextEvent(
                        event_type=self.processor_type(),
                        messages_to_modify=list(range(len(all_messages))),
                        compact_summary=self._message_to_text(session_memory_message),
                    ),
                    session_memory_messages,
                    session_memory_message,
                )
            logger.info("[FullCompact] session_memory candidate rejected: token budget exceeded")
        else:
            logger.info("[FullCompact] session_memory candidate unavailable, fallback to full_compact")

        full_compact_result = await self._build_full_compact_messages(
            context=context,
            prefix=prefix,
            active_messages=active_messages,
        )
        if full_compact_result is None:
            logger.warning("[FullCompact] full_compact candidate build failed")
            return None, None, None
        new_context_messages, compact_summary = full_compact_result
        logger.info(
            "[FullCompact] using full_compact replacement output_messages=%s",
            len(new_context_messages),
        )
        return (
            ContextEvent(
                event_type=self.processor_type(),
                messages_to_modify=list(range(len(all_messages))),
                compact_summary=compact_summary,
            ),
            new_context_messages,
            None,
        )

    async def _build_full_compact_messages(
        self,
        *,
        context: ModelContext,
        prefix: List[BaseMessage],
        active_messages: List[BaseMessage],
    ) -> Optional[Tuple[List[BaseMessage], str]]:

        compact_source = self._prepare_messages_for_prompt(self._strip_media_messages(active_messages))
        if not compact_source:
            return None

        compact_input = self._truncate_for_prompt_budget(compact_source, context)
        if not compact_input:
            return None

        summary = await self._generate_summary(compact_input, context)
        if not summary:
            logger.warning("[FullCompact] full_compact summary generation returned empty content")
            return None

        messages_to_keep = self._select_messages_to_keep(active_messages)
        summary_message = UserMessage(content=self._build_summary_message(summary, bool(messages_to_keep)))
        boundary = SystemMessage(content=f"{self._marker}\nConversation compacted")

        new_context_messages = prefix + [boundary, summary_message]
        new_context_messages.extend(messages_to_keep)
        new_context_messages.extend(
            self.build_reinjected_state_messages(
                context=context,
                source_messages=active_messages,
                messages_to_keep=messages_to_keep,
                summary_message=summary_message,
                boundary_message=boundary,
                builder_names=["plan", "plan_mode", "skills", "task_status"],
            )
        )
        return new_context_messages, summary

    async def _build_session_memory_messages(
        self,
        *,
        context: ModelContext,
        prefix: List[BaseMessage],
        active_messages: List[BaseMessage],
        has_compaction_boundary: bool,
    ) -> Tuple[Optional[List[BaseMessage]], Optional[UserMessage]]:
        if not self._session_memory_enabled:
            logger.info("[FullCompact] session_memory disabled")
            return None, None

        session_memory_runtime = self._load_session_memory_runtime(context)
        if session_memory_runtime.get("is_extracting"):
            logger.info("[FullCompact] session_memory extraction in progress, using latest committed notes")
        session_memory_text = self._load_session_memory_text(context, session_memory_runtime)
        if not session_memory_text:
            logger.info("[FullCompact] session_memory unavailable: empty notes content or unresolved path")
            return None, None

        preserved_messages = self._select_messages_after_session_memory(
            active_messages=active_messages,
            session_memory_runtime=session_memory_runtime,
            has_compaction_boundary=has_compaction_boundary,
        )
        if preserved_messages is None:
            logger.info(
                "[FullCompact] session_memory skipped: no valid active anchor has_boundary=%s active=%s notes_upto=%s",
                has_compaction_boundary,
                len(active_messages),
                session_memory_runtime.get("notes_upto_message_id"),
            )
            return None, None

        boundary = SystemMessage(
            content=f"{self._session_memory_marker}\nEarlier conversation replaced with session memory"
        )
        session_memory_message = UserMessage(
            content=self._build_session_memory_message(session_memory_text, bool(preserved_messages))
        )
        candidate_messages = prefix + [boundary, session_memory_message]
        candidate_messages.extend(preserved_messages)
        candidate_messages.extend(
            self.build_reinjected_state_messages(
                context=context,
                source_messages=active_messages,
                messages_to_keep=preserved_messages,
                summary_message=session_memory_message,
                boundary_message=boundary,
                builder_names=["plan"],
            )
        )
        return candidate_messages, session_memory_message

    def _split_messages_at_compaction_boundary(
        self,
        messages: List[BaseMessage],
        boundary_index: Optional[int] = None,
    ) -> Tuple[List[BaseMessage], List[BaseMessage]]:
        if boundary_index is None:
            boundary_index = self._find_last_compaction_boundary_index(messages)
        prefix = messages[:boundary_index] if boundary_index > 0 else []
        active_messages = messages[boundary_index + 1:] if boundary_index >= 0 else list(messages)
        return prefix, active_messages

    async def _generate_summary(
        self,
        messages: List[BaseMessage],
        context: ModelContext,
    ) -> str:
        if self._model is None:
            return self._build_fallback_summary(messages)

        prompt_messages = [
            SystemMessage(content=BASE_COMPACT_PROMPT),
            UserMessage(content=self._serialize_messages(messages)),
        ]
        try:
            response = await self._model.invoke(messages=prompt_messages, tools=None)
            self._record_compression_usage(response)
            content = (response.content or "").strip()
            if not content:
                logger.warning("[FullCompact] LLM returned empty summary, falling back")
                return self._build_fallback_summary(messages)
            return self._format_summary(content)
        except Exception as exc:
            logger.warning(
                "[FullCompact] LLM summary generation failed: %s, falling back",
                exc,
                exc_info=True,
            )
            return self._build_fallback_summary(messages)

    def _truncate_for_prompt_budget(
        self,
        messages: List[BaseMessage],
        context: ModelContext,
    ) -> List[BaseMessage]:
        groups = self._group_messages_by_api_round(messages)
        while groups:
            candidate = [msg for group in groups for msg in group]
            if self._count_prompt_tokens(candidate, context) <= self._compression_call_max_tokens:
                return candidate
            if len(groups) == 1:
                return self._truncate_messages_from_head(candidate, context)
            groups = groups[1:]
            if groups and isinstance(groups[0][0], AssistantMessage):
                groups[0] = [UserMessage(content=self._synthetic_user_marker), *groups[0]]
        return self._build_minimal_compact_input(messages)

    def _truncate_messages_from_head(
        self,
        messages: List[BaseMessage],
        context: ModelContext,
    ) -> List[BaseMessage]:
        candidate = list(messages)
        while candidate:
            if self._count_prompt_tokens(candidate, context) <= self._compression_call_max_tokens:
                return candidate
            if self._is_synthetic_marker_message(candidate[0]):
                if len(candidate) == 1:
                    return self._build_minimal_compact_input(messages)
                candidate = candidate[2:]
            else:
                candidate = candidate[1:]
            if candidate and isinstance(candidate[0], AssistantMessage):
                candidate = [UserMessage(content=self._synthetic_user_marker), *candidate]
        return self._build_minimal_compact_input(messages)

    def _group_messages_by_api_round(self, messages: List[BaseMessage]) -> List[List[BaseMessage]]:
        return [list(messages[start:end]) for start, end in group_completed_api_rounds(messages)]

    def _build_minimal_compact_input(self, messages: List[BaseMessage]) -> List[BaseMessage]:
        if not messages:
            return []

        tail: List[BaseMessage] = [messages[-1]]
        if isinstance(tail[0], AssistantMessage):
            return [UserMessage(content=self._synthetic_user_marker), tail[0]]
        return tail

    def _select_messages_to_keep(self, messages: List[BaseMessage]) -> List[BaseMessage]:
        keep_recent = self._messages_to_keep
        if keep_recent <= 0 or not messages:
            return []

        start_index = max(len(messages) - keep_recent, 0)
        if self._keep_tool_message_pairs:
            start_index = self._adjust_start_index_for_tool_pairs(messages, start_index)
        return list(messages[start_index:])

    def _adjust_start_index_for_tool_pairs(
        self,
        messages: List[BaseMessage],
        start_index: int,
    ) -> int:
        if start_index <= 0 or start_index >= len(messages):
            return start_index

        adjusted = start_index
        needed_tool_ids = {
            msg.tool_call_id
            for msg in messages[start_index:]
            if isinstance(msg, ToolMessage) and getattr(msg, "tool_call_id", None)
        }
        if not needed_tool_ids:
            return adjusted

        present_tool_calls = set()
        for msg in messages[start_index:]:
            if isinstance(msg, AssistantMessage):
                for tool_call in getattr(msg, "tool_calls", None) or []:
                    tool_call_id = getattr(tool_call, "id", None)
                    if tool_call_id:
                        present_tool_calls.add(tool_call_id)

        missing_tool_calls = needed_tool_ids - present_tool_calls
        if not missing_tool_calls:
            return adjusted

        for index in range(start_index - 1, -1, -1):
            msg = messages[index]
            if not isinstance(msg, AssistantMessage):
                continue
            tool_calls = getattr(msg, "tool_calls", None) or []
            matched = False
            for tool_call in tool_calls:
                tool_call_id = getattr(tool_call, "id", None)
                if tool_call_id in missing_tool_calls:
                    missing_tool_calls.discard(tool_call_id)
                    matched = True
            if matched:
                adjusted = index
            if not missing_tool_calls:
                break
        return adjusted

    @staticmethod
    def _strip_media_messages(messages: List[BaseMessage]) -> List[BaseMessage]:
        # Media-heavy message preprocessing is pending; current behavior passes them through unchanged.
        return messages

    def _prepare_messages_for_prompt(self, messages: List[BaseMessage]) -> List[BaseMessage]:
        result: List[BaseMessage] = []
        for msg in messages:
            if (
                self._is_boundary_message(msg)
                or self._is_state_message(msg)
                or self._is_session_memory_boundary_message(msg)
            ):
                continue
            result.append(msg)
        return result

    def _build_session_memory_message(self, session_memory_text: str, has_preserved_messages: bool) -> str:
        parts = [self._session_memory_intro, "", session_memory_text.strip()]
        if has_preserved_messages:
            parts.extend(["", self._recent_messages_notice])
        return "\n".join(parts)

    def _load_session_memory_text(
        self,
        context: ModelContext,
        session_memory_runtime: Optional[Dict[str, Any]] = None,
    ) -> str:
        session_memory_path = self._resolve_session_memory_path(
            context,
            session_memory_runtime=session_memory_runtime,
        )
        if session_memory_path is None:
            return ""
        try:
            content = session_memory_path.read_text(encoding="utf-8").strip()
        except Exception:
            return ""
        if content:
            return content
        return ""

    def _load_session_memory_runtime(self, context: ModelContext) -> Dict[str, Any]:
        session = getattr(context, "_session_ref", None)
        if session is not None and hasattr(session, "get_state"):
            state = session.get_state("__session_memory__") or {}
            session_id = ""
            if hasattr(session, "get_session_id"):
                try:
                    session_id = session.get_session_id()
                except Exception:
                    session_id = ""
            logger.info(
                "[FullCompact] load_session_memory_runtime session_obj=%s session_id=%s state=%s",
                hex(id(session)),
                session_id,
                state if not isinstance(state, dict) else {
                    "memory_path": state.get("memory_path"),
                    "initialized": state.get("initialized"),
                    "is_extracting": state.get("is_extracting"),
                    "tokens_at_last_update": state.get("tokens_at_last_update"),
                    "tool_calls_at_last_update": state.get("tool_calls_at_last_update"),
                    "last_summarized_message_count": state.get("last_summarized_message_count"),
                    "notes_upto_message_id": state.get("notes_upto_message_id"),
                },
            )
            if isinstance(state, dict) and state:
                return dict(state)
        return {}

    def _resolve_session_memory_path(
        self,
        context: ModelContext,
        session_memory_runtime: Optional[Dict[str, Any]] = None,
    ) -> Optional[Path]:
        runtime = session_memory_runtime or self._load_session_memory_runtime(context)
        memory_path = runtime.get("memory_path")
        if not memory_path:
            return None
        try:
            return Path(memory_path)
        except Exception:
            return None

    def _select_messages_after_session_memory(
        self,
        *,
        active_messages: List[BaseMessage],
        session_memory_runtime: Dict[str, Any],
        has_compaction_boundary: bool,
    ) -> Optional[List[BaseMessage]]:
        notes_upto_message_id = session_memory_runtime.get("notes_upto_message_id")
        summarized_message_index = find_message_index_by_context_message_id(
            active_messages,
            notes_upto_message_id,
        )
        if summarized_message_index >= 0:
            if self._is_session_memory_summary_message(active_messages[summarized_message_index]):
                return list(active_messages[summarized_message_index + 1:])
            completed_end = find_last_completed_api_round_end(active_messages[: summarized_message_index + 1])
            if completed_end <= 0:
                return None
            return list(active_messages[completed_end:])

        if has_compaction_boundary:
            logger.info(
                "[FullCompact] session_memory context-id anchor missing "
                "in active segment after boundary notes_upto=%s active=%s",
                notes_upto_message_id,
                len(active_messages),
            )
            return None

        logger.info(
            "[FullCompact] session_memory no valid context-id anchor before first boundary notes_upto=%s active=%s",
            notes_upto_message_id,
            len(active_messages),
        )
        return None

    @staticmethod
    def _invalidate_session_memory_anchor(context: ModelContext) -> None:
        session = getattr(context, "_session_ref", None)
        invalidate_session_memory_anchor(session)

    def _build_summary_message(self, summary: str, has_preserved_messages: bool) -> str:
        parts = [self._summary_intro, "", summary]
        if has_preserved_messages:
            parts.extend(["", self._recent_messages_notice])
        return "\n".join(parts)

    def build_reinjected_state_messages(
        self,
        *,
        context: ModelContext,
        source_messages: List[BaseMessage],
        messages_to_keep: List[BaseMessage],
        summary_message: UserMessage,
        boundary_message: SystemMessage,
        builder_names: Optional[List[str]] = None,
    ) -> List[BaseMessage]:
        _ = context, summary_message, boundary_message
        candidate_messages = self._prepare_messages_for_prompt(source_messages)
        if not candidate_messages:
            return []

        active_builder_names = set(builder_names) if builder_names is not None else None
        state_messages: List[BaseMessage] = []
        for builder_spec in self._state_reinjector.iter_builders():
            if active_builder_names is not None and builder_spec.name not in active_builder_names:
                continue
            content = builder_spec.builder(
                self,
                context=context,
                messages=candidate_messages,
                messages_to_keep=messages_to_keep,
            )
            if isinstance(content, list):
                state_messages.extend(content)
                continue
            if content:
                state_messages.append(self._make_state_message(builder_spec.label, content))
        return state_messages

    def _make_state_message(self, label: str, content: str) -> UserMessage:
        compact_content = self.truncate_state_text(content)
        return UserMessage(content=f"{self._state_marker}\n[{label}]\n{compact_content}")

    def truncate_state_text(self, text: str) -> str:
        if len(text) <= self._state_snapshot_max_chars:
            return text
        return self._build_head_tail_truncated_text(text, self._state_snapshot_max_chars)

    def _count_prompt_tokens(self, messages: List[BaseMessage], context: ModelContext) -> int:
        prompt_messages = [
            SystemMessage(content=BASE_COMPACT_PROMPT),
            UserMessage(content=self._serialize_messages(messages)),
        ]
        token_counter = context.token_counter()
        if token_counter is not None:
            try:
                return token_counter.count_messages(prompt_messages)
            except Exception:
                return sum(self._estimate_message_tokens(message) for message in prompt_messages)
        return sum(self._estimate_message_tokens(message) for message in prompt_messages)

    @staticmethod
    def _count_tool_calls(messages: List[BaseMessage]) -> int:
        total = 0
        for message in messages:
            if isinstance(message, AssistantMessage):
                total += len(getattr(message, "tool_calls", None) or [])
        return total

    def _count_context_window_tokens(
        self,
        system_messages: List[BaseMessage],
        context_messages: List[BaseMessage],
        tools: List[Any],
        context: ModelContext,
    ) -> int:
        token_counter = context.token_counter()
        all_messages = list(system_messages or []) + list(context_messages or [])
        total = 0
        if token_counter is not None:
            try:
                total += token_counter.count_messages(all_messages)
                return total
            except Exception:
                total = 0
        total += sum(self._estimate_message_tokens(message) for message in all_messages)
        return total

    def _build_fallback_summary(self, messages: List[BaseMessage]) -> str:
        lines = []
        for idx, msg in enumerate(messages[-20:], start=max(len(messages) - 19, 1)):
            lines.append(f"[{idx}] {msg.role}: {self._message_to_text(msg)}")
        return "Summary:\n" + "\n".join(lines)

    @staticmethod
    def _format_summary(content: str) -> str:
        stripped = re.sub(r"<analysis>[\s\S]*?</analysis>", "", content).strip()
        match = re.search(r"<summary>([\s\S]*?)</summary>", stripped)
        if match:
            return "Summary:\n" + match.group(1).strip()
        return stripped

    @staticmethod
    def _serialize_messages(messages: List[BaseMessage]) -> str:
        return "\n".join(FullCompactProcessor._serialize_message(msg) for msg in messages)

    @staticmethod
    def _serialize_message(message: BaseMessage) -> str:
        parts = [f"role={message.role}"]
        if isinstance(message, AssistantMessage):
            tool_calls = getattr(message, "tool_calls", None) or []
            if tool_calls:
                serialized_tool_calls = []
                for tool_call in tool_calls:
                    serialized_tool_calls.append(
                        {
                            "id": getattr(tool_call, "id", ""),
                            "name": getattr(tool_call, "name", ""),
                            "arguments": getattr(tool_call, "arguments", ""),
                            "type": getattr(tool_call, "type", ""),
                        }
                    )
                parts.append(f"tool_calls={json.dumps(serialized_tool_calls, ensure_ascii=False)}")
        if isinstance(message, ToolMessage):
            parts.append(f"tool_call_id={message.tool_call_id}")
        parts.append(f"content={FullCompactProcessor._message_to_text(message)}")
        return " | ".join(parts)

    @staticmethod
    def _message_to_text(message: BaseMessage) -> str:
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content
        try:
            return json.dumps(content, ensure_ascii=False)
        except TypeError:
            return str(content)

    @staticmethod
    def _estimate_message_tokens(message: BaseMessage) -> int:
        return ContextUtils.estimate_message_tokens(message)

    def _find_last_compaction_boundary_index(self, messages: List[BaseMessage]) -> int:
        for idx in range(len(messages) - 1, -1, -1):
            if self._is_boundary_message(messages[idx]) or self._is_session_memory_boundary_message(messages[idx]):
                return idx
        return -1

    def _is_boundary_message(self, message: BaseMessage) -> bool:
        return (
            isinstance(message, SystemMessage)
            and isinstance(message.content, str)
            and message.content.startswith(self._marker)
        )

    def _is_state_message(self, message: BaseMessage) -> bool:
        return (
            isinstance(message, UserMessage)
            and isinstance(message.content, str)
            and message.content.startswith(self._state_marker)
        )

    def _is_session_memory_boundary_message(self, message: BaseMessage) -> bool:
        return (
            isinstance(message, SystemMessage)
            and isinstance(message.content, str)
            and message.content.startswith(self._session_memory_marker)
        )

    def _is_session_memory_summary_message(self, message: BaseMessage) -> bool:
        return (
            isinstance(message, UserMessage)
            and isinstance(message.content, str)
            and message.content.startswith(self._session_memory_intro)
        )

    def _is_synthetic_marker_message(self, message: BaseMessage) -> bool:
        return isinstance(message, UserMessage) and message.content == self._synthetic_user_marker

    @staticmethod
    def _build_head_tail_truncated_text(text: str, kept_chars: int) -> str:
        if kept_chars <= 0:
            return "...[TRUNCATED]..."
        head_chars = max(int(kept_chars * 0.2), 0)
        tail_chars = max(kept_chars - head_chars, 0)
        head = text[:head_chars]
        tail = text[-tail_chars:] if tail_chars > 0 else ""
        if head and tail:
            return f"{head}\n...[TRUNCATED]...\n{tail}"
        return head or tail or "...[TRUNCATED]..."

    def load_state(self, state: Dict[str, Any]) -> None:
        return

    def save_state(self) -> Dict[str, Any]:
        return {}
