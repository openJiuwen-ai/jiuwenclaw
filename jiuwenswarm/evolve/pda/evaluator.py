# Copyright (c) Huawei Technologies, Co., Ltd. 2026. All rights reserved.
"""TraceOutcomeEvaluator — task completion assessment.

Pluggable: PDA algorithm owns this evaluator. LLMProposer does not use it.
Two evaluation modes:
- fast (heuristic/span_error): no LLM call, quick screening
- LLM: openjiuwen Model call for uncertain traces
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from jiuwenswarm.evolve.models import TraceOutcome

logger = logging.getLogger(__name__)

# ── System prompt for LLM evaluation ─────────────────────────────────────

TASK_EVAL_SYSTEM_PROMPT = """你是一名智能体任务完成度评估专家，负责判断 Agent 的最终输出是否完成了用户任务。

# 评估对象
你只允许基于以下两项信息进行判断：
1. 用户输入
2. Agent 最终输出
你不负责分析中间执行步骤，也不负责定位失败根因。如果仅凭用户输入和最终输出无法判断，应返回 uncertain。

# 评估步骤
1. 识别用户输入中的核心目标、明确约束和必须交付的产物。
2. 判断 Agent 最终输出是否覆盖这些核心目标和明确约束。
3. 判断输出是否存在导致任务失败的严重问题。
4. 如果任务是否完成依赖外部事实，返回 uncertain。
5. 不要因为轻微措辞问题或格式小瑕疵直接判 fail。

# 判定标准
- pass：用户核心目标已完成；主要要求被满足。
- fail：用户核心目标未完成；输出明显偏题或不可用。
- uncertain：仅凭用户输入和最终输出无法可靠判断。

# 输出格式
请严格输出 JSON，不要输出 JSON 以外的任何内容：
{
  "outcome": "pass | fail | uncertain",
  "score": 0.0-1.0,
  "confidence": 0.0-1.0,
  "reason": "一句话概括判定原因",
  "key_evidence": "引用或概括最关键的用户要求与输出证据",
  "missing_requirements": ["未满足的关键要求；如果没有则为空数组"],
  "needs_external_verification": false
}

