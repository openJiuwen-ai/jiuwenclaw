# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the correlation-aware self-evaluation gate.

The gate decides how much an agent's own "pass" is worth. Every assertion here
is exact -- the rule is arithmetic, and a gate that needed a model to decide
whether it fired would inherit the very bias it exists to price in.
"""

import pytest

from jiuwenswarm.agents.harness.team.rails.self_eval_gate import evaluate


class TestRegimeDetection:
    def test_identical_models_are_self_graded(self):
        d = evaluate(verdict="accept", generator_model="gpt-5.5", judge_model="gpt-5.5")
        assert d.self_graded is True and d.regime == "self_graded"

    def test_same_family_only_partly_decorrelates(self):
        d = evaluate(verdict="accept", generator_model="gpt-5.5", judge_model="gpt-4.9")
        assert d.regime == "cross_model"
        assert d.effective_rho == pytest.approx(0.7 * 0.7)

    def test_different_family_decorrelates_further_but_not_fully(self):
        d = evaluate(verdict="accept", generator_model="gpt-5.5", judge_model="claude-opus-5")
        assert d.effective_rho == pytest.approx(0.7 * 0.5)
        assert d.effective_rho > 0, "shared familiarity bias means residual correlation remains"

    def test_unknown_provenance_assumes_the_worst_case(self):
        # The convenient assumption would be independence. That is exactly the
        # assumption an unaudited pipeline makes by accident.
        d = evaluate(verdict="accept")
        assert d.self_graded is True and d.regime == "self_graded"

    def test_same_lineage_flag_overrides_differing_model_strings(self):
        d = evaluate(verdict="accept", generator_model="a", judge_model="b", same_lineage=True)
        assert d.self_graded is True


class TestAnchorRequirement:
    def test_self_graded_with_no_anchor_abstains(self):
        d = evaluate(verdict="accept", generator_model="m", judge_model="m")
        assert d.action == "abstain"
        assert d.required_external > 0

    def test_confidence_never_substitutes_for_an_anchor(self):
        """A contaminated judge is confident for the same reason it is wrong."""
        low = evaluate(verdict="accept", generator_model="m", judge_model="m",
                       judge_confidence=0.01)
        high = evaluate(verdict="accept", generator_model="m", judge_model="m",
                        judge_confidence=0.99)
        assert low.required_external == high.required_external
        assert low.action == high.action == "abstain"

    def test_anchors_reduce_the_requirement_monotonically(self):
        needs = [evaluate(verdict="accept", generator_model="m", judge_model="m",
                          external_anchors=k).required_external for k in range(8)]
        assert needs == sorted(needs, reverse=True)
        assert needs[-1] == 0

    def test_enough_anchors_reach_accept(self):
        d = evaluate(verdict="accept", generator_model="m", judge_model="m",
                     external_anchors=6)
        assert d.action == "accept" and d.residual_risk <= d.alpha

    def test_higher_contamination_demands_more_anchors(self):
        lo = evaluate(verdict="accept", generator_model="m", judge_model="m",
                      contamination_rho=0.3)
        hi = evaluate(verdict="accept", generator_model="m", judge_model="m",
                      contamination_rho=0.9)
        assert hi.required_external > lo.required_external

    def test_tighter_budget_demands_more_anchors(self):
        loose = evaluate(verdict="accept", generator_model="m", judge_model="m", alpha=0.2)
        tight = evaluate(verdict="accept", generator_model="m", judge_model="m", alpha=0.01)
        assert tight.required_external > loose.required_external


class TestAsymmetry:
    @pytest.mark.parametrize("verdict", ["reject", "fail", "not novel", "no"])
    def test_negative_verdicts_pass_straight_through(self, verdict):
        # Contamination inflates a model's acceptance of its own favoured
        # outputs; it does not manufacture rejections.
        d = evaluate(verdict=verdict, generator_model="m", judge_model="m")
        assert d.action == "accept_verdict"
        assert d.required_external == 0

    def test_positive_verdict_does_not_pass_through(self):
        d = evaluate(verdict="accept", generator_model="m", judge_model="m")
        assert d.action != "accept_verdict"


class TestContract:
    def test_gate_makes_no_model_call(self):
        import inspect

        from jiuwenswarm.agents.harness.team.rails import self_eval_gate

        src = inspect.getsource(self_eval_gate)
        for forbidden in ("chat(", "completion(", "invoke_model", "acompletion", "llm"):
            assert forbidden not in src, f"gate must stay deterministic: {forbidden}"

    def test_decision_is_immutable(self):
        d = evaluate(verdict="accept")
        with pytest.raises(Exception):
            d.action = "accept"

    def test_render_names_the_action_and_the_numbers(self):
        text = evaluate(verdict="accept", generator_model="m", judge_model="m").render()
        assert "abstain" in text and "alpha" in text

    def test_evaluator_attaches_the_gate_to_every_graded_trace(self):
        """Wired, not dangling: the trace evaluator must record the discount."""
        from jiuwenswarm.symphony.experience.evaluator import TraceEvaluator
        from jiuwenswarm.symphony.experience.models import TraceRecord

        rec = TraceRecord(trace_id="t1", query="q", skills=["s"], result="r")
        ev = TraceEvaluator(llm_model="gpt-5.5")
        ev._parse_judge_response('{"success": true}', rec)

        assert rec.success is True
        assert rec.verdict_gate is not None
        assert rec.verdict_gate["regime"] == "self_graded"
        assert rec.verdict_gate["required_external"] > 0
        assert "verdict_gate" in rec.to_dict()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
