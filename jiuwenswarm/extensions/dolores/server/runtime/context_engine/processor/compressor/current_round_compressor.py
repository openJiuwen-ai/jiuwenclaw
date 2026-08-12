# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import List, Optional, Tuple
from pydantic import BaseModel, Field

from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.exception.codes import StatusCode
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.context_engine import ContextEngine
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.base import ContextProcessor, ContextEvent
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.base import ModelContext
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.compressor.util import (
    build_team_collaboration_reinjected_messages,
    collect_summary_indices,
    count_messages_tokens,
    find_last_completed_api_round_end_idx,
    is_summary_message,
    iter_summary_merge_ranges,
    message_to_text,
)
from openjiuwen.core.foundation.llm import (
    BaseMessage, AssistantMessage, UserMessage,
    ModelRequestConfig, ModelClientConfig, Model
)
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.context.context_utils import ContextUtils


DEFAULT_COMPRESSION_PROMPT: str = """\
You are a **Task Data Preservation Expert**.

Your role is to produce a **high-fidelity incremental memory block** for long-running agent tasks.

Your output will:
1. REPLACE the selected_messages section in the current context
2. BE APPENDED to accumulated memory blocks
3. PRESERVE continuity without rewriting prior memory

---

## CONTEXT STRUCTURE

User Query
↓
Accumulated Memory Blocks  (persistent memory; DO NOT rewrite)
↓
Selected Messages  (THIS is the ONLY content to compress)
↓
Recent Messages  (boundary context; DO NOT absorb unless required for interpretation)

---

[User Intent Context - REFERENCE ONLY]:
{prior_context_and_query}

Rules:
- This section contains: recent raw user requests, recent assistant replies without tool calls, and the current query that triggered this round
- Use ONLY to understand the user's intent and the context leading to selected_messages
- Preserve the user's original requirements, constraints, acceptance criteria, and preferences as completely as possible when they are needed to continue the ongoing work
- Do NOT weaken or over-compress the user's original request unless absolutely necessary
- Treat this as reference context for interpreting selected_messages, not as another compression target

---

[Prior memory blocks - REFERENCE ONLY]:
{accumulated_summaries}

Rules:
- Use ONLY to understand goals, constraints, prior decisions, and continuity
- DO NOT restate, paraphrase, or duplicate their content
- Only reference them when needed to correctly interpret selected_messages

---

[Selected messages - TARGET]:
{selected_messages}

Rules:
- This is the ONLY content you are compressing
- Extract all new progress, changes, unresolved work, and state transitions from this span

---

[Recent uncompressed messages - BOUNDARY CONTEXT]:
{recent_messages}

Rules:
- Use ONLY to resolve ambiguity, references, or incomplete meaning in selected_messages
- DO NOT include their standalone content in your output
- If recent_messages already contain the latest explicit state, do NOT restate them
- Only preserve the minimum handoff information needed to connect selected_messages to recent_messages

---

## CORE PRINCIPLE (CRITICAL)

Treat this output as an **incremental memory block**, NOT a full snapshot.

- Do NOT reconstruct the full global state
- Do NOT repeat previously summarized information
- ONLY capture what is NEW, UPDATED, or STILL OPEN in selected_messages

---

## INFORMATION PRIORITY (CRITICAL)

Preserve information in this order:

1. Task goals and user intent
2. Critical factual basis for continuation
3. Open work / unfinished work
4. Work in progress at the handoff boundary
5. Key decisions, constraints, changes
6. Important files, artifacts, resources, and outputs
7. Supporting details

Never drop higher-priority information to preserve lower-priority details.

---

## FACTUAL BASIS PRESERVATION (CRITICAL)

When preserving progress, always retain the factual basis required to correctly continue the task, including:
- key outputs
- constraints
- evidence
- extracted findings
- comparisons
- conclusions
- decisive intermediate results

When selected_messages contain information that has already been verified, confirmed, validated, or otherwise established with strong support, preserve that verified state explicitly.
Do NOT weaken verified state into vague uncertainty such as "possible", "candidate", or "requires re-evaluation" unless selected_messages contain real counter-evidence or unresolved conflict.

Do NOT preserve action history without the information needed to understand why the action matters.

---

## EVIDENCE PRESERVATION (CRITICAL - DO NOT SUMMARIZE)

For tasks where continuation depends on concrete evidence, verification, or reasoning trace, the following types of evidence MUST be preserved IN FULL or with MINIMAL compression.
This is especially important for debugging, bug-fixing, code modification, investigation, analysis, and other evidence-driven work:

1. **Test/Script Execution Results**:
   - Do NOT compress actual outputs when they contain the factual basis needed later (for example: error messages, stack traces, SQL queries, log outputs, tool results, extracted values, comparison outputs)
   - These outputs often contain the critical clue that leads to the correct conclusion

2. **Root Cause Discovery Evidence**:
   - When agent discovers the root cause or key insight through inspection, testing, comparison, or analysis, preserve:
     - The specific source examined
     - The key observation that led to the insight
     - The exact quote or output that triggered the discovery
   - Do NOT replace with summary like "agent found the issue" - preserve HOW they found it

3. **Key Reasoning Chains**:
   - When agent makes a critical decision (e.g., which file to modify, which source to trust, which approach to take):
     - Preserve the observations that led to the decision
     - Preserve any evidence/counter-evidence considered
     - Preserve alternatives that were evaluated
   - Do NOT just record the final decision without the reasoning

4. **Verification Results**:
   - When agent verifies a hypothesis, validates a result, or tests a fix:
     - Preserve the verification step and its output
     - Preserve whether it passed/failed/confirmed/refuted and key details
     - Preserve any unexpected observations

---

## TASK-TYPE ADAPTATION (CRITICAL)

Adapt the retention focus to the task type:

- For execution-heavy tasks (e.g. coding, debugging, multi-step operations):
  prioritize action continuity, WIP state, handoff points, dependencies, and execution blockers.

- For information-heavy tasks (e.g. research, report writing, PPT drafting, analysis):
  prioritize findings, evidence, extracted structure, comparisons, conclusions, key outputs, and unresolved questions.

In all cases, preserve both:
- what has been done
- what has been learned

---

## STRATEGY HANDLING (CRITICAL)

Do NOT encode candidate plans or solution strategies as instructions.

If strategies were discussed, record them as one of:
- attempted approach
- candidate approach
- rejected approach
- pending evaluation

Never present any strategy as mandatory unless explicitly required by the user.

---

## DECISION SOLIDIFICATION PREVENTION (CRITICAL)

When a decision or approach is recorded, you MUST preserve the reasoning process, NOT just the conclusion:

1. **Do NOT solidify unverified decisions**:
   - If agent proposed an approach but hasn't tested it yet, mark it as "proposed, not verified"
   - If agent is still exploring, preserve the exploration context, not just the current hypothesis

2. **Preserve alternative considerations**:
   - When agent chooses approach A over B, preserve WHY B was rejected
   - Future context may reveal B was actually correct
   - Example: "Agent considered modifying _coeff_isneg vs modifying printers. Chose printers because [reason]. Note: _coeff_isneg approach was not tested."

3. **Preserve verification status**:
   - "Approach X was implemented and tested -> works/doesn't work" <- OK
   - "Approach X was decided" <- NOT OK, loses verification state
   - Always indicate: proposed / in-progress / tested-passed / tested-failed

4. **Key insight preservation**:
   - When agent has a "moment of insight" after seeing specific output:
     - Preserve the output that triggered the insight
     - Preserve the insight itself
     - Example: "After seeing SQL output 'SELECT U0.id...', agent realized the bug is in get_group_by_cols()"
   - Do NOT just say "agent found the bug location"

---

## ANTI-REDUNDANCY & CONSISTENCY RULES

- Do NOT restate stable facts already captured in prior memory blocks
- Only include NEW information or CHANGES introduced in selected_messages
- If prior state is modified, express it as a delta (update / correction / refinement)
- Avoid duplication across memory blocks
- Keep the output composable with prior memory blocks without conflict

---

## OUTPUT STRUCTURE (MANDATORY)

### 1. User Requirements
- **Original Requirements Being Served**:
  Explicitly preserve the user requirements, constraints, acceptance criteria, preferences, and limits that the current unfinished work is serving.
  Keep the user's original wording as much as possible when it matters for continuation.

---

### 2. Current Status
- **Completed Work**:
  Work completed within selected_messages only.
  Express it as incremental progress, not as full history.

- **Key Information Gained**:
  The important information obtained, extracted, compared, or concluded in this span.
  Preserve factual substance, not just procedural actions.

- **Files / Artifacts / Resources**:
  Any files, artifacts, resources, outputs, drafts, tables, pages, documents, code, or results introduced or modified in this span only.

---

### 3. Open Work
- **Work in Progress**:
  MUST include:
  - The active subtask at the end of selected_messages
  - The last concrete action taken in selected_messages
  - Partial results or intermediate state
  - Exact quotes if useful

  IMPORTANT:
  - This section acts as a handoff bridge from selected_messages to recent_messages
  - Do NOT restate recent_messages unless required for interpretation
  - If recent_messages already contain the latest explicit state, record only the handoff point

- **Pending Tasks**:
  Remaining work identified in selected_messages
  - Explicit requests
  - Implicit / derived tasks

- **Priority Order**:
  If multiple open items exist

---

### 4. Important Findings
- **Decisions & Changes**:
  New or updated decisions in this span

- **Constraints / Requirements**:
  Newly introduced or modified requirements, limitations, or preferences

- **Errors & Fixes**:
  Problems encountered in this span and how they were handled

- **Invalid Attempts**:
  Failed or unsuitable approaches and why

---

### 5. Strategy State
- **Attempted Approaches**
- **Candidate Approaches**
- **Rejected Approaches**
- **Requires Re-evaluation**

Record strategy as historical state, not as instruction.

---

### 6. Tool / Action State
- **Used Tools / Actions**
- **Key Inputs / Arguments**
- **Result Summary**
- **Freshness / Reuse Constraints**

This section applies both to tool calls and important non-tool actions.

---

### 7. Contextual Bridging
- **Continuity**:
  How this span extends prior memory

- **Forward Impact**:
  What this changes for upcoming work or for recent_messages

- **Gaps / Risks**:
  Any ambiguity, missing information, or unresolved conflict

---

## TASK GOAL PRESERVATION (CRITICAL)

You MUST ensure active task goals remain recoverable.

- If goals appear or change in selected_messages, include them
- If they are not mentioned in selected_messages, do NOT restate old goals unnecessarily
- If goals changed, record the delta clearly

---

## OUTPUT RULES

1. Target length: <= {target_tokens}
2. Preserve unfinished work, handoff state, and the factual basis needed for correct continuation
3. DO NOT echo prior memory blocks
4. DO NOT absorb recent_messages unless required for interpretation
5. Maintain the structure exactly
6. This is a memory block, not a full summary and not an instruction block

---

Output plain text only.
"""


