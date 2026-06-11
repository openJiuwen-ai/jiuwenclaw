# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit and integration tests for the self-evolution framework."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Models tests (tasks 2.3)
# ---------------------------------------------------------------------------


class TestModels:
    """Test core data model validation and serialization."""

    def test_evidence_ref_creation(self):
        from jiuwenswarm.evolve.models import EvidenceRef

        ref = EvidenceRef(
            trace_id="trace-001",
            span_id="span-042",
            description="Tool error in bash execution",
        )
        assert ref.trace_id == "trace-001"
        assert ref.span_id == "span-042"
        assert ref.field_path is None
        data = ref.model_dump()
        assert data["trace_id"] == "trace-001"

    def test_proposal_default_values(self):
        from jiuwenswarm.evolve.models import (
            EvidenceRef,
            Proposal,
            ProposalState,
            ProposalTargetType,
        )

        prop = Proposal(
            target_type=ProposalTargetType.SKILL,
            proposal_type="add_skill_experience",
            failure_evidence=[
                EvidenceRef(
                    trace_id="t-1",
                    span_id="s-1",
                    description="error",
                )
            ],
            root_cause="Missing parameter",
            targeted_fix={"action": "add_validation"},
            predicted_impact="Fewer errors",
        )
        assert prop.proposal_id.startswith("prop-")
        assert prop.state == ProposalState.CANDIDATE
        assert prop.schema_version == "proposal.v1"
        assert isinstance(prop.metadata, dict)
        assert prop.risk is None

    def test_proposal_json_roundtrip(self):
        from jiuwenswarm.evolve.models import (
            EvidenceRef,
            Proposal,
            ProposalTargetType,
        )

        prop = Proposal(
            target_type=ProposalTargetType.MEMORY,
            proposal_type="add_memory_retrieval_hint",
            failure_evidence=[
                EvidenceRef(trace_id="t-1", description="bad query"),
            ],
            root_cause="Query too broad",
            targeted_fix={"hint": "narrow search"},
            predicted_impact="Better retrieval",
            risk="Low",
            proposer_name="test",
        )
        json_str = prop.model_dump_json()
        reloaded = Proposal.model_validate_json(json_str)
        assert reloaded.proposal_id == prop.proposal_id
        assert reloaded.target_type == ProposalTargetType.MEMORY
        assert len(reloaded.failure_evidence) == 1

    def test_decision_result_score_range(self):
        from jiuwenswarm.evolve.models import DecisionResult, DecisionSuggestion

        dr = DecisionResult(
            proposal_id="prop-1",
            policy_name="rule_policy",
            policy_version="1.0",
            score=0.75,
            reason="OK",
            suggestion=DecisionSuggestion.ACTIVE,
        )
        assert 0.0 <= dr.score <= 1.0
        assert dr.decision_id.startswith("dec-")

        # Score out of range should fail validation
        with pytest.raises(Exception):
            DecisionResult(
                proposal_id="p-1",
                policy_name="x",
                policy_version="1",
                score=1.5,
                reason="bad",
                suggestion=DecisionSuggestion.CANDIDATE,
            )

    def test_apply_record_status_enum(self):
        from jiuwenswarm.evolve.models import (
            ApplyRecord,
            ApplyStatus,
            ProposalTargetType,
            TargetStore,
        )

        ar = ApplyRecord(
            proposal_id="prop-1",
            target_type=ProposalTargetType.SKILL,
            target_store=TargetStore.SKILL_EXPERIENCE_STORE,
            status=ApplyStatus.APPLIED,
            stored_object_id="/path/to/exp.json",
            reason="Written successfully",
            applier_name="skill_writer",
        )
        assert ar.apply_id.startswith("apply-")
        assert ar.status == ApplyStatus.APPLIED

    def test_trace_batch_defaults(self):
        from jiuwenswarm.evolve.models import TraceBatch

        batch = TraceBatch(
            trace_ids=["t-1", "t-2"],
            source="manual",
        )
        assert batch.batch_id.startswith("batch-")
        assert batch.source == "manual"
        assert len(batch.trace_ids) == 2


