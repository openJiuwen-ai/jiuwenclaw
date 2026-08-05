# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for verification-aware planning (veriMAP port).

Covers the pure verification core (criteria extraction, generic / structured
verifiers, the bounded verify -> revise -> re-verify loop), the swarm provider
gating, the config-driven assembly wiring, the rail's ``after_invoke`` gate
(pass / retry / escalate + criteria propagation), and the Swarmflow node-state
fields.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs

from jiuwenswarm.agents.swarm import registry
from jiuwenswarm.agents.swarm.context import SwarmBuildContext
from jiuwenswarm.agents.swarm.config_specs import (
    _verification_enabled,
    _verification_params,
    build_member_capability_specs,
)
from jiuwenswarm.agents.swarm.providers import member_rails
from jiuwenswarm.agents.harness.team.rails.team_verification_rail import (
    TeamVerificationRail,
)
from jiuwenswarm.agents.harness.team.handlers.workflow_state import (
    WorkflowProgress,
    WorkflowRunState,
)
from jiuwenswarm.agents.harness.team import verification as v


# ---------------------------------------------------------------------------
# Criteria extraction / propagation
# ---------------------------------------------------------------------------


def test_extract_criteria_reads_marker_block() -> None:
    prompt = (
        "Do the research subtask.\n\n"
        "Acceptance Criteria:\n- includes 3 sources\n- has a summary"
    )
    assert v.extract_criteria(prompt) == "- includes 3 sources\n- has a summary"


def test_extract_criteria_supports_chinese_marker() -> None:
    assert v.extract_criteria("任务\n\n验收标准：\n必须包含结论") == "必须包含结论"


@pytest.mark.parametrize("text", ["no criteria here", "", None, 123, "Acceptance Criteria:\n   "])
def test_extract_criteria_returns_none_without_body(text: object) -> None:
    assert v.extract_criteria(text) is None


# ---------------------------------------------------------------------------
# Verifiers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generic_verifier_passes_on_positive_judge() -> None:
    async def judge(_prompt: str) -> str:
        return '{"passed": true, "reason": "all points covered"}'

    outcome = await v.GenericVerifier(judge).verify("deliverable", "criteria")

    assert outcome.passed is True
    assert outcome.verifiable is True
    assert outcome.mode == v.MODE_GENERIC


@pytest.mark.asyncio
async def test_generic_verifier_fails_on_negative_judge() -> None:
    async def judge(_prompt: str) -> str:
        return 'The answer is {"passed": false, "reason": "missing summary"} overall.'

    outcome = await v.GenericVerifier(judge).verify("deliverable", "criteria")

    assert outcome.passed is False
    assert outcome.reason == "missing summary"


@pytest.mark.asyncio
async def test_generic_verifier_score_maps_to_pass() -> None:
    async def judge(_prompt: str) -> str:
        return '{"score": 1, "reason": "ok"}'

    outcome = await v.GenericVerifier(judge).verify("d", "c")
    assert outcome.passed is True
    assert outcome.score == 1.0


@pytest.mark.asyncio
async def test_generic_verifier_without_judge_is_not_applicable() -> None:
    outcome = await v.GenericVerifier(None).verify("d", "c")
    assert outcome.passed is True
    assert outcome.verifiable is False


@pytest.mark.asyncio
async def test_generic_verifier_failsoft_on_judge_error() -> None:
    async def judge(_prompt: str) -> str:
        raise RuntimeError("boom")

    outcome = await v.GenericVerifier(judge).verify("d", "c")
    # Never blocks delivery when the judge itself errors.
    assert outcome.passed is True
    assert outcome.verifiable is False


@pytest.mark.asyncio
async def test_structured_verifier_rejects_non_json() -> None:
    outcome = await v.StructuredVerifier(require_json=True).verify("plain text", "c")
    assert outcome.passed is False
    assert outcome.mode == v.MODE_STRUCTURED


