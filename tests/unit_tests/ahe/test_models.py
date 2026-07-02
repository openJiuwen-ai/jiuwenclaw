# Copyright (c) Huawei Technologies, Co., Ltd. 2026. All rights reserved.
"""Tests for PDA phase 1 data models — ExperienceOperation, TraceOutcome, GovernanceContext."""

import pytest
from pydantic import ValidationError

from jiuwenswarm.evolve.models import (
    EvidenceRef,
    ExperienceOperationType,
    ExperienceOperation,
    GovernanceContext,
    TraceOutcome,
)


class TestExperienceOperationType:
    def test_valid_values(self):
        assert ExperienceOperationType.ADD == "add"
        assert ExperienceOperationType.MERGE == "merge"
        assert ExperienceOperationType.REPLACE == "replace"
        assert ExperienceOperationType.NOOP == "noop"
        assert ExperienceOperationType.DEPRECATE == "deprecate"
        assert ExperienceOperationType.UPDATE == "update"

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            ExperienceOperationType("delete")


class TestExperienceOperation:
    def test_add_operation(self):
        op = ExperienceOperation(
            op=ExperienceOperationType.ADD,
            new_content="Use full path /usr/bin/python3",
            reason="Agent used wrong path",
            evidence_refs=[
                EvidenceRef(trace_id="abc123", description="bash: python not found"),
            ],
        )
        assert op.op == ExperienceOperationType.ADD
        assert op.target_experience_id is None
        assert op.new_content == "Use full path /usr/bin/python3"

    def test_merge_operation_requires_target(self):
        op = ExperienceOperation(
            op=ExperienceOperationType.MERGE,
            target_experience_id="exp-001",
            reason="Similar issue already has experience",
            evidence_refs=[
                EvidenceRef(trace_id="def456", description="same path error"),
            ],
        )
        assert op.target_experience_id == "exp-001"
        assert op.new_content is None  # MERGE does not require new_content

    def test_add_without_content_raises(self):
        """ADD/REPLACE/UPDATE must have new_content."""
        with pytest.raises(ValidationError):
            ExperienceOperation(
                op=ExperienceOperationType.ADD,
                reason="missing content",
                evidence_refs=[],
            )

    def test_replace_without_target_raises(self):
        """REPLACE requires both target_experience_id and new_content."""
        with pytest.raises(ValidationError):
            ExperienceOperation(
                op=ExperienceOperationType.REPLACE,
                new_content="better content",
                reason="missing target",
                evidence_refs=[],
            )

    def test_merge_without_target_raises(self):
        """MERGE/REPLACE/DEPRECATE/UPDATE must have target_experience_id."""
        with pytest.raises(ValidationError):
            ExperienceOperation(
                op=ExperienceOperationType.MERGE,
                reason="missing target",
                evidence_refs=[],
            )

    def test_noop_operation(self):
        op = ExperienceOperation(
            op=ExperienceOperationType.NOOP,
            reason="Existing experience covers this issue",
            evidence_refs=[],
        )
        assert op.op == ExperienceOperationType.NOOP
        assert op.new_content is None
        assert op.target_experience_id is None

    def test_deprecate_operation(self):
        op = ExperienceOperation(
            op=ExperienceOperationType.DEPRECATE,
            target_experience_id="exp-005",
            reason="No longer relevant after tool upgrade",
            evidence_refs=[],
        )
        assert op.target_experience_id == "exp-005"

    def test_replace_operation_full(self):
        op = ExperienceOperation(
            op=ExperienceOperationType.REPLACE,
            target_experience_id="exp-003",
            new_content="Updated: use /usr/bin/python3 instead of python",
            reason="exp-003 had incomplete path info",
            evidence_refs=[
                EvidenceRef(trace_id="ghi789", description="python3 not found"),
            ],
        )
        assert op.op == ExperienceOperationType.REPLACE
        assert op.target_experience_id == "exp-003"
        assert op.new_content is not None


class TestTraceOutcome:
    def test_pass_outcome(self):
        outcome = TraceOutcome(
            trace_id="abc123",
            outcome="pass",
            score=0.9,
            confidence=0.85,
            reason="User task completed",
        )
        assert outcome.outcome == "pass"
        assert outcome.score == 0.9

    def test_fail_outcome(self):
        outcome = TraceOutcome(
            trace_id="abc123",
            outcome="fail",
            score=0.1,
            confidence=0.9,
            reason="User task not completed",
            missing_requirements=["correct formula"],
        )
        assert outcome.outcome == "fail"
        assert outcome.missing_requirements == ["correct formula"]

    def test_uncertain_outcome(self):
        outcome = TraceOutcome(
            trace_id="abc123",
            outcome="uncertain",
            score=0.5,
            reason="Cannot reliably judge",
            needs_external_verification=True,
        )
        assert outcome.outcome == "uncertain"
        assert outcome.needs_external_verification is True

    def test_invalid_outcome_raises(self):
        with pytest.raises(ValidationError):
            TraceOutcome(trace_id="abc123", outcome="unknown", score=0.5)

    def test_default_fields(self):
        outcome = TraceOutcome(trace_id="abc123", outcome="uncertain", score=0.5)
        assert outcome.missing_requirements == []
        assert outcome.needs_external_verification is False
        assert outcome.task_name is None
        assert outcome.confidence == 0.0

    def test_score_bounds(self):
        with pytest.raises(ValidationError):
            TraceOutcome(trace_id="abc123", outcome="pass", score=1.5)
        with pytest.raises(ValidationError):
            TraceOutcome(trace_id="abc123", outcome="pass", score=-0.1)


class TestGovernanceContext:
    def test_basic_context_can_add(self):
        ctx = GovernanceContext(
            skill_name="bash-tool",
            current_count=8,
            max_count=10,
            can_add=True,
            existing_experiences=[],
            similar_experiences=[],
            replaceable_experiences=[],
            protected_experiences=[],
            allowed_operations=[ExperienceOperationType.ADD, ExperienceOperationType.NOOP],
        )
        assert ctx.can_add is True
        assert ExperienceOperationType.ADD in ctx.allowed_operations
        assert ctx.current_count == 8

    def test_full_context_disallows_add(self):
        ctx = GovernanceContext(
            skill_name="bash-tool",
            current_count=10,
            max_count=10,
            can_add=False,
            existing_experiences=[],
            similar_experiences=[],
            replaceable_experiences=[
                {"id": "exp-001", "state": "candidate", "hit_count": 0},
            ],
            protected_experiences=["exp-002"],
            allowed_operations=[ExperienceOperationType.REPLACE, ExperienceOperationType.NOOP],
        )
        assert ctx.can_add is False
        assert ExperienceOperationType.ADD not in ctx.allowed_operations
        assert len(ctx.replaceable_experiences) == 1
        assert "exp-002" in ctx.protected_experiences

    def test_mixed_allowed_operations(self):
        ctx = GovernanceContext(
            skill_name="bash-tool",
            current_count=5,
            max_count=10,
            can_add=True,
            existing_experiences=[],
            similar_experiences=[
                {"id": "exp-003", "summary": "path error"},
            ],
            replaceable_experiences=[],
            protected_experiences=[],
            allowed_operations=[
                ExperienceOperationType.ADD,
                ExperienceOperationType.MERGE,
                ExperienceOperationType.NOOP,
            ],
        )
        assert ExperienceOperationType.ADD in ctx.allowed_operations
        assert ExperienceOperationType.MERGE in ctx.allowed_operations