# ---------------------------------------------------------------------------
# Registry tests (task 1.2 supplement)
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_register_and_get(self):
        from jiuwenswarm.evolve.registry import Registry

        reg: Registry = Registry()

        @reg.register("test_component")
        class TestComponent:
            pass

        assert "test_component" in reg
        assert reg.get("test_component") is TestComponent
        assert "test_component" in reg.list()

    def test_duplicate_raises(self):
        from jiuwenswarm.evolve.registry import Registry

        reg: Registry = Registry()

        @reg.register("dup")
        class A:
            pass

        with pytest.raises(ValueError):

            @reg.register("dup")
            class B:
                pass

    def test_unknown_raises_keyerror(self):
        from jiuwenswarm.evolve.registry import Registry

        reg: Registry = Registry()
        with pytest.raises(KeyError):
            reg.get("nonexistent")

    def test_builtin_registries_populated(self):
        from jiuwenswarm.evolve.registry import (
            apply_writers,
            decision_policies,
            proposal_generators,
            trace_samplers,
        )
        # Force import so @register decorators fire
        import jiuwenswarm.evolve.proposal_generators.rule_proposer  # noqa: F401
        import jiuwenswarm.evolve.proposal_generators.llm_proposer  # noqa: F401
        import jiuwenswarm.evolve.decision_policies.rule_policy  # noqa: F401
        import jiuwenswarm.evolve.decision_policies.eval_policy  # noqa: F401
        import jiuwenswarm.evolve.apply_writers.skill_writer  # noqa: F401
        import jiuwenswarm.evolve.apply_writers.memory_writer  # noqa: F401
        import jiuwenswarm.evolve.apply_writers.training_writer  # noqa: F401
        import jiuwenswarm.evolve.trigger.sampler  # noqa: F401

        assert "rule_proposer" in proposal_generators
        assert "llm_proposer" in proposal_generators
        assert "rule_policy" in decision_policies
        assert "eval_policy" in decision_policies
        assert "skill_writer" in apply_writers
        assert "memory_writer" in apply_writers
        assert "training_writer" in apply_writers
        assert "latest_n" in trace_samplers
        assert "time_window" in trace_samplers


# ---------------------------------------------------------------------------
# Storage tests (tasks 3.5)
# ---------------------------------------------------------------------------


class TestSqliteStore:
    @pytest.fixture
    def store(self):
        import jiuwenswarm.evolve.storage.sqlite_store as mod

        tmp = tempfile.mktemp(suffix=".db")
        s = mod.SqliteStore(db_path=tmp)
        yield s
        # Close connection to avoid Windows file locking
        if s._conn is not None:
            s._conn.close()
        if os.path.exists(tmp):
            os.unlink(tmp)

    def test_tables_created(self, store):
        conn = store._get_conn()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {r["name"] for r in tables}
        for expected in (
            "proposals",
            "decision_results",
            "apply_records",
            "trace_batches",
            "training_candidates",
        ):
            assert expected in table_names, f"Missing table: {expected}"

    def test_save_and_query_proposal(self, store):
        from jiuwenswarm.evolve.models import (
            EvidenceRef,
            Proposal,
            ProposalTargetType,
        )

        prop = Proposal(
            target_type=ProposalTargetType.SKILL,
            proposal_type="add_skill_experience",
            failure_evidence=[
                EvidenceRef(trace_id="t-001", description="error")
            ],
            root_cause="Bad param",
            targeted_fix={"action": "fix"},
            predicted_impact="Better",
            proposer_name="test",
            metadata={"batch_id": "batch-1"},
        )
        store.save_proposal(prop)

        row = store.get_proposal(prop.proposal_id)
        assert row is not None
        assert row["proposal_id"] == prop.proposal_id
        assert row["root_cause"] == "Bad param"

    def test_save_training_candidate_idempotent(self, store):
        store.save_training_candidate("t-x", "prop-1", "batch-1")
        # Second insert should be a no-op
        store.save_training_candidate("t-x", "prop-2", "batch-2")

        candidates = store.get_training_candidates()
        t_x_entries = [c for c in candidates if c["trace_id"] == "t-x"]
        assert len(t_x_entries) == 1
        assert t_x_entries[0]["status"] == "pending"

    def test_query_by_trace_id(self, store):
        from jiuwenswarm.evolve.models import (
            EvidenceRef,
            Proposal,
            ProposalTargetType,
        )

        prop = Proposal(
            target_type=ProposalTargetType.SKILL,
            proposal_type="test",
            failure_evidence=[
                EvidenceRef(trace_id="t-chain-1", description="chain test")
            ],
            root_cause="x",
            targeted_fix={"a": "b"},
            predicted_impact="y",
            proposer_name="test",
            metadata={"batch_id": "b-1"},
        )
        store.save_proposal(prop)

        result = store.query_by_trace_id("t-chain-1")
        assert len(result["proposals"]) >= 1

    def test_list_batches(self, store):
        from jiuwenswarm.evolve.models import TraceBatch

        batch = TraceBatch(
            trace_ids=["t-1", "t-2"],
            source="manual",
        )
        store.save_trace_batch(batch)

        batches = store.list_batches()
        assert len(batches) >= 1
        assert any(b["batch_id"] == batch.batch_id for b in batches)


