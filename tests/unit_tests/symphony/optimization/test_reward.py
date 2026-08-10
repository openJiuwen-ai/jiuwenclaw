import pytest

from jiuwenswarm.symphony.optimization.models import (
    CaseResult,
    Execution,
    PromptCandidate,
    TaskCase,
    TaskSpec,
)
from jiuwenswarm.symphony.optimization.reward.components import CorrectnessReward, CustomReward
from jiuwenswarm.symphony.optimization.reward.composite import CompositeReward


class FakeCorrectness(CorrectnessReward):
    """Correctness stub with no LLM: distinct visible/hidden scores."""

    def __init__(self, visible: float, hidden: float | None = None) -> None:
        super().__init__(client=None)
        self._visible = visible
        self._hidden = hidden if hidden is not None else visible

    async def score(self, execution, task):
        return self._visible

    async def correctness_on(self, results, task):
        if results and results[0].hidden:
            return self._hidden
        return self._visible


def _execution(cid="c1", *, hidden_case=False):
    results = [CaseResult(case_input="i", output="- out", hidden=False)]
    if hidden_case:
        results.append(CaseResult(case_input="h", output="- out", hidden=True))
    return Execution(
        candidate=PromptCandidate(prompt="p", candidate_id=cid),
        case_results=results,
        latency_s=1.0,
        token_usage={"total": {"total_tokens": 100}},
    )


def _task(hidden=False):
    cases = [TaskCase(input="i")]
    if hidden:
        cases.append(TaskCase(input="h", hidden=True))
    return TaskSpec(objective="o", cases=cases)


async def test_weighted_sum_without_correctness():
    reward = CompositeReward(
        [CustomReward("a", lambda e, t: 1.0), CustomReward("b", lambda e, t: 0.0)],
        {"a": 3.0, "b": 1.0},
        min_correctness=0.0,
        drift_penalty=0.0,
    )
    [bd] = await reward.evaluate([_execution()], _task(), {})
    assert bd.score == pytest.approx(0.75)  # (3*1 + 1*0) / 4


async def test_min_correctness_gate_caps_reward():
    reward = CompositeReward(
        [FakeCorrectness(0.2), CustomReward("fast", lambda e, t: 1.0)],
        {"correctness": 1.0, "fast": 1.0},
        min_correctness=0.5,
        drift_penalty=0.0,
    )
    [bd] = await reward.evaluate([_execution()], _task(), {})
    assert bd.gated is True
    assert bd.score <= 0.2  # cannot exceed correctness once gated


async def test_drift_penalty_subtracts():
    reward = CompositeReward(
        [CustomReward("a", lambda e, t: 1.0)],
        {"a": 1.0},
        min_correctness=0.0,
        drift_penalty=0.5,
    )
    [bd] = await reward.evaluate([_execution("c1")], _task(), {"c1": 1.0})
    assert bd.drift == pytest.approx(1.0)
    assert bd.score == pytest.approx(0.5)  # 1.0 - 0.5*1.0


async def test_overfitting_penalty_on_hidden_gap():
    reward = CompositeReward(
        [FakeCorrectness(visible=0.9, hidden=0.4)],
        {"correctness": 1.0},
        min_correctness=0.0,
        drift_penalty=0.0,
    )
    [bd] = await reward.evaluate([_execution(hidden_case=True)], _task(hidden=True), {})
    # gap 0.5 > margin 0.25 -> penalty 0.5*0.5 = 0.25 subtracted from 0.9
    assert any("overfitting" in n for n in bd.notes)
    assert bd.score == pytest.approx(0.65)


async def test_execution_error_scores_zero():
    ex = _execution()
    ex.error = "boom"
    reward = CompositeReward(
        [CustomReward("a", lambda e, t: 1.0)], {"a": 1.0},
        min_correctness=0.0, drift_penalty=0.0,
    )
    [bd] = await reward.evaluate([ex], _task(), {})
    assert bd.score == 0.0
