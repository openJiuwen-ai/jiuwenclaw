# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for RectifyOrRejectAuditor."""

from __future__ import annotations

import json

import pytest

from jiuwenswarm.agents.dropout.auditor import RectifyOrRejectAuditor
from jiuwenswarm.agents.dropout.metrics import get_simple_team_metrics


def _flawed_response(metric_hint: str = "") -> str:
    return json.dumps(
        {
            "evidence_quote": "bad claim",
            "analysis": f"Fatal incorrect information {metric_hint}".strip(),
            "suggestion": "Remove the fabricated claim and state verified facts.",
            "impact_assessment": "YES — misleads the team",
            "is_flawed": True,
        }
    )


def _correct_response() -> str:
    return json.dumps(
        {
            "evidence_quote": "N/A",
            "analysis": "N/A",
            "suggestion": "N/A",
            "impact_assessment": "NO",
            "is_flawed": False,
        }
    )


@pytest.mark.asyncio
async def test_judge_passes_when_all_metrics_correct():
    async def llm(_prompt: str) -> str:
        return _correct_response()

    auditor = RectifyOrRejectAuditor(llm=llm, pass_rate=1.0, use_simple_audit=True)
    result = await auditor.judge(
        task="Write a summary",
        agent_output="Here is a correct summary of the plan.",
        attempt_num=1,
        role="teammate",
    )
    assert result.passed is True
    assert result.feedback is None
    assert result.total_metrics == len(get_simple_team_metrics())
    assert result.pass_count == result.total_metrics


@pytest.mark.asyncio
async def test_judge_fails_and_builds_feedback():
    async def llm(_prompt: str) -> str:
        return _flawed_response()

    auditor = RectifyOrRejectAuditor(llm=llm, pass_rate=1.0, use_simple_audit=True)
    result = await auditor.judge(
        task="Share findings",
        agent_output="The sky is made of cheese.",
        attempt_num=2,
        role="researcher",
    )
    assert result.passed is False
    assert result.feedback is not None
    assert "Attempt 2" in result.feedback
    assert "CRITICAL_" in result.feedback
    assert result.pass_count == 0


@pytest.mark.asyncio
async def test_pass_rate_threshold_allows_partial_pass():
    calls = {"n": 0}

    async def llm(_prompt: str) -> str:
        calls["n"] += 1
        # First metric flawed, rest correct.
        if calls["n"] == 1:
            return _flawed_response("first")
        return _correct_response()

    auditor = RectifyOrRejectAuditor(llm=llm, pass_rate=0.5, use_simple_audit=True)
    result = await auditor.judge(
        task="task",
        agent_output="mixed output",
        attempt_num=1,
    )
    assert result.total_metrics == 3
    assert result.pass_count == 2
    assert result.passed is True
    assert result.feedback is None


@pytest.mark.asyncio
async def test_judge_disabled_prune_always_passes():
    async def llm(_prompt: str) -> str:
        return _flawed_response()

    auditor = RectifyOrRejectAuditor(llm=llm, prune_enabled=False)
    result = await auditor.judge(task="t", agent_output="x")
    assert result.passed is True
    assert result.judgements == []


@pytest.mark.asyncio
async def test_judge_without_llm_presumes_validity():
    auditor = RectifyOrRejectAuditor(llm=None, use_simple_audit=True)
    result = await auditor.judge(task="t", agent_output="anything")
    assert result.passed is True
    assert all(j.is_correct for j in result.judgements)