class TestFileStore:
    @pytest.fixture
    def file_store(self):
        import jiuwenswarm.evolve.storage.file_store as mod

        tmp = tempfile.mkdtemp()
        fs = mod.FileStore(root_dir=tmp)
        yield fs
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)

    def test_save_trace_batch_creates_files(self, file_store):
        from jiuwenswarm.evolve.models import TraceBatch

        batch = TraceBatch(trace_ids=["t-1"], source="test")
        file_store.save_trace_batch(batch)

        batch_dir = file_store.get_batch_dir(batch.batch_id)
        assert (batch_dir / "batch.json").exists()

    def test_index_updated(self, file_store):
        from jiuwenswarm.evolve.models import TraceBatch

        batch = TraceBatch(trace_ids=["t-a"], source="test")
        file_store.save_trace_batch(batch)

        assert batch.batch_id in file_store._index
        assert file_store._index[batch.batch_id]["trace_count"] == 1


# ---------------------------------------------------------------------------
# Proposal generator tests (tasks 4.5)
# ---------------------------------------------------------------------------


class TestRuleProposer:
    @pytest.fixture
    def trace_reader(self):
        """Mock trace reader returning controlled span data."""

        class MockReader:
            def read_spans(self, trace_id):
                if "error" in trace_id:
                    return [
                        {
                            "trace_id": trace_id,
                            "span_id": "span-1",
                            "name": "tool_exec",
                            "status_code": "ERROR",
                            "events": "tool execution failed",
                        }
                    ]
                elif "missing" in trace_id:
                    return [
                        {
                            "trace_id": trace_id,
                            "span_id": "span-2",
                            "name": "tool_call",
                            "status_code": "OK",
                            "events": "missing required parameter 'url'",
                        }
                    ]
                else:
                    return [
                        {
                            "trace_id": trace_id,
                            "span_id": "span-3",
                            "name": "success",
                            "status_code": "OK",
                            "events": "",
                        }
                    ]

            def get_recent_trace_ids(self, limit=20):
                return ["error-trace", "ok-trace"]

            def get_trace_ids_since(self, since, limit=100):
                return []

            def get_trace_ids_by_benchmark(self, benchmark_run_id, limit=100):
                return []

        return MockReader()

    @pytest.mark.asyncio
    async def test_detects_tool_error(self, trace_reader):
        import jiuwenswarm.evolve.proposal_generators.rule_proposer  # noqa: F401
        from jiuwenswarm.evolve.proposal_generators.rule_proposer import (
            RuleProposer,
        )
        from jiuwenswarm.evolve.models import TraceBatch

        proposer = RuleProposer(trace_reader=trace_reader)
        batch = TraceBatch(trace_ids=["error-trace"], source="test")

        proposals = await proposer.generate(batch)
        assert len(proposals) >= 1
        assert proposals[0].target_type.value == "skill"
        assert "failure" in proposals[0].root_cause.lower()

    @pytest.mark.asyncio
    async def test_no_proposal_for_successful_trace(self, trace_reader):
        import jiuwenswarm.evolve.proposal_generators.rule_proposer  # noqa: F401
        from jiuwenswarm.evolve.proposal_generators.rule_proposer import (
            RuleProposer,
        )
        from jiuwenswarm.evolve.models import TraceBatch

        proposer = RuleProposer(trace_reader=trace_reader)
        batch = TraceBatch(trace_ids=["ok-trace"], source="test")

        proposals = await proposer.generate(batch)
        assert len(proposals) == 0

    @pytest.mark.asyncio
    async def test_detects_missing_param(self, trace_reader):
        import jiuwenswarm.evolve.proposal_generators.rule_proposer  # noqa: F401
        from jiuwenswarm.evolve.proposal_generators.rule_proposer import (
            RuleProposer,
        )
        from jiuwenswarm.evolve.models import TraceBatch

        proposer = RuleProposer(trace_reader=trace_reader)
        batch = TraceBatch(trace_ids=["missing-trace"], source="test")

        proposals = await proposer.generate(batch)
        assert len(proposals) >= 1