必须使用中文输出。"""


# ── TaskNameInferrer ──────────────────────────────────────────────────────


class TaskNameInferrer:
    """从 NormalizedTrace dict 推断 task_name."""

    @staticmethod
    def infer(trace: dict) -> str:
        """策略:
        1. 如果有 skill_name → "skill_{skill_name}_{trace_id[:8]}"
        2. 从第一条 user message 提取前 30 字 → "task_{snippet}_{trace_id[:8]}"
        3. 兜底 → trace_id
        """
        trace_id = trace.get("id", trace.get("trace_id", "unknown"))

        # Strategy 1: skill_name
        skill_name = trace.get("skill_name")
        if skill_name:
            return f"skill_{skill_name}_{trace_id[:8]}"

        # Strategy 2: first user message
        messages = trace.get("messages", [])
        for msg in messages:
            if isinstance(msg, dict) and msg.get("role") == "user":
                content = msg.get("content", "")
                if content:
                    snippet = content[:30].replace("\n", " ").strip()
                    return f"task_{snippet}_{trace_id[:8]}"

        # Strategy 3: fallback
        return trace_id


# ── TraceOutcomeEvaluator ────────────────────────────────────────────────


class TraceOutcomeEvaluator:
    """Evaluate task completion from root span input/output.

    Two modes:
    - evaluate_fast(): heuristic + span_error, no LLM call
    - evaluate(): LLM-based assessment, uses openjiuwen Model
    """

    def __init__(self, model: Any | None = None):
        self._model = model

    def evaluate_fast(self, trace_dict: dict) -> TraceOutcome:
        """Non-LLM fast evaluation using heuristics and span error detection.

        Strategies:
        1. span_error: check if spans have error status → fail
        2. heuristic: check if output is empty/truncated → uncertain
        3. Default → uncertain (heuristic can't reliably judge pass/fail)
        """
        trace_id = trace_dict.get("id", trace_dict.get("trace_id", "unknown"))

        # Strategy 1: span_error detection
        # Check the trace's top-level status or spans
        status = trace_dict.get("status_code", "")
        status_desc = trace_dict.get("status_description", "")
        if status == "ERROR" or "error" in str(status_desc).lower():
            return TraceOutcome(
                trace_id=trace_id,
                outcome="fail",
                score=0.1,
                confidence=0.7,
                reason=f"Trace has error status: {status_desc}",
                judgment_method="span_error",
            )

        # Strategy 2: heuristic
        output = trace_dict.get("output", "")
        if isinstance(output, dict):
            output_content = output.get("content", "")
        else:
            output_content = str(output)

        if not output_content or output_content.strip() == "":
            return TraceOutcome(
                trace_id=trace_id,
                outcome="uncertain",
                score=0.3,
                confidence=0.5,
                reason="Agent output is empty or not available",
                judgment_method="heuristic",
            )

        # Strategy 3: default uncertain
        return TraceOutcome(
            trace_id=trace_id,
            outcome="uncertain",
            score=0.5,
            confidence=0.3,
            reason="Heuristic evaluation — cannot reliably determine outcome",
            judgment_method="heuristic",
        )

    async def evaluate(self, input_text: str, output_text: str) -> TraceOutcome:
        """LLM-based evaluation — only based on user input and Agent final output."""
        if self._model is None:
            self._model = await self._init_model()

        if self._model is None:
            logger.warning("TraceOutcomeEvaluator: no model available, falling back to heuristic")
            return TraceOutcome(
                trace_id="unknown",
                outcome="uncertain",
                score=0.5,
                reason="No LLM model available for evaluation",
            )

        try:
            from openjiuwen.core.foundation.llm import (
                SystemMessage, UserMessage,
            )

            user_msg = (
                f"[用户输入]: {input_text}\n\n[Agent最终输出]: {output_text}"
            )

            messages = [
                SystemMessage(content=TASK_EVAL_SYSTEM_PROMPT),
                UserMessage(content=user_msg),
            ]

            response = await self._model.invoke(messages=messages)
            content = response.content if hasattr(response, "content") else str(response)

            # Parse JSON response
            outcome = self._parse_llm_response(content)
            outcome.judgment_method = "llm_evaluator"
            return outcome

        except Exception as exc:
            logger.warning("TraceOutcomeEvaluator.evaluate failed: %s", exc)
            return TraceOutcome(
                trace_id="unknown",
                outcome="uncertain",
                score=0.5,
                reason=f"LLM evaluation failed: {exc}",
            )

    async def evaluate_batch(
        self, normalized_traces: list[dict]
    ) -> list[TraceOutcome]:
        """Evaluate all traces in a batch."""
        outcomes = []
        for trace in normalized_traces:
            # First try fast evaluation
            fast = self.evaluate_fast(trace)

            # If heuristic returns uncertain, try LLM evaluation
            if fast.outcome == "uncertain" and fast.judgment_method == "heuristic":
                input_text = self._extract_input(trace)
                output_text = self._extract_output(trace)
                if input_text and output_text:
                    llm_outcome = await self.evaluate(input_text, output_text)
                    llm_outcome.trace_id = trace.get("id", trace.get("trace_id", ""))
                    llm_outcome.task_name = TaskNameInferrer.infer(trace)
                    outcomes.append(llm_outcome)
                    continue

            fast.task_name = TaskNameInferrer.infer(trace)
            outcomes.append(fast)

        return outcomes

    # ── Internal helpers ──────────────────────────────────────────────

    @staticmethod
    def _extract_input(trace: dict) -> str:
        """Extract user input from NormalizedTrace."""
        input_data = trace.get("input", {})
        if isinstance(input_data, dict):
            return input_data.get("message", str(input_data))
        return str(input_data)

    @staticmethod
    def _extract_output(trace: dict) -> str:
        """Extract Agent final output from NormalizedTrace."""
        output_data = trace.get("output", {})
        if isinstance(output_data, dict):
            return output_data.get("content", str(output_data))
        return str(output_data)

    @staticmethod
    def _parse_llm_response(content: str) -> TraceOutcome:
        """Parse LLM JSON response into TraceOutcome."""
        # Try direct JSON parse
        try:
            parsed = json.loads(content.strip())
            return TraceOutcome(
                trace_id="unknown",  # Will be set by caller
                outcome=parsed.get("outcome", "uncertain"),
                score=float(parsed.get("score", 0.5)),
                confidence=float(parsed.get("confidence", 0.5)),
                reason=parsed.get("reason", ""),
                key_evidence=parsed.get("key_evidence", ""),
                missing_requirements=parsed.get("missing_requirements", []),
                needs_external_verification=parsed.get("needs_external_verification", False),
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

        # Try extracting JSON from text
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if m:
            try:
                parsed = json.loads(m.group(0))
                return TraceOutcome(
                    trace_id="unknown",
                    outcome=parsed.get("outcome", "uncertain"),
                    score=float(parsed.get("score", 0.5)),
                    confidence=float(parsed.get("confidence", 0.5)),
                    reason=parsed.get("reason", "Parsed from LLM response"),
                )
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

        logger.warning("TraceOutcomeEvaluator: failed to parse LLM response")
        return TraceOutcome(
            trace_id="unknown",
            outcome="uncertain",
            score=0.5,
            reason="Failed to parse LLM evaluation response",
        )

    async def _init_model(self):
        """Initialize openjiuwen Model from config — same pattern as LLMProposer."""
        try:
            from openjiuwen.core.foundation.llm import (
                Model, ModelClientConfig, ModelRequestConfig,
            )
            from jiuwenswarm.common.config import get_default_models
            from jiuwenswarm.common.utils import get_env_file

            env_file = get_env_file()
            if env_file.exists():
                from dotenv import load_dotenv
                load_dotenv(dotenv_path=env_file, override=False)

            models = get_default_models()
            if not models:
                return None

            first = models[0]
            client_cfg = first.get("model_client_config", {})
            model_cfg = first.get("model_config_obj", {})

            return Model(
                model_client_config=ModelClientConfig(
                    client_provider=client_cfg.get("client_provider", "OpenAI"),
                    api_base=client_cfg.get("api_base", ""),
                    api_key=client_cfg.get("api_key", ""),
                    verify_ssl=client_cfg.get("verify_ssl", False),
                ),
                model_config=ModelRequestConfig(
                    model=client_cfg.get("model_name", "gpt-4"),
                    temperature=model_cfg.get("temperature", 0.4),
                    max_tokens=2000,
                ),
            )
        except Exception as exc:
            logger.warning("TraceOutcomeEvaluator._init_model failed: %s", exc)
            return None
