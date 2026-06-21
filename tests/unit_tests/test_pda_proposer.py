# Copyright (c) Huawei Technologies, Co., Ltd. 2026. All rights reserved.
"""Tests for AheProposer — limit enforcement and proposal parsing."""

import pytest
from jiuwenswarm.evolve.models import (
    Proposal, ProposalTargetType, ProposalState,
    TraceBatch,
)
from jiuwenswarm.evolve.ahe.proposer import AheProposer


class TestAheProposerEnforceLimits:
    def test_activates_top_skills(self):
        proposer = AheProposer.__new__(AheProposer)
        proposer._max_proposals = 3
        proposer._max_skill_proposals = 2

        proposals = [
            Proposal(
                target_type=ProposalTargetType.SKILL,
                proposal_type="add_skill_experience",
                failure_evidence=[], root_cause="r1", targeted_fix={},
                predicted_impact="p1", state=ProposalState.CANDIDATE,
                proposal_id="p1", proposer_name="pda_proposer",
                metadata={"max_score": 0.9},
            ),
            Proposal(
                target_type=ProposalTargetType.SKILL,
                proposal_type="add_skill_experience",
                failure_evidence=[], root_cause="r2", targeted_fix={},
                predicted_impact="p2", state=ProposalState.CANDIDATE,
                proposal_id="p2", proposer_name="pda_proposer",
                metadata={"max_score": 0.8},
            ),
            Proposal(
                target_type=ProposalTargetType.SKILL,
                proposal_type="add_skill_experience",
                failure_evidence=[], root_cause="r3", targeted_fix={},
                predicted_impact="p3", state=ProposalState.CANDIDATE,
                proposal_id="p3", proposer_name="pda_proposer",
                metadata={"max_score": 0.7},
            ),
        ]
        result = proposer._enforce_limits(proposals)
        # Only top 2 should be active
        assert len(result) == 2

    def test_all_pass_returns_empty_from_generate(self):
        """When MockEvaluator returns all 'pass', AheProposer returns []."""
        # This exercises the flow: generate() -> no fail traces -> empty
        proposer = AheProposer(trace_reader=None, store=None)
        # Without real store/traces, generate() should return []
        import asyncio
        result = asyncio.run(proposer.generate(TraceBatch(trace_ids=[])))
        assert result == []


class TestAheProposerParseProposals:
    def test_parse_valid_proposals(self):
        proposer = AheProposer.__new__(AheProposer)
        raw = [
            {
                "target_id": "bash-tool",
                "target_type": "skill",
                "proposal_type": "add_skill_experience",
                "failure_evidence": [
                    {"trace_id": "abc123", "description": "bash command failed"}
                ],
                "root_cause": "Missing path",
                "targeted_fix": {"action": "add_knowledge", "suggestion": "Use full path"},
                "predicted_impact": "Reduce errors",
                "risk": None,
                "operations": [
                    {"op": "add", "new_content": "Use full path",
                     "reason": "Agent used wrong path", "evidence_refs": []}
                ],
            }
        ]
        proposals = proposer._parse_proposals(raw, "batch-001")
        assert len(proposals) == 1
        assert proposals[0].proposer_name == "pda_proposer"
        assert proposals[0].target_id == "bash-tool"
        assert "operations" in proposals[0].metadata

    def test_parse_invalid_proposal_skipped(self):
        proposer = AheProposer.__new__(AheProposer)
        raw = [{"invalid": "no required fields"}]
        proposals = proposer._parse_proposals(raw, "batch-001")
        assert len(proposals) == 0


class TestAheProposerBuildSummaries:
    def test_build_trace_summaries(self):
        proposer = AheProposer.__new__(AheProposer)
        from jiuwenswarm.evolve.models import TraceOutcome
        failed = [
            (
                {"trace_id": "abc123", "input": {"message": "help me"},
                 "output": {"content": "ok"}},
                TraceOutcome(trace_id="abc123", outcome="fail", score=0.1,
                             reason="task incomplete"),
            )
        ]
        summaries = proposer._build_trace_summaries(failed)
        assert len(summaries) == 1
        assert summaries[0]["trace_id"] == "abc123"
        assert summaries[0]["outcome"] == "fail"

    def test_build_diagnosis_summary(self):
        from jiuwenswarm.evolve.diagnosis.models import DiagnosisResult, DiagnosisIssue
        diag = DiagnosisResult(
            mode="diagnose",
            issues=[
                DiagnosisIssue(
                    issue_type="工具错误", summary="bash error",
                    evidence="span #7", trace_id="abc123", span_index=7,
                    root_cause="Missing path", suggested_fix="Add path",
                )
            ],
            response="Found 1 issue",
            iterations=5,
        )
        summary = AheProposer._build_diagnosis_summary(diag)
        assert "bash error" in summary
        assert "Missing path" in summary

    def test_build_governance_summary(self):
        from jiuwenswarm.evolve.models import GovernanceContext, ExperienceOperationType
        ctx = GovernanceContext(
            skill_name="bash-tool",
            current_count=5,
            max_count=10,
            can_add=True,
            replaceable_experiences=[{"id": "exp-001", "state": "candidate"}],
            allowed_operations=[ExperienceOperationType.ADD, ExperienceOperationType.NOOP],
        )
        summary = AheProposer._build_governance_summary({"bash-tool": ctx})
        assert "bash-tool" in summary
        assert "5/10" in summary
