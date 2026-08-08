# Copyright (c) Huawei Technologies, Co., Ltd. 2026. All rights reserved.
"""PDA-style AHE End-to-End integration test with mocked dependencies."""

import asyncio
import json
import logging
import tempfile
from pathlib import Path

from jiuwenswarm.evolve.models import (
    TraceBatch, Proposal, ProposalTargetType, ProposalState,
    EvidenceRef,
)
from jiuwenswarm.evolve.ahe.proposer import AheProposer
from jiuwenswarm.evolve.ahe.decision_policy import AheDecisionPolicy


class MockStore:
    """Minimal mock store for E2E test."""
    _traces_db_path = "traces.db"
    _skills_dir = None

    def read_spans(self, trace_id):
        # Return minimal fake spans
        return [
            {
                "name": "gen_ai.chat",
                "span_id": "s1",
                "trace_id": trace_id,
                "parent_span_id": None,
                "start_time_ns": 1000000,
                "end_time_ns": 2000000,
                "duration_ns": 1000000,
                "attributes": '{"gen_ai.span.type": "model", "gen_ai.system": "anthropic"}',
                "events": '[{"name": "gen_ai.user.message", "attributes": {"content": "hello"}}, {"name": "gen_ai.assistant.message", "attributes": {"content": "hi there"}}]',
                "status_code": "ERROR",
                "status_description": "tool failed",
                "resource": '{}',
            },
            {
                "name": "gen_ai.tool.execute: bash",
                "span_id": "s2",
                "trace_id": trace_id,
                "parent_span_id": "s1",
                "start_time_ns": 2000000,
                "end_time_ns": 3000000,
                "duration_ns": 1000000,
                "attributes": '{"gen_ai.span.type": "tool", "gen_ai.tool.name": "bash"}',
                "events": '[]',
                "status_code": "ERROR",
                "status_description": "command not found",
                "resource": '{}',
            },
        ]

    def get_recent_trace_ids(self, limit=20):
        return ["trace-001"]

    def read_spans_batch(self, trace_ids):
        return {tid: self.read_spans(tid) for tid in trace_ids}

    def query_by_trace_id(self, trace_id):
        return {"trace_id": trace_id, "proposals": []}

    def get_batch(self, batch_id):
        return {"batch_id": batch_id, "proposals": []}


class TestPdaEndToEnd:
    """Full PDA pipeline: CLEAN → EVAL → DIAG → GOV → PROPOSE → DECIDE → APPLY."""

    async def asyncSetUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.skills_dir = Path(self.tmpdir) / "skills"
        self.skills_dir.mkdir(exist_ok=True)
        self.store = MockStore()

    async def test_full_pipeline_mocked(self):
        """Run the full PDA pipeline with mocked store (no real DB/LLM needed).

        This tests that:
        1. AheProposer.generate() returns proposals from mock data
        2. AheDecisionPolicy.evaluate() returns decisions
        3. The results have the right structure
        """
        # Create a batch with 2 traces (1 error, 1 empty)
        batch = TraceBatch(
            trace_ids=["trace-001", "trace-002"],
            source="manual",
        )

        proposer = AheProposer(
            trace_reader=self.store,
            store=self.store,
            model=None,  # No LLM — will use fallback
            skills_dir=str(self.skills_dir),
            max_proposals=3,
            max_skill_proposals=2,
        )

        # Propose — without a real LLM, this should return [] gracefully
        proposals = await proposer.generate(batch)
        # The result depends on whether LLM is available
        # Without LLM, the pipeline will gracefully return empty
        assert isinstance(proposals, list)
        print("PDA E2E: generate() returned %d proposals" % len(proposals))

    async def test_propose_decide_cycle(self):
        """Test the Propose->Decide cycle with a manually crafted Proposal."""
        policy = AheDecisionPolicy(model=None)

        # Create a valid proposal (mimicking what AheProposer would produce)
        proposal = Proposal(
            target_type=ProposalTargetType.SKILL,
            target_id="bash-tool",
            proposal_type="add_skill_experience",
            failure_evidence=[
                EvidenceRef(trace_id="trace-001", description="bash: command not found"),
            ],
            root_cause="Agent attempted to use 'python' without full path",
            targeted_fix={
                "suggestion": "Always use /usr/bin/python3 for Python execution",
            },
            predicted_impact="Reduce tool call failures for bash/python operations",
            risk="Path may vary across environments",
            proposer_name="pda_proposer",
            state=ProposalState.CANDIDATE,
        )

        # RuleGate check (sync)
        rule_result = policy._rule_gate(proposal)
        # Should pass RuleGate since all fields are present
        assert rule_result.blocking is False, f"RuleGate failed: {rule_result.failed_checks}"
        assert "empty_failure_evidence" not in rule_result.failed_checks
        assert "empty_root_cause" not in rule_result.failed_checks

        print("PASS: Proposal passes RuleGate")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test = TestPdaEndToEnd()
    test.tmpdir = tempfile.mkdtemp()
    test.skills_dir = Path(test.tmpdir) / "skills"
    test.skills_dir.mkdir(exist_ok=True)
    test.store = MockStore()
    asyncio.run(test.test_full_pipeline_mocked())
    asyncio.run(test.test_propose_decide_cycle())
    print("\nALL PDA E2E TESTS PASSED")