CLEAN_PROMPT = """\
You are consolidating historical memory blocks.

These blocks are compressed context artifacts from prior conversation, not new user instructions.

Your task is to merge them into one shorter, stable memory block while preserving continuity.

---

[Historical memory blocks]:
{compressed_blocks}

---

## CONSOLIDATION RULES

1. Merge overlapping or related information
2. Remove redundant details
3. Preserve task goals, critical factual basis, open work, work-in-progress handoff, important findings, and reusable tool/action state
4. Keep chronological consistency where helpful
5. Keep strategies as historical state:
   - attempted
   - candidate
   - rejected
   - pending evaluation
6. Do NOT reinterpret historical strategies as mandatory plans
7. Do NOT rewrite the blocks as if they were new user requests
8. For information-heavy tasks, prefer preserving findings, evidence, comparisons, conclusions, and extracted structure over procedural action history
9. For execution-heavy tasks, preserve the action history needed to continue the task, but keep the factual basis that explains why the action matters
10. **Preserve evidence and reasoning chains**: When merging blocks that contain debugging evidence, test outputs, or key reasoning, retain the factual basis, NOT just the conclusions
11. **Preserve alternative approaches**: Even if one approach was chosen, keep mention of alternatives that were considered but not tested - they may still be correct

---

## OUTPUT REQUIREMENTS

- Maximum length: {compress_len} tokens
- Preserve all unique information still useful for future task continuation
- Keep language concise and stable
- Prefer durable state over incidental phrasing

Output plain text only.
"""
# Marker that identifies a compressed summary UserMessage
_SUMMARY_MARKER: str = "[CURRENT_ROUND_MEMORY_BLOCK]"