@pytest.mark.asyncio
async def test_structured_verifier_accepts_fenced_json_then_defers_to_judge() -> None:
    seen: list[str] = []

    async def judge(prompt: str) -> str:
        seen.append(prompt)
        return '{"passed": true, "reason": "valid"}'

    payload = "```json\n{\"a\": 1}\n```"
    outcome = await v.StructuredVerifier(require_json=True, judge=judge).verify(payload, "c")

    assert outcome.passed is True
    assert seen, "semantic judge should run once structure is valid"


@pytest.mark.asyncio
async def test_structured_verifier_without_enforcement_uses_judge_only() -> None:
    async def judge(_prompt: str) -> str:
        return '{"passed": false, "reason": "nope"}'

    outcome = await v.StructuredVerifier(require_json=False, judge=judge).verify("free text", "c")
    assert outcome.passed is False


def test_build_verifier_by_mode() -> None:
    assert isinstance(v.build_verifier("generic"), v.GenericVerifier)
    assert isinstance(v.build_verifier("structured"), v.StructuredVerifier)
    assert isinstance(v.build_verifier("vanilla"), v.GenericVerifier)  # alias
    assert v.build_verifier("none") is None


# ---------------------------------------------------------------------------
# Bounded verify -> revise -> re-verify loop
# ---------------------------------------------------------------------------


