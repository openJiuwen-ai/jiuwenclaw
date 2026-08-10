"""Built-in reward components.

Each returns a scalar in ``[0, 1]`` (higher is better). ``CorrectnessReward`` is
LLM-backed and reuses the JSON-judge idiom of
:class:`jiuwenswarm.symphony.experience.evaluator.TraceEvaluator`; the rest are
cheap and deterministic. Register any custom metric by subclassing
:class:`RewardComponent` and adding a weight in ``symphony.optimization.reward_weights``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

from jiuwenswarm.symphony.llm import llm_usage_context
from jiuwenswarm.symphony.optimization.models import CaseResult, Execution, TaskSpec
from jiuwenswarm.symphony.optimization.reward.base import RewardComponent

LOGGER = logging.getLogger(__name__)

_CORRECTNESS_SYSTEM = (
    "You grade whether an assistant output correctly satisfies a task. "
    "Output only valid JSON."
)

_CORRECTNESS_PROMPT = """\
Task objective: {objective}
Case input: {case_input}
{expected_block}
Assistant output:
{output}

Rate how well the output satisfies the objective for this input on a 0.0-1.0 scale,
where 1.0 is fully correct and complete and 0.0 is wrong or empty.

Output ONLY valid JSON: {{"score": <float 0..1>, "reason": "<short reason>"}}
"""


def _clamp01(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, parsed))


class CorrectnessReward(RewardComponent):
    """LLM-judged correctness of the visible outputs against the objective/expected."""

    name = "correctness"

    def __init__(self, client: Any, *, max_concurrency: int = 5) -> None:
        self._client = client
        self._sem = asyncio.Semaphore(max(1, max_concurrency))

    async def score(self, execution: Execution, task: TaskSpec) -> float:
        return await self.correctness_on(execution.visible_results, task)

    async def correctness_on(self, results: list[CaseResult], task: TaskSpec) -> float:
        graded = [r for r in results if r.error is None]
        if not graded:
            return 0.0
        scores = await asyncio.gather(
            *(self._judge_case(task.objective, r) for r in graded)
        )
        return sum(scores) / len(scores)

    async def _judge_case(self, objective: str, result: CaseResult) -> float:
        if not (result.output or "").strip():
            return 0.0
        expected_block = ""
        # The expected answer, when present, is carried on the TaskCase; results
        # only keep the input, so callers put expectations in the objective when
        # there is no per-case ground truth. We still judge against the objective.
        prompt = _CORRECTNESS_PROMPT.format(
            objective=objective,
            case_input=result.case_input,
            expected_block=expected_block,
            output=result.output,
        )
        try:
            async with self._sem:
                with llm_usage_context("optimization", "reward_correctness"):
                    raw = await self._client.complete_json_async(
                        system_prompt=_CORRECTNESS_SYSTEM,
                        user_content=prompt,
                        error_context="Prompt optimizer correctness judge",
                        request_overrides={"extra_body": {"thinking": {"type": "disabled"}}},
                    )
            data = json.loads(raw)
            return _clamp01(data.get("score"))
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("CorrectnessReward: judge failed, scoring 0.0: %s", exc)
            return 0.0


class CorrectnessRewardWithExpected(CorrectnessReward):
    """Correctness variant that folds each case's ``expected`` answer into the judge."""

    def __init__(self, client: Any, task: TaskSpec, *, max_concurrency: int = 5) -> None:
        super().__init__(client, max_concurrency=max_concurrency)
        self._expected = {c.input: c.expected for c in task.cases if c.expected}

    async def _judge_case(self, objective: str, result: CaseResult) -> float:
        if not (result.output or "").strip():
            return 0.0
        expected = self._expected.get(result.case_input, "")
        expected_block = f"Expected answer: {expected}\n" if expected else ""
        prompt = _CORRECTNESS_PROMPT.format(
            objective=objective,
            case_input=result.case_input,
            expected_block=expected_block,
            output=result.output,
        )
        try:
            async with self._sem:
                with llm_usage_context("optimization", "reward_correctness"):
                    raw = await self._client.complete_json_async(
                        system_prompt=_CORRECTNESS_SYSTEM,
                        user_content=prompt,
                        error_context="Prompt optimizer correctness judge",
                        request_overrides={"extra_body": {"thinking": {"type": "disabled"}}},
                    )
            data = json.loads(raw)
            return _clamp01(data.get("score"))
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("CorrectnessReward: judge failed, scoring 0.0: %s", exc)
            return 0.0


class CompletenessReward(RewardComponent):
    """Heuristic completeness: fraction of visible cases that produced non-trivial output."""

    name = "completeness"

    def __init__(self, *, min_chars: int = 1) -> None:
        self._min_chars = max(1, min_chars)

    async def score(self, execution: Execution, task: TaskSpec) -> float:
        results = execution.visible_results
        if not results:
            return 0.0
        ok = sum(
            1 for r in results if r.error is None and len((r.output or "").strip()) >= self._min_chars
        )
        return ok / len(results)


class LatencyReward(RewardComponent):
    """Lower average latency scores higher. ``half_life_s`` is where the score hits 0.5."""

    name = "latency"

    def __init__(self, *, half_life_s: float = 5.0) -> None:
        self._half_life = max(1e-3, half_life_s)

    async def score(self, execution: Execution, task: TaskSpec) -> float:
        return 1.0 / (1.0 + execution.latency_s / self._half_life)


class TokenUsageReward(RewardComponent):
    """Fewer tokens scores higher. ``half_life_tokens`` is where the score hits 0.5."""

    name = "token_usage"

    def __init__(self, *, half_life_tokens: int = 2000) -> None:
        self._half_life = max(1, half_life_tokens)

    async def score(self, execution: Execution, task: TaskSpec) -> float:
        return 1.0 / (1.0 + execution.total_tokens / self._half_life)


class CostReward(RewardComponent):
    """Lower monetary cost scores higher. ``price_per_mtok`` in currency per 1M tokens."""

    name = "cost"

    def __init__(self, *, price_per_mtok: float = 0.0, half_life_cost: float = 0.01) -> None:
        self._price = max(0.0, price_per_mtok)
        self._half_life = max(1e-9, half_life_cost)

    async def score(self, execution: Execution, task: TaskSpec) -> float:
        cost = execution.total_tokens / 1_000_000 * self._price
        return 1.0 / (1.0 + cost / self._half_life)


class StructuredValidationReward(RewardComponent):
    """Fraction of visible outputs that pass a validator (regex, JSON-parse, or callable)."""

    name = "structured_validation"

    def __init__(self, validator: Callable[[str], bool]) -> None:
        self._validator = validator

    async def score(self, execution: Execution, task: TaskSpec) -> float:
        results = execution.visible_results
        if not results:
            return 0.0
        ok = 0
        for r in results:
            try:
                if self._validator(r.output or ""):
                    ok += 1
            except Exception:  # noqa: BLE001
                continue
        return ok / len(results)


class CustomReward(RewardComponent):
    """Wrap any user callable ``(execution, task) -> float`` (sync or async)."""

    def __init__(self, name: str, fn: Callable[[Execution, TaskSpec], Any]) -> None:
        self.name = name
        self._fn = fn

    async def score(self, execution: Execution, task: TaskSpec) -> float:
        result: Any = self._fn(execution, task)
        if isinstance(result, Awaitable):
            result = await result
        return _clamp01(result)


__all__ = [
    "CorrectnessReward",
    "CorrectnessRewardWithExpected",
    "CompletenessReward",
    "LatencyReward",
    "TokenUsageReward",
    "CostReward",
    "StructuredValidationReward",
    "CustomReward",
]
