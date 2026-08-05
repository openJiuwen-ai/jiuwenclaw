# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""ResearchRun 状态机离线测试。"""

from __future__ import annotations

import json

import pytest

from jiuwenswarm.agents.harness.evidence_first.run_state_machine import (
    FunnelDecision,
    ResearchRun,
    RunStage,
    predefined_funnel_decision,
)


def make_run() -> ResearchRun:
    return ResearchRun(
        "S1", "预算漏斗测试", "验证 A 机制",
        budget_cap_tokens=100_000,
    )


def test_full_lifecycle():
    run = make_run()
    assert run.state == RunStage.IDEA
    assert run.plan()
    assert run.execute_tier("smoke", {"answer_accuracy": 0.8}, {
        "a": {"answer_accuracy": 0.80}, "b": {"answer_accuracy": 0.85},
    }) == FunnelDecision.PASS_TO_NEXT
    assert run.state == RunStage.EXPERIMENT_VERIFY
    assert run.execute_tier("verify", {"answer_accuracy": 0.75}, {
        "a": {"answer_accuracy": 0.75}, "b": {"answer_accuracy": 0.82},
    }) == FunnelDecision.PASS_TO_NEXT
    assert run.execute_tier("full", {"answer_accuracy": 0.7}, {
        "a": {"answer_accuracy": 0.70}, "b": {"answer_accuracy": 0.72},
    }) == FunnelDecision.DONE
    assert run.state == RunStage.ANALYSIS
    assert run.write_paper()
    assert run.replay()
    assert run.done()
    assert run.state == RunStage.DONE


def test_ceiling_stop():
    run = make_run()
    run.plan()
    decision = run.execute_tier("smoke", {"answer_accuracy": 0.82}, {
        "a": {"answer_accuracy": 0.82}, "b": {"answer_accuracy": 0.82},
        "c": {"answer_accuracy": 0.81}, "d": {"answer_accuracy": 0.82},
    })
    assert decision == FunnelDecision.STOP_CEILING
    assert run.state == RunStage.STOPPED


def test_low_metric_stop():
    run = make_run()
    run.plan()
    decision = run.execute_tier("smoke", {"answer_accuracy": 0.2}, {
        "a": {"answer_accuracy": 0.2}, "b": {"answer_accuracy": 0.7},
    })
    assert decision == FunnelDecision.STOP_LOW_METRIC
    assert run.state == RunStage.STOPPED


def test_illegal_transition_rejected():
    run = make_run()
    # 从 IDEA 直接跳到 PAPER 非法。
    assert not run.transition(RunStage.PAPER)
    assert run.state == RunStage.IDEA


def test_terminal_immutable():
    run = make_run()
    run.stop("止损")
    assert run.state == RunStage.STOPPED
    assert not run.write_paper()


def test_budget_cap_exhausted():
    run = make_run()
    run.budget.add(120_000, 100_000, 20_000)
    assert not run.plan()
    assert run.state == RunStage.FAILED
    assert run.completed_at is not None


def test_save_load_roundtrip(tmp_path):
    run = make_run()
    run.plan()
    run.budget.add(5000, 3000, 2000, calls=3)
    path = tmp_path / "run.json"
    run.save(path)
    loaded = ResearchRun.load(path)
    assert loaded.run_id == run.run_id
    assert loaded.state == run.state
    assert loaded.budget.tokens_total == 5000
    assert loaded.transitions[-1].to_stage == RunStage.PLAN


def test_predefined_funnel_decision_units():
    assert predefined_funnel_decision("agent", {"answer_accuracy": 0.7}, "full", {}) == FunnelDecision.DONE
    assert predefined_funnel_decision(
        "claim", {"f1": 0.8}, "smoke",
        {"a": {"f1": 0.8}, "b": {"f1": 0.8}},
    ) == FunnelDecision.STOP_CEILING
    assert predefined_funnel_decision(
        "claim", {"f1": 0.3}, "smoke",
        {"a": {"f1": 0.3}, "b": {"f1": 0.8}},
    ) == FunnelDecision.STOP_LOW_METRIC