# ---------------------------------------------------------------------------
# Decision policy tests (tasks 5.5)
# ---------------------------------------------------------------------------


class TestRulePolicy:
    @pytest.mark.asyncio
    async def test_valid_proposal_passes(self):
        import jiuwenswarm.evolve.decision_policies.rule_policy  # noqa: F401
        from jiuwenswarm.evolve.decision_policies.rule_policy import RulePolicy
        from jiuwenswarm.evolve.models import (
            EvidenceRef,
            Proposal,
            ProposalTargetType,
        )

        policy = RulePolicy()
        prop = Proposal(
            target_type=ProposalTargetType.SKILL,
            proposal_type="test",
            failure_evidence=[
                EvidenceRef(trace_id="t-1", description="error")
            ],
            root_cause="Something went wrong with the tool execution",
            targeted_fix={"action": "add_retry"},
            predicted_impact="Fewer failures",
            risk="Low",
        )
        result = await policy.evaluate(prop)
        assert result.blocking is False
        assert result.score >= 0.5

    @pytest.mark.asyncio
    async def test_empty_root_cause_blocked(self):
        import jiuwenswarm.evolve.decision_policies.rule_policy  # noqa: F401
        from jiuwenswarm.evolve.decision_policies.rule_policy import RulePolicy
        from jiuwenswarm.evolve.models import (
            EvidenceRef,
            Proposal,
            ProposalTargetType,
        )

        policy = RulePolicy()
        prop = Proposal(
            target_type=ProposalTargetType.SKILL,
            proposal_type="test",
            failure_evidence=[
                EvidenceRef(trace_id="t-1", description="error")
            ],
            root_cause="",  # Empty!
            targeted_fix={"action": "fix"},
            predicted_impact="Better",
        )
        result = await policy.evaluate(prop)
        assert result.blocking is True
        assert "empty_root_cause" in result.failed_checks
        assert result.score == 0.0


class TestEvalPolicy:
    @pytest.mark.asyncio
    async def test_strong_proposal_scores_high(self):
        import jiuwenswarm.evolve.decision_policies.eval_policy  # noqa: F401
        from jiuwenswarm.evolve.decision_policies.eval_policy import EvalPolicy
        from jiuwenswarm.evolve.models import (
            EvidenceRef,
            Proposal,
            ProposalTargetType,
        )

        policy = EvalPolicy()
        prop = Proposal(
            target_type=ProposalTargetType.SKILL,
            proposal_type="add_skill_experience",
            failure_evidence=[
                EvidenceRef(trace_id="t-1", description="e1"),
                EvidenceRef(trace_id="t-2", description="e2"),
                EvidenceRef(trace_id="t-3", description="e3"),
            ],
            root_cause="The tool failed because it did not validate the input "
            "URL parameter before making the HTTP request, causing a "
            "ConnectionError that propagated to the caller.",
            targeted_fix={"action": "add_url_validation"},
            predicted_impact="Eliminate ConnectionErrors",
            risk="Low — only adds input validation",
        )
        result = await policy.evaluate(prop)
        assert result.score >= 0.60
        assert result.suggestion.value in ("active", "candidate")

    @pytest.mark.asyncio
    async def test_weak_proposal_scores_low(self):
        import jiuwenswarm.evolve.decision_policies.eval_policy  # noqa: F401
        from jiuwenswarm.evolve.decision_policies.eval_policy import EvalPolicy
        from jiuwenswarm.evolve.models import (
            EvidenceRef,
            Proposal,
            ProposalTargetType,
        )

        policy = EvalPolicy()
        prop = Proposal(
            target_type=ProposalTargetType.SKILL,
            proposal_type="test",
            failure_evidence=[],  # No evidence
            root_cause="maybe something",  # Vague
            targeted_fix={},  # No action
            predicted_impact="improve",
        )
        result = await policy.evaluate(prop)
        assert result.score < 0.50


# ---------------------------------------------------------------------------
# Apply writer tests (tasks 6.6)
# ---------------------------------------------------------------------------


