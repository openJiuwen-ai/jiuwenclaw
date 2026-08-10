"""RLAF-P runtime prompt optimizer for JiuwenSwarm.

An RL-style feedback loop that improves system prompts over repeated executions of
similar tasks — without training any weights. A Policy (LLM) proposes candidate
prompts, an Environment executes them, a RewardModel scores the results, a DriftJudge
guards the objective, and a compressed optimization history steers the next round.

Public entrypoint::

    from jiuwenswarm.symphony.optimization import optimize_prompt, TaskSpec, TaskCase

    result = await optimize_prompt(
        TaskSpec(objective="...", cases=[TaskCase(input="...", expected="...")])
    )
    print(result.best_prompt, result.best_score)

Every collaborator (Policy / Environment / RewardModel / DriftJudge / PromptMemory)
is an ABC with a swappable default — see :mod:`.factory` and ``docs/prompt-optimization.md``.
"""

from jiuwenswarm.symphony.optimization.config import (
    OptimizationConfig,
    default_optimization_config,
    load_optimization_config,
    optimization_config_from_dict,
)
from jiuwenswarm.symphony.optimization.factory import OptimizerRuntimeFactory
from jiuwenswarm.symphony.optimization.models import (
    Evaluation,
    Execution,
    IterationRecord,
    OptimizationResult,
    PromptCandidate,
    PromptRecord,
    RewardBreakdown,
    TaskCase,
    TaskSpec,
)
from jiuwenswarm.symphony.optimization.optimizer import PromptOptimizer
from jiuwenswarm.symphony.optimization.service import default_run_log_path, optimize_prompt

__all__ = [
    "optimize_prompt",
    "default_run_log_path",
    "PromptOptimizer",
    "OptimizerRuntimeFactory",
    "OptimizationConfig",
    "load_optimization_config",
    "default_optimization_config",
    "optimization_config_from_dict",
    "TaskSpec",
    "TaskCase",
    "PromptCandidate",
    "Execution",
    "Evaluation",
    "RewardBreakdown",
    "IterationRecord",
    "OptimizationResult",
    "PromptRecord",
]
