# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""ResearchQueue 预算感知队列离线测试。"""

from __future__ import annotations

import json

from jiuwenswarm.agents.harness.evidence_first.research_queue import ResearchQueue
from jiuwenswarm.agents.harness.evidence_first.run_state_machine import (
    ResearchRun,
    RunStage,
)


def _cheap_runner(run: ResearchRun) -> None:
    """假 runner：推进状态机并记一点 token 预算（含分级明细）。"""
    run.plan()
    run.execute_tier("smoke", {"answer_accuracy": 0.9}, {"a": {"answer_accuracy": 0.90}})
    run.budget.add_stage(RunStage.EXPERIMENT_SMOKE, 250, 1)
    run.execute_tier("verify", {"answer_accuracy": 0.9}, {"a": {"answer_accuracy": 0.90}})
    run.budget.add_stage(RunStage.EXPERIMENT_VERIFY, 250, 1)
    run.execute_tier("full", {"answer_accuracy": 0.9}, {"a": {"answer_accuracy": 0.90}})
    run.budget.add_stage(RunStage.EXPERIMENT_FULL, 250, 1)
    run.write_paper()
    run.replay()
    run.done()
    run.budget.add(1000, 600, 400, calls=4)


def test_queue_budget_cap_skips_remaining():
    # 软封顶语义：单个 run 的成本在启动前不可知，累计超过预算后不再调度新 run。
    q = ResearchQueue(budget_cap_tokens=500)
    r1 = ResearchRun("r1", "t1", "h1")
    r2 = ResearchRun("r2", "t2", "h2")
    r3 = ResearchRun("r3", "t3", "h3")
    q.schedule(r1); q.schedule(r2); q.schedule(r3)

    result = q.execute(runner=_cheap_runner)
    assert result.completed == 1          # 只有 r1 完整执行
    assert len(result.skipped) == 2       # r2/r3 因预算跳过
    assert r1.state == RunStage.DONE
    assert r2.state == RunStage.STOPPED
    assert r3.state == RunStage.STOPPED


def test_queue_no_cap_runs_all():
    q = ResearchQueue()
    for i in range(3):
        q.schedule(ResearchRun(f"r{i}", f"t{i}", "h"))
    result = q.execute(runner=_cheap_runner)
    assert result.completed == 3
    assert result.total_tokens == 1750 * 3
    assert result.total_calls == 7 * 3


def test_runner_error_marks_failed():
    def bad_runner(run: ResearchRun) -> None:
        raise RuntimeError("engine crash")

    q = ResearchQueue()
    r = ResearchRun("r1", "t1", "h1")
    q.schedule(r)
    result = q.execute(runner=bad_runner)
    assert r.state == RunStage.FAILED
    assert result.executed[0]["state"] == "FAILED"


def test_resource_log(tmp_path):
    q = ResearchQueue()
    q.schedule(ResearchRun("r1", "t1", "h1"))
    result = q.execute(runner=_cheap_runner)
    log = q.write_resource_log(result, tmp_path / "usage.json")
    data = json.loads(log.read_text(encoding="utf-8"))
    assert data["total_tokens"] == 1750
    assert data["completed_runs"] == 1
    assert data["runs"][0]["run_id"] == "r1"
    assert data["runs"][0]["per_stage_tokens"]
