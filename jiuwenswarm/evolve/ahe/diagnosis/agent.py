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
from typing import Any

from jiuwenswarm.evolve.ahe.diagnosis.models import (
    DiagnosisIssue,
    DiagnosisResult,
    ALLOWED_ISSUE_TYPES,
)
from jiuwenswarm.evolve.ahe.diagnosis.prompts import (
    DIAGNOSIS_SYSTEM_PROMPT,
    TOOL_DESCRIPTIONS,
)
from jiuwenswarm.evolve.ahe.diagnosis.tools import DiagnosisToolExecutor

logger = logging.getLogger(__name__)


class DiagnosisAgent:
    """Lightweight ReAct Agent for trace diagnosis.

    Not a DeepAgent — uses its own simple loop with 7 read-only tools.
    Context management: tool output truncation + context compression
    when approaching token limits.
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
        trace_ids: list[str],
        mode: str = "diagnose",
        question: str | None = None,
    ) -> DiagnosisResult:
        """Execute ReAct loop, return diagnosis result."""
        if mode not in ("diagnose", "propose"):
            raise ValueError(f"mode must be 'diagnose' or 'propose', got '{mode}'")

        # Initialize tool executor
        if self._store:
            self._tool_executor = DiagnosisToolExecutor(
                store=self._store, workspace_dir=self._workspace_dir
            )

        # Build initial messages
        messages = self._build_messages(trace_ids, mode, question)

        # ReAct loop
        for iteration in range(self._max_iterations):
            # 1. Call LLM
            response_text = await self._call_llm(messages)

            # 2. Parse tool calls
            tool_calls = self._parse_tool_calls(response_text)

            if not tool_calls:
                # Pure text response → append and continue
                messages.append({"role": "assistant", "content": response_text})
                continue

            # 3. Execute tools
            tool_results = []
            for tc in tool_calls:
                if tc["name"] == "submit_result":
                    # Stop signal — parse result
                    return self._finalize(tc["arguments"].get("result", ""), iteration + 1, mode)

                result = self._execute_tool(tc["name"], tc["arguments"])
                tool_results.append({
                    "tool_name": tc["name"],
                    "arguments": tc["arguments"],
                    "result": result,
                })

            # 4. Append to messages
            messages.append({"role": "assistant", "content": response_text})
            for tr in tool_results:
                messages.append({
                    "role": "tool_result",
                    "content": json.dumps(tr, ensure_ascii=False, default=str),
                })

            # 5. Check context size and compress if needed
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

    async def generate(self, batch: Any) -> list:
        """ProposalGenerator interface — propose mode output."""
        from jiuwenswarm.evolve.models import (
            Proposal, ProposalTargetType, ProposalState, EvidenceRef,
        )

        result = await self.run(
            trace_ids=batch.trace_ids,  # type: ignore[attr-defined]
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
        # System prompt
        system_msg = DIAGNOSIS_SYSTEM_PROMPT

        # Add tool descriptions
        tool_desc = "\n\n## 工具详细说明\n"
        for name, desc in TOOL_DESCRIPTIONS.items():
            tool_desc += f"\n**{name}**: {desc}\n"
        system_msg += tool_desc

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

    async def _call_llm(self, messages: list[dict]) -> str:
        """Call LLM using openjiuwen Model — same pattern as LLMProposer."""
        if self._model is None:
            # Lazy init from config
            self._model = await self._init_model()

        try:
            from openjiuwen.core.foundation.llm import (
                Model, ModelClientConfig, ModelRequestConfig,
                SystemMessage, UserMessage,
            )
        except ImportError:
            logger.warning("DiagnosisAgent: openjiuwen.llm not available")
            return ""

        # Convert message dicts to openjiuwen message objects
        oiwen_messages = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                oiwen_messages.append(SystemMessage(content=content))
            else:
                oiwen_messages.append(UserMessage(content=content))

        try:
            response = await self._model.invoke(messages=oiwen_messages)
            content = response.content if hasattr(response, "content") else str(response)
            return str(content)
        except Exception as exc:
            logger.warning("DiagnosisAgent._call_llm failed: %s", exc)
            return ""

    async def _init_model(self):
        """Initialize openjiuwen Model from config."""
        try:
            from openjiuwen.core.foundation.llm import (
                Model, ModelClientConfig, ModelRequestConfig,
            )
            from jiuwenswarm.common.config import get_default_models
            from jiuwenswarm.common.utils import get_env_file

            # Load .env if not already loaded
            env_file = get_env_file()
            if env_file.exists():
                from dotenv import load_dotenv
                load_dotenv(dotenv_path=env_file, override=False)

            models = get_default_models()
            if not models:
                logger.warning("DiagnosisAgent: no default model configured")
                return None

            first = models[0]
            client_cfg = first.get("model_client_config", {})
            model_cfg = first.get("model_config_obj", {})

            model = Model(
                model_client_config=ModelClientConfig(
                    client_provider=client_cfg.get("client_provider", "OpenAI"),
                    api_base=client_cfg.get("api_base", ""),
                    api_key=client_cfg.get("api_key", ""),
                    verify_ssl=client_cfg.get("verify_ssl", False),
                ),
                model_config=ModelRequestConfig(
                    model=client_cfg.get("model_name", "gpt-4"),
                    temperature=self._temperature,
                    max_tokens=20000,
                ),
            )
            return model
        except Exception as exc:
            logger.warning("DiagnosisAgent._init_model failed: %s", exc)
            return None

    @staticmethod
    def _parse_tool_calls(content: str) -> list[dict]:
        """Parse tool calls from LLM response.

        Strategy:
        1. Try entire response as JSON
        2. Regex search for tool_name + arguments patterns
        """
        # Strategy 1: Full JSON parse
        try:
            parsed = json.loads(content.strip())
            if isinstance(parsed, dict):
                if "tool_name" in parsed:
                    return [parsed]
                if "name" in parsed and "arguments" in parsed:
                    return [{"name": parsed["name"], "arguments": parsed["arguments"]}]
            if isinstance(parsed, list):
                results = []
                for item in parsed:
                    if isinstance(item, dict) and ("tool_name" in item or "name" in item):
                        results.append({
                            "name": item.get("tool_name", item.get("name", "")),
                            "arguments": item.get("arguments", item.get("args", {})),
                        })
                return results
        except (json.JSONDecodeError, TypeError):
            pass

        # Strategy 2: Regex search for tool call patterns
        pattern = r'\{[^{}]*"tool_name"\s*:\s*"(\w+)"[^{}]*"arguments"\s*:\s*\{[^{}]*\}[^{}]*\}'
        matches = re.findall(pattern, content, re.DOTALL)

        if not matches:
            # Try alternative pattern
            pattern2 = r'"tool_name"\s*:\s*"(\w+)"'
            names = re.findall(pattern2, content)
            if names:
                # Extract arguments nearby
                results = []
                for name in names:
                    args = {}
                    # Simple heuristic: find "arguments": {...} after the name
                    args_pattern = r'"arguments"\s*:\s*\{([^{}]*)\}'
                    args_match = re.search(args_pattern, content)
                    if args_match:
                        try:
                            args = json.loads("{" + args_match.group(1) + "}")
                        except json.JSONDecodeError:
                            args = {}
                    results.append({"name": name, "arguments": args})
                return results

        # Parse full match objects
        results = []
        full_matches = re.finditer(pattern, content, re.DOTALL)
        for m in full_matches:
            try:
                parsed = json.loads(m.group(0))
                results.append({
                    "name": parsed.get("tool_name", ""),
                    "arguments": parsed.get("arguments", {}),
                })
            except (json.JSONDecodeError, TypeError):
                pass

        return results

    def _execute_tool(self, tool_name: str, arguments: dict) -> dict:
        """Execute a tool via DiagnosisToolExecutor."""
        if self._tool_executor is None:
            return {"error": "No tool executor configured (store not provided)"}
        return self._tool_executor.execute(tool_name, arguments)

    def _finalize(
        self, result_json: str, iterations: int, mode: str
    ) -> DiagnosisResult:
        """Parse submit_result payload into DiagnosisResult."""
        try:
            payload = json.loads(result_json)
        except json.JSONDecodeError:
            # Try extracting JSON from text
            m = re.search(r"\{.*\}", result_json, re.DOTALL)
            if m:
                try:
                    payload = json.loads(m.group(0))
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

        # Validate and convert
        issues = []
        for raw_issue in payload.get("issues", []):
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
            except ValueError as exc:
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