class TestSkillWriter:
    @pytest.mark.asyncio
    async def test_applies_active_skill_proposal(self):
        import jiuwenswarm.evolve.apply_writers.skill_writer  # noqa: F401
        from jiuwenswarm.evolve.apply_writers.skill_writer import (
            SkillExperienceWriter,
        )
        from jiuwenswarm.evolve.models import (
            EvidenceRef,
            Proposal,
            ProposalState,
            ProposalTargetType,
        )

        with tempfile.TemporaryDirectory() as tmp:
            writer = SkillExperienceWriter(skills_dir=tmp)
            prop = Proposal(
                target_type=ProposalTargetType.SKILL,
                proposal_type="add_skill_experience",
                failure_evidence=[
                    EvidenceRef(trace_id="t-1", description="err")
                ],
                root_cause="Bad param",
                targeted_fix={"action": "validate"},
                predicted_impact="Better",
                state=ProposalState.ACTIVE,
            )
            record = await writer.apply(prop)
            assert record.status.value == "applied"
            assert record.stored_object_id is not None
            assert Path(record.stored_object_id).exists()

    @pytest.mark.asyncio
    async def test_skips_rejected_proposal(self):
        import jiuwenswarm.evolve.apply_writers.skill_writer  # noqa: F401
        from jiuwenswarm.evolve.apply_writers.skill_writer import (
            SkillExperienceWriter,
        )
        from jiuwenswarm.evolve.models import (
            EvidenceRef,
            Proposal,
            ProposalState,
            ProposalTargetType,
        )

        with tempfile.TemporaryDirectory() as tmp:
            writer = SkillExperienceWriter(skills_dir=tmp)
            prop = Proposal(
                target_type=ProposalTargetType.SKILL,
                proposal_type="test",
                failure_evidence=[
                    EvidenceRef(trace_id="t-1", description="err")
                ],
                root_cause="x",
                targeted_fix={"a": "b"},
                predicted_impact="y",
                state=ProposalState.REJECTED,
            )
            record = await writer.apply(prop)
            assert record.status.value == "skipped"


# ---------------------------------------------------------------------------
# Pipeline integration tests (tasks 7.6)
# ---------------------------------------------------------------------------


class TestPipeline:
    @pytest.fixture
    def mock_store(self):
        import jiuwenswarm.evolve.storage.sqlite_store as mod

        tmp = tempfile.mktemp(suffix=".db")

        class MockStore:
            def __init__(self, db_path):
                self._sqlite = mod.SqliteStore(db_path=db_path)
                self._proposals = []
                self._decisions = []
                self._apply_records = []

            def save_trace_batch(self, batch):
                self._sqlite.save_trace_batch(batch)

            def save_proposal(self, p):
                self._proposals.append(p)

            def save_proposals(self, ps):
                for p in ps:
                    self.save_proposal(p)

            def save_decision_result(self, dr):
                self._decisions.append(dr)

            def save_decision_results(self, drs):
                for d in drs:
                    self.save_decision_result(d)

            def save_apply_record(self, ar):
                self._apply_records.append(ar)

            def save_apply_records(self, ars):
                for a in ars:
                    self.save_apply_record(a)

            def save_training_candidate(self, trace_id, proposal_id, batch_id):
                self._sqlite.save_training_candidate(
                    trace_id, proposal_id, batch_id
                )

        s = MockStore(tmp)
        yield s
        if s._sqlite._conn is not None:
            s._sqlite._conn.close()
        if os.path.exists(tmp):
            os.unlink(tmp)

    @pytest.fixture
    def mock_generator(self):
        from jiuwenswarm.evolve.models import (
            EvidenceRef,
            Proposal,
            ProposalTargetType,
        )

        class MockGen:
            def __init__(self):
                self.name = "mock_gen"

            async def generate(self, batch):
                return [
                    Proposal(
                        target_type=ProposalTargetType.SKILL,
                        proposal_type="add_skill_experience",
                        failure_evidence=[
                            EvidenceRef(
                                trace_id=batch.trace_ids[0]
                                if batch.trace_ids
                                else "t-0",
                                description="mock",
                            )
                        ],
                        root_cause="Mock issue",
                        targeted_fix={"action": "mock_fix"},
                        predicted_impact="Mock improvement",
                        risk="Low",
                        proposer_name="mock_gen",
                        metadata={"batch_id": batch.batch_id},
                    )
                ]

        return MockGen()

    @pytest.fixture
    def mock_policy(self):
        from jiuwenswarm.evolve.models import DecisionResult, DecisionSuggestion

        class MockPolicy:
            def __init__(self):
                self.name = "mock_policy"
                self.version = "1.0"

            async def evaluate(self, proposal):
                return DecisionResult(
                    proposal_id=proposal.proposal_id,
                    policy_name=self.name,
                    policy_version=self.version,
                    score=0.8,
                    reason="Mock approval",
                    suggestion=DecisionSuggestion.ACTIVE,
                    blocking=False,
                )

        return MockPolicy()

    @pytest.mark.asyncio
    async def test_pipeline_run_produces_results(
        self, mock_store, mock_generator, mock_policy
    ):
        from jiuwenswarm.evolve.pipeline import EvolutionPipeline
        from jiuwenswarm.evolve.models import TraceBatch

        pipeline = EvolutionPipeline(
            generators=[mock_generator],
            policies=[mock_policy],
            writers=[],  # No writers for this test
            store=mock_store,
        )
        batch = TraceBatch(trace_ids=["t-pipe-1"], source="test")
        result = await pipeline.run(batch)

        assert result.batch_id == batch.batch_id
        assert len(result.proposals) >= 1
        assert len(result.decision_results) >= 1
        assert result.active_count >= 1

    @pytest.mark.asyncio
    async def test_pipeline_feeds_training_candidates(
        self, mock_store, mock_generator, mock_policy
    ):
        from jiuwenswarm.evolve.pipeline import EvolutionPipeline
        from jiuwenswarm.evolve.models import TraceBatch

        pipeline = EvolutionPipeline(
            generators=[mock_generator],
            policies=[mock_policy],
            writers=[],
            store=mock_store,
        )
        batch = TraceBatch(trace_ids=["t-tc-1"], source="test")
        await pipeline.run(batch)

        candidates = mock_store._sqlite.get_training_candidates()
        assert any(c["trace_id"] == "t-tc-1" for c in candidates)
        pending = [c for c in candidates if c["trace_id"] == "t-tc-1"]
        assert pending[0]["status"] == "pending"