class _ScriptedVerifier:
    """Verifier returning a scripted sequence of outcomes."""

    def __init__(self, outcomes: list[v.VerificationOutcome]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[str] = []

    async def verify(self, output: str, criteria: str) -> v.VerificationOutcome:
        self.calls.append(output)
        return self._outcomes.pop(0) if self._outcomes else self._outcomes_default()

    @staticmethod
    def _outcomes_default() -> v.VerificationOutcome:
        return v.VerificationOutcome(passed=False, reason="exhausted", mode="generic")


def _fail(reason: str = "no") -> v.VerificationOutcome:
    return v.VerificationOutcome(passed=False, reason=reason, mode="generic")


def _pass(reason: str = "ok") -> v.VerificationOutcome:
    return v.VerificationOutcome(passed=True, reason=reason, mode="generic")


@pytest.mark.asyncio
async def test_loop_passes_first_try_no_revision() -> None:
    verifier = _ScriptedVerifier([_pass()])
    revise_calls: list[str] = []

    async def revise(output: str, criteria: str, reason: str) -> str:
        revise_calls.append(output)
        return output + "!"

    result = await v.run_verification_loop(
        output="orig", criteria="c", verifier=verifier, revise=revise, max_iterations=2
    )

    assert result.outcome.passed is True
    assert result.attempts == 0
    assert result.escalated is False
    assert revise_calls == []  # never revised on a first-try pass
    assert result.output == "orig"


@pytest.mark.asyncio
async def test_loop_recovers_after_one_revision() -> None:
    verifier = _ScriptedVerifier([_fail("missing"), _pass("fixed")])

    async def revise(output: str, criteria: str, reason: str) -> str:
        return "revised"

    result = await v.run_verification_loop(
        output="orig", criteria="c", verifier=verifier, revise=revise, max_iterations=3
    )

    assert result.outcome.passed is True
    assert result.attempts == 1
    assert result.escalated is False
    assert result.output == "revised"


@pytest.mark.asyncio
async def test_loop_escalates_after_exhausting_budget() -> None:
    verifier = _ScriptedVerifier([_fail("1"), _fail("2"), _fail("3")])
    counter = {"n": 0}

    async def revise(output: str, criteria: str, reason: str) -> str:
        counter["n"] += 1
        return f"revision-{counter['n']}"

    result = await v.run_verification_loop(
        output="orig", criteria="c", verifier=verifier, revise=revise, max_iterations=2
    )

    assert result.outcome.passed is False
    assert result.attempts == 2
    assert result.escalated is True


@pytest.mark.asyncio
async def test_loop_not_applicable_never_escalates() -> None:
    verifier = _ScriptedVerifier([v.VerificationOutcome.not_applicable("no criteria")])

    result = await v.run_verification_loop(
        output="orig", criteria="c", verifier=verifier, revise=None, max_iterations=2
    )

    assert result.escalated is False
    assert result.attempts == 0


@pytest.mark.asyncio
async def test_loop_without_reviser_escalates_immediately() -> None:
    verifier = _ScriptedVerifier([_fail()])

    result = await v.run_verification_loop(
        output="orig", criteria="c", verifier=verifier, revise=None, max_iterations=3
    )

    assert result.attempts == 0
    assert result.escalated is True


@pytest.mark.asyncio
async def test_loop_stops_when_revision_is_unchanged() -> None:
    verifier = _ScriptedVerifier([_fail(), _fail()])

    async def revise(output: str, criteria: str, reason: str) -> str:
        return output  # no change -> loop must stop

    result = await v.run_verification_loop(
        output="orig", criteria="c", verifier=verifier, revise=revise, max_iterations=5
    )

    assert result.attempts == 1
    assert result.escalated is True


# ---------------------------------------------------------------------------
# Provider gating
# ---------------------------------------------------------------------------


def test_provider_returns_none_when_disabled() -> None:
    ctx = SwarmBuildContext(role="teammate", language="cn")
    assert member_rails._build_team_verification_rail({"mode": "none"}, ctx) is None


def test_provider_builds_rail_when_enabled() -> None:
    ctx = SwarmBuildContext(role="teammate", language="en")
    rail = member_rails._build_team_verification_rail(
        {"mode": "generic", "max_iterations": 3, "apply_to_roles": ["teammate"]}, ctx
    )
    assert isinstance(rail, TeamVerificationRail)
    assert rail._role == "teammate"
    assert rail._max_iterations == 3
    assert rail._language == "en"


# ---------------------------------------------------------------------------
# config_specs wiring
# ---------------------------------------------------------------------------


def test_verification_enabled_and_params() -> None:
    assert _verification_enabled({"verification": {"mode": "generic"}}) is True
    assert _verification_enabled({"verification": {"mode": "none"}}) is False
    assert _verification_enabled({}) is False

    params = _verification_params(
        {"verification": {"mode": "structured", "max_iterations": 4, "output_enforcement": True}}
    )
    assert params["mode"] == "structured"
    assert params["max_iterations"] == 4
    assert params["output_enforcement"] is True
    assert params["apply_to_roles"] == ["teammate"]


@pytest.mark.parametrize("role", ["leader", "teammate"])
def test_capability_specs_append_verification_when_enabled(role: str) -> None:
    config = {"verification": {"mode": "generic"}}
    rails, _ = build_member_capability_specs(config, "team", role)
    names = {spec.type for spec in rails}
    assert registry.TEAM_VERIFICATION in names


def test_capability_specs_omit_verification_by_default() -> None:
    rails, _ = build_member_capability_specs({}, "team", "teammate")
    names = {spec.type for spec in rails}
    assert registry.TEAM_VERIFICATION not in names


@pytest.mark.parametrize("mode", ["code.team", "team.plan"])
def test_code_capability_specs_append_verification_when_enabled(mode: str) -> None:
    config = {"verification": {"mode": "structured", "output_enforcement": True}}
    rails, _ = build_member_capability_specs(config, mode, "teammate")
    names = {spec.type for spec in rails}
    assert registry.TEAM_VERIFICATION in names


# ---------------------------------------------------------------------------
# Rail after_invoke gate (end-to-end through a fake member LLM)
# ---------------------------------------------------------------------------


class _FakeLLM:
    """Fake member model: judges by whether the deliverable contains 'FIXED'."""

    def __init__(self) -> None:
        self.revise_count = 0

    async def invoke(self, *, messages):  # noqa: ANN001 - test double
        prompt = messages[-1]["content"]
        if "Revise it so it fully complies" in prompt:
            self.revise_count += 1
            return SimpleNamespace(content="FIXED deliverable")
        # Judge prompt.
        passed = "FIXED" in prompt
        verdict = "true" if passed else "false"
        return SimpleNamespace(content=f'{{"passed": {verdict}, "reason": "r"}}')


def _make_ctx(query: str, result: dict) -> AgentCallbackContext:
    return AgentCallbackContext(
        agent=None,
        inputs=InvokeInputs(query=query, result=result),
        session=None,
    )


def _make_rail(agent, **kwargs) -> TeamVerificationRail:
    rail = TeamVerificationRail(role="teammate", mode="generic", **kwargs)
    rail.init(agent)
    return rail


@pytest.mark.asyncio
async def test_rail_skips_when_no_criteria() -> None:
    agent = SimpleNamespace(_llm=_FakeLLM(), system_prompt_builder=None)
    rail = _make_rail(agent)
    result = {"output": "some answer"}
    await rail.after_invoke(_make_ctx("plain subtask, no criteria", result))

    assert result == {"output": "some answer"}  # untouched
    assert "verification" not in result


@pytest.mark.asyncio
async def test_rail_passes_and_annotates_report() -> None:
    agent = SimpleNamespace(_llm=_FakeLLM(), system_prompt_builder=None)
    rail = _make_rail(agent)
    result = {"output": "FIXED already good"}
    ctx = _make_ctx("task\n\nAcceptance Criteria:\n- be good", result)

    await rail.after_invoke(ctx)

    assert result["verification"]["passed"] is True
    assert result["verification"]["escalated"] is False
    assert result["output"] == "FIXED already good"


@pytest.mark.asyncio
async def test_rail_revises_then_passes() -> None:
    llm = _FakeLLM()
    agent = SimpleNamespace(_llm=llm, system_prompt_builder=None)
    rail = _make_rail(agent, max_iterations=2)
    result = {"output": "initial bad answer"}
    ctx = _make_ctx("task\n\nAcceptance Criteria:\n- must be fixed", result)

    await rail.after_invoke(ctx)

    assert llm.revise_count == 1
    assert result["output"] == "FIXED deliverable"
    assert result["verification"]["passed"] is True
    assert result["verification"]["attempts"] == 1
    assert result["verification"]["escalated"] is False


@pytest.mark.asyncio
async def test_rail_escalates_when_never_fixed() -> None:
    class _NeverFixLLM:
        async def invoke(self, *, messages):  # noqa: ANN001 - test double
            prompt = messages[-1]["content"]
            if "Revise it so it fully complies" in prompt:
                return SimpleNamespace(content="still bad answer")
            return SimpleNamespace(content='{"passed": false, "reason": "missing X"}')

    agent = SimpleNamespace(_llm=_NeverFixLLM(), system_prompt_builder=None)
    rail = _make_rail(agent, max_iterations=2)
    result = {"output": "bad answer"}
    ctx = _make_ctx("task\n\nAcceptance Criteria:\n- need X", result)

    await rail.after_invoke(ctx)

    assert result["verification"]["escalated"] is True
    assert result["verification"]["attempts"] == 2
    # An escalation note (with the failure reason) is appended to the deliverable.
    assert "\n---\n" in result["output"]
    assert "missing X" in result["output"]


@pytest.mark.asyncio
async def test_rail_leader_is_not_gated() -> None:
    agent = SimpleNamespace(_llm=_FakeLLM(), system_prompt_builder=None)
    rail = TeamVerificationRail(role="leader", mode="generic")
    rail.init(agent)
    result = {"output": "leader consolidation"}
    ctx = _make_ctx("task\n\nAcceptance Criteria:\n- x", result)

    await rail.after_invoke(ctx)

    assert "verification" not in result  # leader deliverables are not gated


@pytest.mark.asyncio
async def test_rail_leader_injects_planning_guidance() -> None:
    sections: list[object] = []
    builder = SimpleNamespace(add_section=sections.append, remove_section=lambda name: None)
    agent = SimpleNamespace(_llm=_FakeLLM(), system_prompt_builder=builder)
    rail = TeamVerificationRail(role="leader", mode="generic", language="en")
    rail.init(agent)

    await rail.before_model_call(_make_ctx("q", {}))

    assert len(sections) == 1
    section = sections[0]
    assert section.name == rail.SECTION_NAME
    assert "Acceptance Criteria" in section.content["en"]


@pytest.mark.asyncio
async def test_rail_teammate_does_not_inject_planning_guidance() -> None:
    sections: list[object] = []
    builder = SimpleNamespace(add_section=sections.append, remove_section=lambda name: None)
    agent = SimpleNamespace(_llm=_FakeLLM(), system_prompt_builder=builder)
    rail = _make_rail(agent)

    await rail.before_model_call(_make_ctx("q", {}))

    assert sections == []


# ---------------------------------------------------------------------------
# Swarmflow node-state carries verification fields
# ---------------------------------------------------------------------------


def test_workflow_state_records_verification_fields() -> None:
    run = WorkflowRunState()
    run.apply(WorkflowProgress(kind="workflow_started", run_id="r1", workflow_name="wf"))
    run.apply(
        WorkflowProgress(
            kind="agent_started",
            phase="p1",
            label="researcher",
            agent_id="researcher-1",
            verification_criteria="- must cite sources",
        )
    )
    run.apply(
        WorkflowProgress(
            kind="agent_completed",
            phase="p1",
            label="researcher",
            agent_id="researcher-1",
            outcome="done",
            verification_status="passed",
            verification_reason="all criteria met",
        )
    )

    agent = run.phases[0].agents[0]
    assert agent.verification_criteria == "- must cite sources"
    assert agent.verification_status == "passed"
    assert agent.verification_reason == "all criteria met"
    # Serialization surfaces the new fields.
    assert agent.to_dict()["verification_status"] == "passed"


def test_verification_update_on_already_terminal_agent_emits_delta() -> None:
    """A verification-only update to an already-completed agent must not be lost.

    Regression: when the agent is already terminal and no outcome/error needs
    backfilling, ``_finalize_agent`` used to return ``None`` even though it had
    mutated the verification fields, silently dropping the delta.
    """
    run = WorkflowRunState()
    run.apply(WorkflowProgress(kind="workflow_started", run_id="r1", workflow_name="wf"))
    run.apply(
        WorkflowProgress(
            kind="agent_started",
            phase="p1",
            label="researcher",
            agent_id="researcher-1",
        )
    )
    # First completion marks the agent terminal with an outcome (no verdict yet).
    run.apply(
        WorkflowProgress(
            kind="agent_completed",
            phase="p1",
            label="researcher",
            agent_id="researcher-1",
            outcome="done",
        )
    )
    agent = run.phases[0].agents[0]
    assert agent.status == "completed"
    assert agent.verification_status is None

    # A later verification-only update (same outcome, nothing to backfill).
    delta = run.apply(
        WorkflowProgress(
            kind="agent_completed",
            phase="p1",
            label="researcher",
            agent_id="researcher-1",
            outcome="done",
            verification_status="failed",
            verification_reason="missing citations",
        )
    )

    # The verdict is recorded AND a delta is emitted (not silently dropped).
    assert agent.verification_status == "failed"
    assert agent.verification_reason == "missing citations"
    assert delta is not None
    # Counters are unaffected by a verification-only update.
    assert run.phases[0].completed_agent_count == 1

    # A redundant repeat (no real change) should not emit a delta.
    repeat = run.apply(
        WorkflowProgress(
            kind="agent_completed",
            phase="p1",
            label="researcher",
            agent_id="researcher-1",
            outcome="done",
            verification_status="failed",
            verification_reason="missing citations",
        )
    )
    assert repeat is None
