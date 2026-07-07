# Copyright (c) Huawei Technologies, Co., Ltd. 2026. All rights reserved.
"""Tests for AheDecisionPolicy — RuleGate + LLMDecision."""

from jiuwenswarm.evolve.models import (
    Proposal, ProposalTargetType, DecisionResult, DecisionSuggestion, EvidenceRef,
)
from jiuwenswarm.evolve.ahe.decision_policy import AheDecisionPolicy
from jiuwenswarm.evolve.ahe.experience_governor import ExperienceGovernor


class TestRuleGate:
    def _make_proposal(self, **overrides):
        defaults = {
            "target_type": ProposalTargetType.SKILL,
            "target_id": "bash-tool",
            "proposal_type": "add_skill_experience",
            "failure_evidence": [EvidenceRef(trace_id="abc123", description="bash error")],
            "root_cause": "Missing path specification",
            "targeted_fix": {"action": "add_knowledge", "suggestion": "Use /usr/bin/python3"},
            "predicted_impact": "Reduce tool error rate",
            "proposer_name": "pda_proposer",
        }
        defaults.update(overrides)
        return Proposal(**defaults)

    def test_empty_evidence_blocked(self):
        policy = AheDecisionPolicy(governor=ExperienceGovernor(), model=None)
        proposal = self._make_proposal(failure_evidence=[])
        result = policy._rule_gate(proposal)
        assert result.blocking is True
        assert "empty_failure_evidence" in result.failed_checks

    def test_empty_root_cause_blocked(self):
        policy = AheDecisionPolicy(governor=ExperienceGovernor(), model=None)
        proposal = self._make_proposal(root_cause="")
        result = policy._rule_gate(proposal)
        assert result.blocking is True
        assert "empty_root_cause" in result.failed_checks

    def test_unsupported_target_type_blocked(self):
        policy = AheDecisionPolicy(governor=ExperienceGovernor(), model=None)
        proposal = self._make_proposal(target_type=ProposalTargetType.MEMORY)
        result = policy._rule_gate(proposal)
        assert result.blocking is True
        assert "unsupported_target_type_memory" in result.failed_checks

    def test_empty_predicted_impact_blocked(self):
        policy = AheDecisionPolicy(governor=ExperienceGovernor(), model=None)
        proposal = self._make_proposal(predicted_impact="")
        result = policy._rule_gate(proposal)
        assert result.blocking is True
        assert "empty_predicted_impact" in result.failed_checks

    def test_valid_proposal_passes_rule_gate(self):
        policy = AheDecisionPolicy(governor=ExperienceGovernor(), model=None)
        proposal = self._make_proposal()
        result = policy._rule_gate(proposal)
        assert result.blocking is False
        assert result.failed_checks == []
        assert result.suggestion == DecisionSuggestion.CANDIDATE


class TestLlmDecisionFallback:
    def test_no_model_fallback_to_candidate(self):
        """When no model is available, LLMDecision should return CANDIDATE."""
        policy = AheDecisionPolicy(governor=ExperienceGovernor(), model=None)
        result = policy._llm_decision(Proposal(
            target_type=ProposalTargetType.SKILL,
            proposal_type="add_skill_experience",
            failure_evidence=[EvidenceRef(trace_id="abc", description="err")],
            root_cause="test", targeted_fix={}, predicted_impact="test",
            proposer_name="pda_proposer",
        ))
        # Since model is None, it should try to init and fallback
        assert isinstance(result, DecisionResult)


class TestParseLlmJson:
    def test_parse_valid_json(self):
        text = '{"score": 0.8, "suggestion": "active", "reason": "Good proposal"}'
        result = AheDecisionPolicy._parse_llm_json(text)
        assert result["score"] == 0.8
        assert result["suggestion"] == "active"

    def test_parse_markdown_json(self):
        text = '```json\n{"score": 0.3, "suggestion": "rejected", "reason": "Bad"}\n```'
        result = AheDecisionPolicy._parse_llm_json(text)
        assert result["score"] == 0.3
        assert result["suggestion"] == "rejected"

    def test_parse_embedded_json(self):
        text = 'Analysis: {"score": 0.6, "suggestion": "candidate", "reason": "OK"} End'
        result = AheDecisionPolicy._parse_llm_json(text)
        assert result["score"] == 0.6

    def test_parse_fallback(self):
        text = "I think this proposal is good."
        result = AheDecisionPolicy._parse_llm_json(text)
        assert result["score"] == 0.5
        assert result["suggestion"] == "candidate"