# ---------------------------------------------------------------------------
# Sampler tests (tasks 8.4)
# ---------------------------------------------------------------------------


class TestLatestNSampler:
    def test_samples_up_to_max(self):
        import jiuwenswarm.evolve.trigger.sampler  # noqa: F401
        from jiuwenswarm.evolve.trigger.sampler import LatestNSampler

        class MockReader:
            def get_recent_trace_ids(self, limit=20):
                return [f"t-{i}" for i in range(min(limit, 15))]

        reader = MockReader()
        sampler = LatestNSampler(trace_reader=reader, max_traces=10)
        batch = sampler.sample()
        assert len(batch.trace_ids) <= 10
        assert batch.source == "periodic"


# ---------------------------------------------------------------------------
# End-to-end tests (tasks 12.1–12.5)
# ---------------------------------------------------------------------------


class TestEndToEnd:
    """End-to-end tests covering the full audit chain."""

    @pytest.mark.asyncio
    async def test_full_pipeline_with_skill_writer(self):
        """12.1: E2E: traces.db → RuleProposer → RulePolicy → SkillWriter."""
        import jiuwenswarm.evolve.proposal_generators.rule_proposer  # noqa: F401
        import jiuwenswarm.evolve.decision_policies.rule_policy  # noqa: F401
        import jiuwenswarm.evolve.decision_policies.eval_policy  # noqa: F401
        import jiuwenswarm.evolve.apply_writers.skill_writer  # noqa: F401
        from jiuwenswarm.evolve.pipeline import EvolutionPipeline
        from jiuwenswarm.evolve.proposal_generators.rule_proposer import (
            RuleProposer,
        )
        from jiuwenswarm.evolve.decision_policies.rule_policy import RulePolicy
        from jiuwenswarm.evolve.decision_policies.eval_policy import EvalPolicy
        from jiuwenswarm.evolve.apply_writers.skill_writer import (
            SkillExperienceWriter,
        )
        from jiuwenswarm.evolve.models import TraceBatch
        from jiuwenswarm.evolve.storage.sqlite_store import SqliteStore

        # Setup
        tmp_db = tempfile.mktemp(suffix=".db")
        store = SqliteStore(db_path=tmp_db)

        class MockTraceReader:
            def read_spans(self, trace_id):
                return [
                    {
                        "trace_id": trace_id,
                        "span_id": "span-e2e",
                        "name": "tool_run",
                        "status_code": "ERROR",
                        "events": "execution failed with timeout",
                    }
                ]

            def get_recent_trace_ids(self, limit=20):
                return []

            def get_trace_ids_since(self, since, limit=100):
                return []

            def get_trace_ids_by_benchmark(self, benchmark_run_id, limit=100):
                return []

        reader = MockTraceReader()
        proposer = RuleProposer(trace_reader=reader)
        rule_policy = RulePolicy()
        eval_policy = EvalPolicy()

        with tempfile.TemporaryDirectory() as skills_tmp:
            writer = SkillExperienceWriter(skills_dir=skills_tmp)

            # We need a store facade
            class E2EStore:
                def __init__(self, sqlite, file_root=None):
                    self._sqlite = sqlite

                def save_trace_batch(self, batch):
                    self._sqlite.save_trace_batch(batch)

                def save_proposal(self, p):
                    self._sqlite.save_proposal(p)

                def save_proposals(self, ps):
                    for p in ps:
                        self.save_proposal(p)

                def save_decision_result(self, dr):
                    self._sqlite.save_decision_result(dr)

                def save_decision_results(self, drs):
                    for d in drs:
                        self.save_decision_result(d)

                def save_apply_record(self, ar):
                    self._sqlite.save_apply_record(ar)

                def save_apply_records(self, ars):
                    for a in ars:
                        self.save_apply_record(a)

                def save_training_candidate(
                    self, trace_id, proposal_id, batch_id
                ):
                    self._sqlite.save_training_candidate(
                        trace_id, proposal_id, batch_id
                    )

            e2e_store = E2EStore(store)

            pipeline = EvolutionPipeline(
                generators=[proposer],
                policies=[rule_policy, eval_policy],
                writers=[writer],
                store=e2e_store,
            )

            batch = TraceBatch(trace_ids=["e2e-trace-1"], source="test")
            result = await pipeline.run(batch)

            # Assertions
            assert len(result.proposals) >= 1
            proposal = result.proposals[0]
            assert proposal.proposal_id.startswith("prop-")
            assert proposal.state.value == "active"

            # 12.2: Audit chain
            chain = store.query_by_trace_id("e2e-trace-1")
            assert len(chain["proposals"]) >= 1

            # 12.3: Training candidates fed
            candidates = store.get_training_candidates()
            assert any(c["trace_id"] == "e2e-trace-1" for c in candidates)

            # An apply record should exist
            assert result.applied_count >= 1

        # Cleanup: close connections first (Windows file locking)
        if store._conn is not None:
            store._conn.close()
        if os.path.exists(tmp_db):
            os.unlink(tmp_db)

    def test_evolve_disabled_by_default(self):
        """12.4: evolve.enabled=false means no scheduler."""
        from jiuwenswarm.evolve import get_evolve_config

        evolve_cfg = get_evolve_config()
        # Default is false
        assert evolve_cfg.get("enabled", False) is False

    @pytest.mark.asyncio
    async def test_dual_storage_both_backends_written(self):
        """12.5: SQLite + file system both receive records."""
        from jiuwenswarm.evolve.storage.base import EvolutionStore
        from jiuwenswarm.evolve.storage.sqlite_store import SqliteStore
        from jiuwenswarm.evolve.storage.file_store import FileStore
        from jiuwenswarm.evolve.models import TraceBatch

        with tempfile.TemporaryDirectory() as file_root:
            db_path = tempfile.mktemp(suffix=".db")
            try:
                sqlite = SqliteStore(db_path=db_path)
                file_store = FileStore(root_dir=file_root)
                store = EvolutionStore(
                    sqlite_backend=sqlite, file_backend=file_store
                )

                batch = TraceBatch(trace_ids=["t-dual"], source="test")
                store.save_trace_batch(batch)

                # Check SQLite
                batches = sqlite.list_batches()
                assert any(b["batch_id"] == batch.batch_id for b in batches)

                # Check file system
                batch_dir = file_store.get_batch_dir(batch.batch_id)
                assert (batch_dir / "batch.json").exists()
            finally:
                # Close connections before cleanup (Windows file locking)
                if sqlite._conn is not None:
                    sqlite._conn.close()
                if os.path.exists(db_path):
                    os.unlink(db_path)