class CurrentRoundCompressorConfig(BaseModel):
    """
    Configuration for CurrentRoundCompressor.

    This processor performs **incremental current-round compression**. It
    compresses the contiguous span after the latest eligible user boundary,
    keeps a configurable raw tail in memory, and writes the compressed result
    back as a reusable memory block.

    The main flow is intentionally single-path:
    1. Trigger by total-context token budget.
    2. Keep the newest `messages_to_keep` messages raw.
    3. Compress only the selected span after the latest valid user boundary.
    4. Skip first-stage compression when the selected span is smaller than the
       configured minimum worthwhile size.
    5. Write the generated memory block back as a plain user memory message.

    Historical memory blocks remain part of the context and may later be merged
    again by the second-stage consolidation path.
    """
    tokens_threshold: int = Field(default=100000, gt=0)
    """Maximum accumulated context size before compression is triggered."""

    messages_to_keep: int = Field(default=3, gt=0)
    """Guaranteed number of most-recent messages to retain, regardless of any other threshold."""

    model: ModelRequestConfig | None = Field(default=None)
    """Model request configuration used during compression and summary merge."""

    model_client: ModelClientConfig | None = Field(default=None)
    """Client configuration for the LLM used to generate compressed memory blocks."""


    # ------------------------------------------------------------------
    # Advanced compression settings
    # ------------------------------------------------------------
    min_selected_tokens_for_compression: int = Field(default=20000, gt=0)
    """Minimum token size required for the selected compression span before first-stage compression runs."""

    compression_target_tokens: int = Field(default=4000, gt=0)
    """Target token budget communicated to the first-stage compression prompt."""

    summary_merge_target_tokens: int = Field(default=4000, gt=0)
    """Target token budget communicated to the historical summary merge prompt."""

    accumulated_summary_token_limit: int = Field(default=20000, gt=0)
    """Total token threshold for triggering second-stage consolidation of memory blocks."""

    summary_merge_min_blocks: int = Field(default=3, ge=2)
    """Minimum number of accumulated memory blocks required before merge consolidation is attempted."""

    prior_context_window_size: int = Field(default=10, gt=0)
    """Maximum number of recent user/assistant intent messages included in the reference-only user intent context."""

    # Prompt customization.
    custom_compression_prompt: str | None = Field(
        default=None,
        description="Custom first-stage compression prompt template. Defaults to the built-in prompt when omitted.",
    )
    """User-editable prompt template for current-round compression."""


