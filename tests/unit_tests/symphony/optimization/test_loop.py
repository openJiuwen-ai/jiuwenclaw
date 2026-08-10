from dataclasses import replace

from jiuwenswarm.symphony.optimization import (
    PromptOptimizer,
    TaskCase,
    TaskSpec,
    default_optimization_config,
)
from jiuwenswarm.symphony.optimization.drift.base import NullDriftJudge
from jiuwenswarm.symphony.optimization.environment.callable_env import CallableEnvironment
from jiuwenswarm.symphony.optimization.memory.base import JsonlPromptMemory
from jiuwenswarm.symphony.optimization.models import PromptCandidate
from jiuwenswarm.symphony.optimization.policy.base import PolicyRequest, PromptPolicy
from jiuwenswarm.symphony.optimization.reward.components import CustomReward
from jiuwenswarm.symphony.optimization.reward.composite import CompositeReward


class VersionPolicy(PromptPolicy):
    """Each iteration proposes prompts tagged with the iteration number."""

    async def generate(self, request: PolicyRequest) -> list[PromptCandidate]:
        n = request.iteration
        return [
            PromptCandidate(prompt=f"v{n}", rationale=f"iter {n}")
            for _ in range(request.num_candidates)
        ]


def _runner(system_prompt: str, case_input: str) -> str:
    ver = int(system_prompt[1:])
    return "- item\n" * ver


def _quality(execution, task) -> float:
    outs = execution.visible_results
    scores = [min(1.0, len(r.output.splitlines()) / 4) for r in outs]
    return sum(scores) / max(1, len(scores))


def _make_optimizer(tmp_path, **overrides):
    config = replace(
        default_optimization_config(),
        candidate_prompts=2,
        max_iterations=6,
        drift_penalty=0.0,
        **overrides,
    )
    reward = CompositeReward(
        [CustomReward("quality", _quality)],
        {"quality": 1.0},
        min_correctness=0.0,
        drift_penalty=0.0,
    )
    return PromptOptimizer(
        config,
        policy=VersionPolicy(),
        environment=CallableEnvironment(_runner, attribute_tokens=False),
        reward_model=reward,
        drift_judge=NullDriftJudge(),
        memory=JsonlPromptMemory(tmp_path),
    )


async def test_reward_improves_and_converges(tmp_path):
    optimizer = _make_optimizer(tmp_path)
    task = TaskSpec(objective="produce a 4-item list", cases=[TaskCase(input="x")])

    result = await optimizer.optimize(task)

    assert result.success is True
    scores = [it.best_score for it in result.iterations]
    assert scores[0] < scores[-1]        # reward climbed
    assert result.best_score == max(scores)
    assert result.best_prompt == "v4"    # v4 first reaches 4 items -> reward 1.0
    assert result.converged is True


async def test_best_prompt_persisted_to_memory(tmp_path):
    optimizer = _make_optimizer(tmp_path)
    task = TaskSpec(objective="produce a 4-item list", cases=[TaskCase(input="x")])

    result = await optimizer.optimize(task)

    memory = JsonlPromptMemory(tmp_path)
    records = memory.search_similar(task, top_k=5)
    assert records, "expected the best prompt to be stored"
    assert any(r.prompt == result.best_prompt for r in records)


async def test_parallel_and_sequential_agree(tmp_path):
    task = TaskSpec(objective="produce a 4-item list", cases=[TaskCase(input="x")])
    par = await _make_optimizer(tmp_path / "p", parallel_execution=True).optimize(task)
    seq = await _make_optimizer(tmp_path / "s", parallel_execution=False).optimize(task)
    assert par.best_prompt == seq.best_prompt
    assert par.best_score == seq.best_score
