"""The RLAF-P optimization loop.

Structure mirrors :class:`jiuwenswarm.symphony.build.SymphonyScoreBuilder.build`:
a driver that pulls its collaborators (Policy, Environment, RewardModel, DriftJudge,
Memory, HistoryCompressor, ConvergenceDetector) as injected dependencies and emits a
JSONL run log. Every collaborator is an interface with a default built by
:class:`OptimizerRuntimeFactory`, so any one can be swapped without touching the loop.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from jiuwenswarm.symphony.llm import (
    get_llm_token_usage_summary,
    reset_llm_token_usage,
)
from jiuwenswarm.symphony.optimization.config import OptimizationConfig
from jiuwenswarm.symphony.optimization.convergence import ConvergenceDetector
from jiuwenswarm.symphony.optimization.drift.base import DriftJudge
from jiuwenswarm.symphony.optimization.environment.base import PromptEnvironment
from jiuwenswarm.symphony.optimization.memory.base import NullPromptMemory, PromptMemory
from jiuwenswarm.symphony.optimization.models import (
    Evaluation,
    Execution,
    IterationRecord,
    OptimizationResult,
    PromptCandidate,
    PromptRecord,
    TaskSpec,
)
from jiuwenswarm.symphony.optimization.policy.base import PolicyRequest, PromptPolicy
from jiuwenswarm.symphony.optimization.policy.history import (
    HistoryCompressor,
    HistoryEntry,
    OptimizationHistory,
)
from jiuwenswarm.symphony.optimization.reward.base import RewardModel
from jiuwenswarm.symphony.optimization.run_log import NullRunLogger, OptimizerRunLogger

LOGGER = logging.getLogger(__name__)


class PromptOptimizer:
    """Iterative prompt optimizer. See module docstring for the collaboration shape."""

    def __init__(
        self,
        config: OptimizationConfig,
        *,
        policy: PromptPolicy,
        environment: PromptEnvironment,
        reward_model: RewardModel,
        drift_judge: DriftJudge,
        memory: PromptMemory | None = None,
        history_compressor: HistoryCompressor | None = None,
        convergence: ConvergenceDetector | None = None,
        run_logger: OptimizerRunLogger | None = None,
    ) -> None:
        self._config = config
        self._policy = policy
        self._environment = environment
        self._reward = reward_model
        self._drift = drift_judge
        self._memory = memory or NullPromptMemory()
        self._compressor = history_compressor or HistoryCompressor(None)
        self._convergence = convergence or ConvergenceDetector(
            threshold=config.convergence_threshold, window=config.convergence_window
        )
        self._log = run_logger or NullRunLogger()

    async def optimize(self, task: TaskSpec) -> OptimizationResult:
        run_id = _new_run_id()
        reset_llm_token_usage()
        self._log.reset()
        self._log.record(
            "run.start",
            run_id=run_id,
            objective=task.objective,
            candidate_prompts=self._config.candidate_prompts,
            max_iterations=self._config.max_iterations,
        )

        history = OptimizationHistory()
        similar = self._safe_search(task)
        if similar:
            self._log.record("memory.warm_start", count=len(similar))

        best_prompt = task.base_prompt
        best_score = float("-inf")
        iterations: list[IterationRecord] = []
        converged = False
        convergence_reason = ""

        for iteration in range(1, self._config.max_iterations + 1):
            request = PolicyRequest(
                task=task,
                history=history,
                num_candidates=self._config.candidate_prompts,
                iteration=iteration,
                temperature=self._config.policy_temperature,
                similar_records=similar if iteration == 1 else [],
            )
            candidates = await self._policy.generate(request)
            self._log.record(
                "policy.candidates",
                iteration=iteration,
                count=len(candidates),
                candidates=[c.to_dict() for c in candidates],
            )

            executions = await self._execute_all(candidates, task)
            drift_scores = await self._drift_all(candidates, task)
            breakdowns = await self._reward.evaluate(executions, task, drift_scores)
            evaluations = [Evaluation(ex, bd) for ex, bd in zip(executions, breakdowns)]

            best_eval = max(evaluations, key=lambda e: e.score, default=None)
            if best_eval is None:
                self._log.record("iteration.empty", iteration=iteration)
                break

            observations = _observations(best_eval)
            history.add(
                HistoryEntry(
                    iteration=iteration,
                    prompt=best_eval.candidate.prompt,
                    reward=best_eval.score,
                    observations=observations,
                )
            )
            history.compressed = await self._compressor.compress(history)

            state = self._convergence.update(best_eval.score)

            record = IterationRecord(
                iteration=iteration,
                evaluations=evaluations,
                best_candidate_id=best_eval.candidate.candidate_id,
                best_score=best_eval.score,
                converged=state.converged,
                convergence_reason=state.reason,
                observations=observations,
            )
            iterations.append(record)
            self._log.record(
                "iteration.done",
                iteration=iteration,
                best_score=round(best_eval.score, 4),
                best_candidate_id=best_eval.candidate.candidate_id,
                moving_average=round(state.moving_average, 4),
                variance=round(state.variance, 6),
                drift=round(best_eval.reward.drift, 4),
                reward_breakdown=best_eval.reward.to_dict(),
                observations=observations,
            )

            if best_eval.score > best_score:
                best_score = best_eval.score
                best_prompt = best_eval.candidate.prompt

            if state.converged:
                converged = True
                convergence_reason = state.reason
                self._log.record("run.converged", iteration=iteration, reason=state.reason)
                break

        if not converged and iterations:
            convergence_reason = "max_iterations reached"

        token_usage = _safe_token_summary()
        self._persist_best(task, best_prompt, best_score, iterations)

        result = OptimizationResult(
            success=bool(iterations),
            best_prompt=best_prompt,
            best_score=best_score if best_score != float("-inf") else 0.0,
            iterations=iterations,
            converged=converged,
            convergence_reason=convergence_reason,
            token_usage=token_usage,
            detail="" if iterations else "no iterations produced a candidate",
            run_id=run_id,
        )
        self._log.record(
            "run.done",
            run_id=run_id,
            best_score=round(result.best_score, 4),
            iterations=len(iterations),
            converged=converged,
            reason=convergence_reason,
        )
        return result

    async def _execute_all(
        self, candidates: list[PromptCandidate], task: TaskSpec
    ) -> list[Execution]:
        if self._config.parallel_execution:
            return list(
                await asyncio.gather(*(self._environment.execute(c, task) for c in candidates))
            )
        return [await self._environment.execute(c, task) for c in candidates]

    async def _drift_all(
        self, candidates: list[PromptCandidate], task: TaskSpec
    ) -> dict[str, float]:
        if self._config.drift_penalty <= 0:
            return {c.candidate_id: 0.0 for c in candidates}
        scores = await asyncio.gather(
            *(self._drift.deviation(task.objective, c) for c in candidates)
        )
        return {c.candidate_id: s for c, s in zip(candidates, scores)}

    def _safe_search(self, task: TaskSpec) -> list[PromptRecord]:
        try:
            return self._memory.search_similar(task, top_k=3)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("PromptOptimizer: memory search failed: %s", exc)
            return []

    def _persist_best(
        self,
        task: TaskSpec,
        best_prompt: str,
        best_score: float,
        iterations: list[IterationRecord],
    ) -> None:
        if not iterations or not best_prompt or best_score == float("-inf"):
            return
        best_eval = _best_evaluation(iterations)
        metrics = best_eval.reward.raw_components if best_eval else {}
        observations = best_eval.reward.notes if best_eval else []
        record = PromptRecord(
            prompt=best_prompt,
            reward=best_score,
            objective=task.objective,
            task_characteristics=task.characteristics,
            metrics=dict(metrics),
            observations=list(observations),
            metadata={"iterations": len(iterations), **task.metadata},
        )
        try:
            self._memory.add(record)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("PromptOptimizer: failed to persist best prompt: %s", exc)


def _observations(evaluation: Evaluation) -> list[str]:
    obs: list[str] = []
    if evaluation.candidate.rationale:
        obs.append(evaluation.candidate.rationale)
    obs.extend(evaluation.reward.notes)
    # surface the strongest and weakest component to guide the next policy step
    components = evaluation.reward.raw_components
    if components:
        strongest = max(components, key=components.get)
        weakest = min(components, key=components.get)
        if strongest != weakest:
            obs.append(
                f"strongest metric: {strongest} ({components[strongest]:.2f}); "
                f"weakest: {weakest} ({components[weakest]:.2f})"
            )
    return obs


def _best_evaluation(iterations: list[IterationRecord]) -> Evaluation | None:
    best: Evaluation | None = None
    for record in iterations:
        for evaluation in record.evaluations:
            if best is None or evaluation.score > best.score:
                best = evaluation
    return best


def _safe_token_summary() -> dict:
    try:
        return get_llm_token_usage_summary()
    except Exception:  # noqa: BLE001
        return {}


def _new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


__all__ = ["PromptOptimizer"]