@ContextEngine.register_processor()
class CurrentRoundCompressor(ContextProcessor):
    """
    Compress the current round into protocolized memory blocks.

    The intended logical layout of the active context is:
    `compressed_history + selected_messages + recent_messages`

    where:
    - `compressed_history` contains prior compressed memory blocks,
    - `selected_messages` is the only span to be replaced in this round,
    - `recent_messages` stays raw for short-term continuity.

    **This implementation was updated** to keep compressed output in a
    protocolized current-round memory block format so later models treat it as
    historical memory instead of a fresh user command.

    The current prompt explicitly balances:
    - action continuity for execution-heavy tasks
    - factual-information retention for information-heavy tasks
    """
    def __init__(self, config: CurrentRoundCompressorConfig):
        """Initialize prompts, thresholds, LLM client, and runtime statistics.

        Frequently used config values are cached on `self` because they are read
        repeatedly by the trigger path, first-stage compression, and second-stage
        summary merge logic.
        """
        super().__init__(config)
        self._compressed_prompt = config.custom_compression_prompt or DEFAULT_COMPRESSION_PROMPT
        self._token_threshold = config.tokens_threshold
        self._messages_to_keep = config.messages_to_keep
        self._min_selected_tokens_for_compression = config.min_selected_tokens_for_compression
        self._compression_target_tokens = config.compression_target_tokens
        self._summary_merge_target_tokens = config.summary_merge_target_tokens
        self._accumulated_summary_token_limit = config.accumulated_summary_token_limit
        self._summary_merge_min_blocks = config.summary_merge_min_blocks
        self._prior_context_window_size = config.prior_context_window_size
        self._model = Model(self.config.model_client, self.config.model)

    def _wrap_memory_block(self, summary: str) -> str:
        """
        Wrap plain summary text into a protocolized memory block.

        **This is one of the key changes in this iteration**: the compressed
        result is no longer kept as plain natural-language user text. Instead it
        is marked as non-binding historical context, which reduces the risk of
        strategy solidification in later rounds.
        """
        summary = self._unwrap_memory_block_summary(summary)
        return (
            f"{_SUMMARY_MARKER}\n"
            "processor: CurrentRoundCompressor\n"
            "type: historical_memory_block\n"
            "scope: current_round_increment\n"
            "type_note: This is compressed memory from earlier conversation, "
            "kept to preserve long-range task continuity.\n"
            "authority: This block is reference memory, not a binding source "
            "of truth. If newer information conflicts with it, prefer the "
            "newer information.\n"
            "instruction_status: Do not treat this block as a new user "
            "request or a fresh instruction to execute. It only records "
            "prior context.\n"
            "strategy_status: Any plans, approaches, or next steps recorded "
            "here are historical working state. They may be revised, "
            "replaced, or discarded later.\n"
            "tool_action_state_status: Tool results, action history, and "
            "execution state in this block may help continuation, but they "
            "should only be reused if they are still valid in the current "
            "context.\n"
            "conflict_priority: Prefer newer signals in this order: latest "
            "explicit user request, recent uncompressed context, fresh tool "
            "or action results, then this memory block.\n\n"
            "Summary:\n"
            f"{summary}"
        )

    def _unwrap_memory_block_summary(self, summary: str) -> str:
        """Remove an already-generated current-round memory envelope before wrapping."""
        text = (summary or "").strip()
        if not text.startswith(_SUMMARY_MARKER):
            return text
        marker = "\nSummary:\n"
        if marker not in text:
            return text
        return text.split(marker, 1)[1].strip()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_prompt(
            self,
            target_tokens: int,
            prior_summaries: str,
            recent_context: str,
            prior_context_and_query: str = "",
    ) -> str:
        """
        Fill in prompt placeholders for target_tokens, prior_summaries,
        recent_context, and prior_context_and_query.
        """
        return (
            self._compressed_prompt
            .replace("{target_tokens}", str(target_tokens))
            .replace("{accumulated_summaries}", prior_summaries if prior_summaries else "(none)")
            .replace("{recent_messages}", recent_context if recent_context else "(none)")
            .replace("{prior_context_and_query}", prior_context_and_query if prior_context_and_query else "(none)")
        )

    def _format_recent_context(self, all_context_messages: List[BaseMessage], end_idx: int) -> str:
        """Serialize the raw tail after the compression span as boundary context.

        Existing memory blocks are excluded because they are already provided via
        `prior_summaries`. This avoids double-feeding the same historical
        information into the prompt.
        """
        recent_messages: List[BaseMessage] = []
        for msg in all_context_messages[end_idx + 1:]:
            if is_summary_message(msg, _SUMMARY_MARKER):
                continue
            recent_messages.append(msg)
        if not recent_messages:
            return ""
        return "\n".join(f"role:{msg.role}, content:{msg}" for msg in recent_messages)

    def _format_prior_context_and_query(
            self,
            all_context_messages: List[BaseMessage],
            current_query_idx: int,
    ) -> str:
        """Format a filtered user-intent context window plus the current query message."""
        lines = []
        prior_messages = []
        if current_query_idx > 0:
            for msg in all_context_messages[:current_query_idx]:
                is_plain_user = isinstance(msg, UserMessage) and not is_summary_message(msg, _SUMMARY_MARKER)
                is_plain_assistant = isinstance(msg, AssistantMessage) and not msg.tool_calls
                if is_plain_user or is_plain_assistant:
                    prior_messages.append(msg)
            prior_messages = prior_messages[-self._prior_context_window_size:]

        for msg in prior_messages:
            lines.append(f"role:{msg.role}, content:{msg}")

        if 0 <= current_query_idx < len(all_context_messages):
            query_msg = all_context_messages[current_query_idx]
            lines.append(f"\n--- Current User Intent ---\nrole:{query_msg.role}, content:{query_msg}")

        return "\n".join(lines) if lines else ""

    # ------------------------------------------------------------------
    # ContextProcessor interface
    # ------------------------------------------------------------------

    async def on_add_messages(
            self,
            context: ModelContext,
            messages_to_add: List[BaseMessage],
            **kwargs
    ) -> Tuple[ContextEvent | None, List[BaseMessage]]:
        """
        Compress eligible content before new messages are committed to context.

        The processor first finds the latest compressible user boundary, then
        runs span-level compression on the selected range after that boundary.
        Successful compression replaces the original raw messages in place with
        a plain current-round memory block.

        In the successful replacement path, the context is updated in place and
        the returned message list is empty because the new block has already been
        written back to `context`.
        """
        context_messages = context.get_messages() + messages_to_add
        self._reset_compression_usage()
        last_user_idx = await self.get_compress_idx(context_messages)
        if last_user_idx == -1:
            return None, messages_to_add
        keep_start_idx = max(0, len(context_messages) - self._messages_to_keep)
        end_idx = keep_start_idx - 1

        try:
            compressed_context, modified_indices, compact_summary = await self.multi_compress(
                context_messages, last_user_idx, end_idx, context
            )
            if compressed_context:
                event = ContextEvent(
                    event_type=self.processor_type(),
                    compact_summary=compact_summary,
                    compression_usage=self._current_compression_usage(),
                )
                event.messages_to_modify += modified_indices
                context.set_messages(compressed_context)
                return event, []
            return None, messages_to_add
        except Exception as e:
            raise build_error(
                StatusCode.CONTEXT_EXECUTION_ERROR,
                error_msg="compress messages failed",
                cause=e
            ) from e

    async def trigger_add_messages(
            self,
            context: ModelContext,
            messages_to_add: List[BaseMessage],
            **kwargs
    ) -> bool:
        """
        Decide whether compression should run for the incoming batch.

        Compression is triggered only when the combined context size exceeds
        `tokens_threshold`. If the runtime does not provide a token counter, the
        processor falls back to a character-based approximation.

        `messages_to_keep` still acts as a hard guard for the preserved tail.
        This method only decides whether compression work should start; the exact
        replacement range is determined later by the preserved tail and the last
        complete API round.
        """
        config = self.config
        message_size = len(context) + len(messages_to_add)
        if message_size < self._messages_to_keep:
            return False
        token_counter = context.token_counter()
        tokens = count_messages_tokens(
            context.get_messages() + messages_to_add,
            token_counter,
            self.processor_type(),
        )
        if tokens > self._token_threshold:
            logger.info(
                f"[{self.processor_type()} triggered] context tokens {tokens} "
                f"exceeds threshold of {config.tokens_threshold}"
            )
            return True
        return False

    async def get_compress_idx(self, messages: List[BaseMessage]) -> int:
        """
        Locate the latest eligible user boundary for compression.

        Only the span after the latest user message is considered compressible.
        If the latest message is itself a user message, or the boundary falls
        inside the preserved tail, this method returns `-1`.

        This keeps the processor focused on the current round instead of
        repeatedly rewriting older raw conversation segments.
        """
        compressed_idx = -1
        for i in range(len(messages) - 1, -1, -1):
            if isinstance(messages[i], UserMessage):
                compressed_idx = i
                break
        if compressed_idx == len(messages) - 1:
            return -1
        if compressed_idx < 0:
            return -1
        keep_index = len(messages) - self._messages_to_keep
        if compressed_idx >= keep_index:
            return -1
        return compressed_idx

    async def multi_compress(
            self,
            context_messages: List[BaseMessage],
            last_user_idx: int,
            end_idx: int,
            context: ModelContext,
    ) -> Tuple[Optional[list[BaseMessage]], List[int], str]:
        """
        Compress the whole eligible span into one memory block.

        This mode is suitable when the selected messages form one contiguous
        semantic unit and should be summarized together.

        **This method now also supports** second-stage merge compression for
        old memory blocks once enough compressed history has accumulated.
        """
        updated = False
        modified_indices: List[int] = []
        compact_summary_parts: List[str] = []
        start_idx = last_user_idx + 1
        actual_end_idx = end_idx
        if actual_end_idx >= start_idx:
            actual_end_idx = find_last_completed_api_round_end_idx(
                context_messages,
                start_idx,
                actual_end_idx,
            )
        if actual_end_idx >= start_idx:
            messages_to_compress = context_messages[start_idx:actual_end_idx + 1]
            compressed_msg = await self.compress(
                messages_to_compress, context, context_messages, actual_end_idx, current_query_idx=last_user_idx
            )
            if compressed_msg:
                compact_summary_parts.append(message_to_text(compressed_msg))
                replacement_messages = [compressed_msg]
                replacement_messages.extend(build_team_collaboration_reinjected_messages(messages_to_compress))
                context_messages = ContextUtils.replace_messages(
                    context_messages, replacement_messages, start_idx, actual_end_idx
                )
                modified_indices.extend(range(start_idx, actual_end_idx + 1))
                updated = True
        for start_idx_, end_idx_ in iter_summary_merge_ranges(
                context_messages,
                _SUMMARY_MARKER,
                self._summary_merge_min_blocks,
        ):
            old_compress_messages = context_messages[start_idx_:end_idx_ + 1]
            compressed_msg = await self._merge_summary_blocks(context, old_compress_messages)
            if compressed_msg:
                compact_summary_parts.append(message_to_text(compressed_msg))
                context_messages = ContextUtils.replace_messages(
                    context_messages, [compressed_msg], start_idx_, end_idx_
                )
                modified_indices.extend(range(start_idx_, end_idx_ + 1))
                updated = True
                break
        return (
            context_messages if updated else None,
            modified_indices,
            "\n\n".join(part for part in compact_summary_parts if part),
        )

    async def compress(
            self,
            messages_to_compress: List[BaseMessage],
            context: ModelContext,
            all_context_messages: Optional[List[BaseMessage]] = None,
            compress_end_idx: Optional[int] = None,
            current_query_idx: Optional[int] = None,
    ) -> Optional[BaseMessage]:
        """
        Compress one selected span into a single memory block.

        Inputs are organized in the recommended reference/target split:
        - prior memory blocks: reference only,
        - prior context and current query: reference for understanding user intent,
        - selected messages: the only real compression target,
        - recent raw messages: boundary context only.

        The prompt intentionally preserves two axes at once:
        - execution continuity for process-heavy tasks
        - factual findings for information-heavy tasks

        **This method was updated** so recent messages act only as handoff
        reference; they must not be absorbed into the compressed block unless
        they are strictly necessary for interpretation.

        Compression is accepted only when:
        - the selected span reaches the minimum worthwhile size, and
        - the generated summary is smaller than the original span.
        """
        token_counter = context.token_counter()
        input_tokens = count_messages_tokens(messages_to_compress, token_counter, self.processor_type())
        if input_tokens < self._min_selected_tokens_for_compression:
            logger.info(
                f"[{self.processor_type()}] Skipping: selected span tokens ({input_tokens}) "
                f"< min_selected_tokens_for_compression ({self._min_selected_tokens_for_compression})"
            )
            return None

        prior_summaries = ""
        recent_context = ""
        prior_context_and_query = ""
        if all_context_messages is not None:
            summary_indices = collect_summary_indices(all_context_messages, _SUMMARY_MARKER)
            if summary_indices:
                prior_summaries = "\n---\n".join(
                    all_context_messages[i].content for i in summary_indices
                )
            if compress_end_idx is not None:
                recent_context = self._format_recent_context(all_context_messages, compress_end_idx)
            if current_query_idx is not None and current_query_idx >= 0:
                prior_context_and_query = self._format_prior_context_and_query(
                    all_context_messages,
                    current_query_idx,
                )

        filled_prompt = self._build_prompt(
            self._compression_target_tokens,
            prior_summaries,
            recent_context,
            prior_context_and_query,
        )

        processed_messages = "\n".join([f"role:{msg.role}, content:{msg}"
            for msg in messages_to_compress])
        filled_prompt = filled_prompt.replace("{selected_messages}", str(processed_messages))

        try:
            response = await self._model.invoke([UserMessage(content=filled_prompt)])
            self._record_compression_usage(response)
        except Exception as exc:
            logger.warning(
                f"[{self.processor_type()}] compression model invoke failed during current-round compression, "
                f"skip current processor and continue remaining processors: {exc}"
            )
            return None

        summary = response.content or ""
        if summary:
            compressed_tokens = count_messages_tokens(
                [UserMessage(content=summary)],
                token_counter,
                self.processor_type(),
            )
            if compressed_tokens >= input_tokens:
                logger.info(
                    f"[{self.processor_type()}] Skipping: compressed tokens ({compressed_tokens}) "
                    f">= original ({input_tokens}), no benefit."
                )
                return None

        return UserMessage(content=self._wrap_memory_block(summary))

    async def _merge_summary_blocks(
            self,
            context: ModelContext,
            old_compress_messages: Optional[List[BaseMessage]] = None,
    ) -> Optional[BaseMessage]:
        """
        Merge multiple historical memory blocks into one shorter memory block.

        **This is another major change in this iteration**: old compressed
        blocks are converted into plain text reference material before being sent
        to the LLM, instead of being passed through as a user-turn message
        sequence. This reduces role bias during second-stage consolidation.

        This path is guarded by both `summary_merge_min_blocks` and
        `accumulated_summary_token_limit` so historical merging only happens when
        the consolidation call is likely to be worthwhile. The merged result uses
        the same configurable writeback strategy as first-stage compression.
        """
        token_counter = context.token_counter()

        total_tokens = count_messages_tokens(old_compress_messages or [], token_counter, self.processor_type())
        if total_tokens <= self._accumulated_summary_token_limit:
            return None
        merged_blocks = "\n\n".join(
            f"[MEMORY_BLOCK_{i}]\n{msg.content}"
            for i, msg in enumerate(old_compress_messages or [], 1)
        )
        filled_prompt = (
            CLEAN_PROMPT
            .replace("{compress_len}", str(self._summary_merge_target_tokens))
            .replace("{compressed_blocks}", merged_blocks if merged_blocks else "(none)")
        )
        model_messages = [UserMessage(content=filled_prompt)]

        try:
            response = await self._model.invoke(model_messages)
            self._record_compression_usage(response)
        except Exception as exc:
            logger.warning(
                f"[{self.processor_type()}] compression model invoke failed during summary merge, "
                f"skip summary merge and continue remaining processors: {exc}"
            )
            return None
        summary_text = response.content or ""
        if summary_text:
            compressed_msg = UserMessage(content=self._wrap_memory_block(summary_text))
            logger.info(
                f"[{self.processor_type()}] compressed "
                f"{len(old_compress_messages or [])} old compressed messages into one"
            )
        else:
            logger.info(
                f"[{self.processor_type()}] failed to compress "
                f"{len(old_compress_messages or [])} old compressed messages"
            )
            return None
        return compressed_msg
