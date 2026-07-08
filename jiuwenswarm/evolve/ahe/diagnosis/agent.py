# Copyright (c) Huawei Technologies, Co., Ltd. 2026. All rights reserved.
"""DiagnosisAgent — lightweight ReAct loop for trace diagnosis.

Pluggable design: no dependency on DeepAgent, NexAU, or existing
ProposalGenerators. Uses openjiuwen Model for LLM calls (same pattern as
LLMProposer, but independent instance — no shared state).

Two modes:
- diagnose: output DiagnosisResult with issues
- propose: output Proposal list (registered as ProposalGenerator)
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from jiuwenswarm.evolve.ahe.diagnosis.models import (
    DiagnosisIssue,
    DiagnosisResult,
)
from jiuwenswarm.evolve.ahe.diagnosis.prompts import (
    DIAGNOSIS_SYSTEM_PROMPT,
    DIAGNOSIS_TOOL_SCHEMAS,
)
from jiuwenswarm.evolve.ahe.diagnosis.tools import DiagnosisToolExecutor

if TYPE_CHECKING:
    from jiuwenswarm.evolve.models import TraceBatch

logger = logging.getLogger(__name__)


class DiagnosisAgent:
    """Lightweight ReAct Agent for trace diagnosis.

    Operates on NormalizedTrace data (structured messages), not raw OTEL spans.
    Uses its own simple loop with 7 read-only tools.
    Context management: tool output truncation + context compression.
    """

    def __init__(
        self,
        store: Any | None = None,
        model: Any | None = None,
        max_iterations: int = 20,
        temperature: float = 0.4,
        workspace_dir: str | None = None,
    ) -> None:
        self._store = store
        self._model = model
        self._max_iterations = max_iterations
        self._temperature = temperature
        self._workspace_dir = workspace_dir
        self._tool_executor = None  # lazy init

    async def run(
        self,
        trace_ids: list[str] | None = None,
        normalized_traces: list[dict] | None = None,
        mode: str = "diagnose",
        question: str | None = None,
    ) -> DiagnosisResult:
        """Execute ReAct loop over NormalizedTrace data.

        Two entry points:
        - From AheProposer: pass normalized_traces directly.
        - Standalone (CLI): pass trace_ids (CLEAN step done here).
        """
        if mode not in ("diagnose", "propose"):
            raise ValueError(f"mode must be 'diagnose' or 'propose', got '{mode}'")

        # Resolve NormalizedTrace data
        traces = normalized_traces
        if traces is None and trace_ids:
            traces = await self._clean_traces(trace_ids)

        if not traces:
            logger.warning("No NormalizedTrace data available")
            return DiagnosisResult(mode=mode, issues=[], response="No trace data", iterations=0)

        # Initialize tool executor
        self._tool_executor = DiagnosisToolExecutor(
            normalized_traces=traces,
            store=self._store,
            workspace_dir=self._workspace_dir,
        )

        # Build initial messages
        trace_ids_to_show = [nt.get("id") or nt.get("trace_id", "unknown") for nt in traces]
        messages = self._build_messages(trace_ids_to_show, mode, question)

        logger.info(
            "DiagnosisAgent: starting ReAct loop (traces=%d, mode=%s)",
            len(traces), mode
        )
        # ReAct loop
        empty_response_count = 0
        turn = 0
        for iteration in range(self._max_iterations):
            turn += 1

            # 简洁的 turn 信息
            logger.info("=== Turn %d/%d ===", turn, self._max_iterations)

            # 1. Call LLM with tools
            content, tool_calls = await self._call_llm(messages)

            # 检测连续空响应（可能是没有 model）
            if not content and not tool_calls:
                empty_response_count += 1
                logger.warning(
                    "Turn %d: Empty response (no content, no tool_calls)",
                    turn
                )
                if empty_response_count >= 3:
                    logger.error(
                        "DiagnosisAgent: %d consecutive empty responses, breaking loop",
                        empty_response_count
                    )
                    return DiagnosisResult(
                        mode=mode,
                        issues=[],
                        response="DiagnosisAgent: no LLM model available",
                        iterations=turn,
                        budget_exceeded=True,
                    )
                continue

            if not tool_calls:
                # Pure text response → append and continue
                logger.info("Turn %d: Text response only (%d chars)", turn, len(content))
                messages.append({"role": "assistant", "content": content})
                empty_response_count = 0
                continue

            # Tool calls detected
            logger.info(
                "Turn %d: Tool calls: %s",
                turn,
                [tc.get("name") for tc in tool_calls]
            )

            # 2. Append assistant message with tool_calls
            assistant_tool_calls = []
            for tc in tool_calls:
                tc_id = tc.get("id") or f"call_{tc['name']}_{turn}"
                tc["id"] = tc_id

                assistant_tool_calls.append({
                    "id": tc_id,
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc["arguments"], ensure_ascii=False)
                    }
                })

            messages.append({
                "role": "assistant",
                "content": content,
                "tool_calls": assistant_tool_calls
            })

            # 3. Execute tools and append results
            for tc in tool_calls:
                if tc["name"] == "submit_result":
                    logger.info("Turn %d: submit_result detected, finalizing", turn)
                    return self._finalize(tc["arguments"].get("result", ""), turn, mode)

                logger.debug("Executing tool: %s", tc["name"])  # DEBUG级别，不显示在stdout
                result = self._execute_tool(tc["name"], tc["arguments"])

                # Append tool result
                tool_content = json.dumps(result, ensure_ascii=False, default=str)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": tool_content,
                })

            # 4. Check context size
            messages = self._compact_context_if_needed(messages)
        # Budget exceeded
        last_text = ""
        for msg in reversed(messages):
            if msg.get("role") == "assistant" and msg.get("content"):
                last_text = msg["content"]
                break

        return DiagnosisResult(
            mode=mode,
            issues=[],
            response=f"[budget-exceeded] {last_text}".strip(),
            iterations=self._max_iterations,
            budget_exceeded=True,
        )

    # ── ProposalGenerator interface ───────────────────────────────────

    async def generate(self, batch: TraceBatch) -> list:
        """ProposalGenerator interface — propose mode output."""
        from jiuwenswarm.evolve.models import (
            Proposal, ProposalTargetType, ProposalState, EvidenceRef,
        )

        result = await self.run(
            trace_ids=batch.trace_ids,
            mode="propose",
        )
        if result.proposals is None:
            # Parse from raw response JSON
            return []

        # Convert raw dicts to Proposal objects
        proposals = []
        for raw in result.proposals:
            try:
                prop = Proposal(
                    target_id=raw.get("target_id"),
                    target_type=ProposalTargetType(raw.get("target_type", "skill")),
                    proposal_type=raw.get("proposal_type", "add_skill_experience"),
                    failure_evidence=[
                        EvidenceRef(**e) for e in raw.get("failure_evidence", [])
                    ],
                    root_cause=raw.get("root_cause", ""),
                    targeted_fix=raw.get("targeted_fix", {}),
                    predicted_impact=raw.get("predicted_impact", ""),
                    risk=raw.get("risk"),
                    proposer_name="diagnosis_agent",
                    state=ProposalState.CANDIDATE,
                )
                proposals.append(prop)
            except Exception as exc:
                logger.warning("DiagnosisAgent: failed to parse proposal: %s", exc)

        return proposals

    # ── Internal methods ──────────────────────────────────────────────

    def _build_messages(
        self, trace_ids: list[str], mode: str, question: str | None
    ) -> list[dict]:
        """Construct initial messages for the ReAct loop."""
        # System prompt (tools are now defined via OpenAI function schemas)
        system_msg = DIAGNOSIS_SYSTEM_PROMPT

        # User message
        trace_list = "\n".join(f"- {tid}" for tid in trace_ids)
        if mode == "diagnose":
            user_content = (
                f"分析以下 trace 数据：\n{trace_list}\n\n"
                f"问题：{question or '这些 trace 中发现了什么问题？'}"
            )
        else:
            user_content = (
                f"分析以下 trace 数据并生成 Skill Experience Proposal：\n{trace_list}\n\n"
                "请找出导致失败的问题，生成 propose 模式的 JSON 输出。"
            )

        return [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_content},
        ]

    async def _clean_traces(self, trace_ids: list[str]) -> list[dict]:
        """CLEAN step: OTEL spans → NormalizedTrace dicts.

        For standalone use (CLI) where NormalizedTrace not pre-computed.
        """
        try:
            from jiuwenswarm.evolve.ahe.otel_adapter import OtelTraceAdapter

            adapter = OtelTraceAdapter(db_path=self._store._traces_db_path if self._store else "traces.db")
            normalized = []
            for tid in trace_ids:
                trace_dict = adapter.convert_trace(tid)
                if trace_dict.get("observations"):
                    trace_dict["trace_id"] = tid
                    normalized.append(trace_dict)
            return normalized
        except Exception as exc:
            logger.warning("DiagnosisAgent._clean_traces failed: %s", exc)
            return []

    async def _call_llm(self, messages: list[dict]) -> tuple[str, list[dict]]:
        """Call LLM with tools parameter, return content and tool_calls.

        Returns:
            tuple of (content, tool_calls):
            - content: Text content from LLM response
            - tool_calls: List of tool call dicts with 'name' and 'arguments'
        """
        if self._model is None:
            # Lazy init from config
            self._model = await self._init_model()

        if self._model is None:
            logger.warning("DiagnosisAgent: no model available")
            return "", []

        try:
            # Directly pass dict messages to model (no need for SimpleMessage conversion)
            # openai_wrapper.py already handles both dict and object messages correctly
            # logger.info("call llm with message %s", str(messages)[:100])
            response = await self._model.invoke(messages=messages, tools=DIAGNOSIS_TOOL_SCHEMAS)

            # Defensive check: verify response has choices
            if not response.choices:
                logger.warning(
                    "DiagnosisAgent: API response has no choices. Model: %s",
                    self._model._model if hasattr(self._model, '_model') else 'unknown',
                )
                return "", []  # Return empty content and tool_calls

            # Extract content and tool_calls
            message = response.choices[0].message
            content = message.content or ""

            tool_calls = []
            if message.tool_calls:
                for tc in message.tool_calls:
                    try:
                        # Parse arguments JSON
                        arguments = json.loads(tc.function.arguments)
                        tool_calls.append({
                            "id": tc.id,
                            "name": tc.function.name,
                            "arguments": arguments,
                        })
                    except json.JSONDecodeError as exc:
                        logger.warning(
                            "DiagnosisAgent: failed to parse tool arguments for %s: %s",
                            tc.function.name, exc
                        )
                        # Skip this tool call on parse error
                        continue

            return content, tool_calls
        except Exception as exc:
            logger.warning("DiagnosisAgent._call_llm failed: %s", exc)
            return "", []

    async def _init_model(self):
        """Initialize OpenAI client from evolve config (not openjiuwen).

        Reads configuration from evolve/config.yaml llm section:
        - api_key: supports ${ENV_VAR} expansion
        - api_base: OpenAI-compatible API endpoint
        - model_name: model to use (e.g., gpt-4)
        - temperature: generation temperature
        - max_tokens: max tokens in response
        """
        try:
            from openai import AsyncOpenAI
            from jiuwenswarm.evolve import get_evolve_config
            from jiuwenswarm.evolve.ahe.openai_wrapper import OpenAIModelWrapper
            import os

            evolve_cfg = get_evolve_config()
            llm_cfg = evolve_cfg.get("llm", {})

            # Expand environment variables in api_key
            api_key = llm_cfg.get("api_key")
            # Check if api_key is valid string before calling startswith
            if api_key and isinstance(api_key, str):
                if api_key.startswith("${") and api_key.endswith("}"):
                    env_var = api_key[2:-1]
                    api_key = os.getenv(env_var)
            else:
                # api_key is None or not a string
                api_key = os.getenv("EVOLVE_API_KEY")  # Fallback: try direct env var

            if not api_key:
                logger.warning("DiagnosisAgent: no API key configured in evolve/config.yaml or environment")
                return None

            # Get api_base (with proper fallback for DeepSeek)
            api_base = llm_cfg.get("api_base")
            # Check if api_base is a valid non-empty string
            if not api_base or not isinstance(api_base, str) or api_base.strip() == "":
                # Use DeepSeek default endpoint if not configured
                api_base = "https://api.deepseek.com/v1"
                logger.info("DiagnosisAgent: api_base not configured, using DeepSeek default: %s", api_base)
            else:
                logger.info("DiagnosisAgent: using configured api_base: %s", api_base)

            # Create AsyncOpenAI client with timeout to prevent hanging
            client = AsyncOpenAI(
                api_key=api_key,
                base_url=api_base,
                timeout=60.0,  # Add 60 second timeout
            )

            # Return wrapper that matches expected interface
            return OpenAIModelWrapper(
                client=client,
                model=llm_cfg.get("model_name", "deepseek-v4-pro"),
                temperature=self._temperature,
                max_tokens=llm_cfg.get("max_tokens", 20000),
            )
        except Exception as exc:
            logger.warning("DiagnosisAgent._init_model failed: %s", exc)
            return None

    def _execute_tool(self, tool_name: str, arguments: dict) -> dict:
        """Execute a tool via DiagnosisToolExecutor."""
        if self._tool_executor is None:
            return {"error": "No tool executor configured (no NormalizedTrace data)"}
        return self._tool_executor.execute(tool_name, arguments)

    def _finalize(
        self, result_json: str, iterations: int, mode: str
    ) -> DiagnosisResult:
        """Parse submit_result payload into DiagnosisResult."""
        payload = None
        try:
            parsed = json.loads(result_json)
            # Handle double-encoded JSON: if parsed is string, parse again
            if isinstance(parsed, str):
                logger.warning("DiagnosisAgent: result is double-encoded JSON string, parsing again")
                payload = json.loads(parsed)
            elif isinstance(parsed, dict):
                payload = parsed
            else:
                logger.warning("DiagnosisAgent: result is not dict or string, type=%s", type(parsed))
                return DiagnosisResult(
                    mode=mode,
                    issues=[],
                    response=result_json,
                    iterations=iterations,
                    budget_exceeded=False,
                )
        except json.JSONDecodeError as e:
            # Try extracting JSON from text
            logger.warning("DiagnosisAgent: JSON decode failed: %s, trying regex extraction", e)
            m = re.search(r"\{.*\}", result_json, re.DOTALL)
            if m:
                try:
                    extracted = m.group(0)
                    parsed = json.loads(extracted)
                    # Handle double-encoded JSON after extraction
                    if isinstance(parsed, str):
                        payload = json.loads(parsed)
                    elif isinstance(parsed, dict):
                        payload = parsed
                    else:
                        return DiagnosisResult(
                            mode=mode,
                            issues=[],
                            response=result_json,
                            iterations=iterations,
                            budget_exceeded=False,
                        )
                except json.JSONDecodeError:
                    return DiagnosisResult(
                        mode=mode,
                        issues=[],
                        response=result_json,
                        iterations=iterations,
                        budget_exceeded=False,
                    )
            else:
                return DiagnosisResult(
                    mode=mode,
                    issues=[],
                    response=result_json,
                    iterations=iterations,
                    budget_exceeded=False,
                )

        # Validate payload is dict
        if not isinstance(payload, dict):
            logger.warning("DiagnosisAgent: payload is not dict after all parsing attempts, type=%s", type(payload))
            return DiagnosisResult(
                mode=mode,
                issues=[],
                response=result_json,
                iterations=iterations,
                budget_exceeded=False,
            )

        # Validate and convert
        issues = []
        for raw_issue in payload.get("issues", []):
            # Type safety check: ensure raw_issue is dict
            if not isinstance(raw_issue, dict):
                logger.warning("DiagnosisAgent: issue is not dict, skipping: %s", raw_issue)
                continue

            try:
                issues.append(DiagnosisIssue(
                    issue_type=raw_issue.get("issue_type", ""),
                    summary=raw_issue.get("summary", ""),
                    evidence=raw_issue.get("evidence", ""),
                    trace_id=raw_issue.get("trace_id", ""),
                    span_index=raw_issue.get("span_index", 0),
                    root_cause=raw_issue.get("root_cause"),
                    suggested_fix=raw_issue.get("suggested_fix"),
                ))
            except (ValueError, AttributeError) as exc:
                logger.warning("DiagnosisAgent: invalid issue: %s", exc)

        proposals = payload.get("proposals")  # Raw dicts for propose mode

        return DiagnosisResult(
            mode=mode,
            issues=issues,
            response=payload.get("response", ""),
            iterations=iterations,
            budget_exceeded=False,
            proposals=proposals,
        )

    def _compact_context_if_needed(self, messages: list[dict]) -> list[dict]:
        """Compress context when approaching token limits.

        Strategy:
        - Estimate tokens via char/4 heuristic
        - When reaching 75% of max_context_tokens (200k), compress older iterations
        - Keep system prompt + last 3 iterations intact
        - Older iterations → summarized via LLM (one extra call)
        - If no LLM compression possible, just keep recent and drop older
        """
        max_context_tokens = 200000
        threshold = 0.75

        # Estimate current token count
        total_chars = sum(len(msg.get("content", "")) for msg in messages)
        estimated_tokens = total_chars / 4  # rough: 4 chars per token

        if estimated_tokens < max_context_tokens * threshold:
            return messages  # No compression needed

        logger.info(
            "DiagnosisAgent: context approaching limit (%d estimated tokens), compressing",
            int(estimated_tokens),
        )

        # Strategy: keep system prompt + last 3 iterations, drop older tool_results
        # Simple approach: keep first message (system) and last 6 messages (3 iterations ≈ 6 messages)
        if len(messages) <= 7:
            return messages  # Not enough to compress meaningfully

        system = messages[0]
        recent = messages[-6:]  # Keep last ~3 iterations

        # Summarize dropped messages
        dropped = messages[1:-6]
        summary_parts = []
        for msg in dropped:
            role = msg.get("role", "")
            content = msg.get("content", "")
            # Truncate each dropped message to key info
            if role == "tool_result":
                # Extract just tool_name and a brief summary
                try:
                    data = json.loads(content)
                    name = data.get("tool_name", "unknown")
                    result_str = str(data.get("result", ""))[:200]
                    summary_parts.append(f"[{role}:{name}] {result_str}")
                except (json.JSONDecodeError, TypeError):
                    summary_parts.append(f"[{role}] {content[:200]}")
            else:
                summary_parts.append(f"[{role}] {content[:300]}")

        summary = "以下是早期迭代的摘要：\n" + "\n".join(summary_parts)

        compressed = [system, {"role": "system", "content": summary}] + recent
        return compressed
