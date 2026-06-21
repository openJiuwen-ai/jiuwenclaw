# Copyright (c) Huawei Technologies, Co., Ltd. 2026. All rights reserved.
"""Tests for ExperienceGovernor — governance context and validation."""

import json
import tempfile
from pathlib import Path

import pytest

from jiuwenswarm.evolve.models import (
    ExperienceOperationType,
    ExperienceOperation,
    GovernanceContext,
)
from jiuwenswarm.evolve.pda.experience_governor import ExperienceGovernor


def _create_test_skills_dir(entries_count: int = 0) -> str:
    """Create a temporary skills dir with evolutions.json."""
    tmpdir = tempfile.mkdtemp()
    skill_dir = Path(tmpdir) / "bash-tool"
    skill_dir.mkdir(parents=True, exist_ok=True)

    entries = []
    for i in range(entries_count):
        entries.append({
            "id": f"exp-{i+1:03d}",
            "change": {
                "section": "Troubleshooting",
                "content": f"Experience #{i+1}: use full path for python",
                "action": "append",
            },
            "metadata": {
                "state": "candidate" if i < 3 else "active",
                "hit_count": 0 if i < 3 else i - 2,
                "proposal_id": f"prop-{i+1}",
            },
        })

    evo_data = {
        "skill_id": "bash-tool",
        "version": "1.0.0",
        "entries": entries,
    }
    evo_path = skill_dir / "evolutions.json"
    evo_path.write_text(json.dumps(evo_data, indent=2, ensure_ascii=False))

    return tmpdir


class TestGovernorGetContext:
    def test_empty_skill_returns_can_add(self):
        skills_dir = _create_test_skills_dir(entries_count=0)
        governor = ExperienceGovernor(skills_dir=skills_dir, max_per_skill=10)
        ctx = governor.get_context("bash-tool")
        assert ctx.can_add is True
        assert ctx.current_count == 0
        assert ExperienceOperationType.ADD in ctx.allowed_operations
        assert ExperienceOperationType.NOOP in ctx.allowed_operations

    def test_partial_skill_returns_can_add(self):
        skills_dir = _create_test_skills_dir(entries_count=5)
        governor = ExperienceGovernor(skills_dir=skills_dir, max_per_skill=10)
        ctx = governor.get_context("bash-tool")
        assert ctx.can_add is True
        assert ctx.current_count == 5
        assert ExperienceOperationType.ADD in ctx.allowed_operations

    def test_full_skill_disallows_add(self):
        skills_dir = _create_test_skills_dir(entries_count=10)
        governor = ExperienceGovernor(skills_dir=skills_dir, max_per_skill=10)
        ctx = governor.get_context("bash-tool")
        assert ctx.can_add is False
        assert ExperienceOperationType.ADD not in ctx.allowed_operations
        assert ExperienceOperationType.REPLACE in ctx.allowed_operations
        assert ExperienceOperationType.NOOP in ctx.allowed_operations

    def test_classifies_replaceable_experiences(self):
        """Candidate experiences with hit_count=0 are replaceable."""
        skills_dir = _create_test_skills_dir(entries_count=5)
        governor = ExperienceGovernor(skills_dir=skills_dir, max_per_skill=10)
        ctx = governor.get_context("bash-tool")
        # First 3 entries have state=candidate, hit_count=0
        assert len(ctx.replaceable_experiences) == 3

    def test_classifies_protected_experiences(self):
        """Active experiences with hit_count>0 are protected."""
        skills_dir = _create_test_skills_dir(entries_count=5)
        governor = ExperienceGovernor(skills_dir=skills_dir, max_per_skill=10)
        ctx = governor.get_context("bash-tool")
        # Entries 3-5 have state=active, hit_count > 0
        assert len(ctx.protected_experiences) > 0

    def test_similar_experience_detection(self):
        """When query_hint matches existing content, MERGE is allowed."""
        skills_dir = _create_test_skills_dir(entries_count=5)
        governor = ExperienceGovernor(skills_dir=skills_dir, max_per_skill=10)
        ctx = governor.get_context("bash-tool", query_hint="python path error")
        # Content contains "python" → similar
        assert len(ctx.similar_experiences) > 0
        assert ExperienceOperationType.MERGE in ctx.allowed_operations


class TestGovernorValidateOperation:
    def test_add_approved_when_can_add(self):
        skills_dir = _create_test_skills_dir(entries_count=5)
        governor = ExperienceGovernor(skills_dir=skills_dir, max_per_skill=10)
        op = ExperienceOperation(
            op=ExperienceOperationType.ADD,
            new_content="test content",
            reason="new experience",
            evidence_refs=[],
        )
        assert governor.validate_operation("bash-tool", op) is True

    def test_add_rejected_when_full(self):
        skills_dir = _create_test_skills_dir(entries_count=10)
        governor = ExperienceGovernor(skills_dir=skills_dir, max_per_skill=10)
        op = ExperienceOperation(
            op=ExperienceOperationType.ADD,
            new_content="test content",
            reason="should be rejected",
            evidence_refs=[],
        )
        assert governor.validate_operation("bash-tool", op) is False

    def test_replace_approved_for_replaceable_target(self):
        skills_dir = _create_test_skills_dir(entries_count=10)
        governor = ExperienceGovernor(skills_dir=skills_dir, max_per_skill=10)
        ctx = governor.get_context("bash-tool")
        # Use a replaceable experience ID
        replaceable_id = ctx.replaceable_experiences[0]["id"]
        op = ExperienceOperation(
            op=ExperienceOperationType.REPLACE,
            target_experience_id=replaceable_id,
            new_content="better content",
            reason="replace low-value experience",
            evidence_refs=[],
        )
        assert governor.validate_operation("bash-tool", op) is True

    def test_replace_rejected_for_non_replaceable_target(self):
        skills_dir = _create_test_skills_dir(entries_count=10)
        governor = ExperienceGovernor(skills_dir=skills_dir, max_per_skill=10)
        op = ExperienceOperation(
            op=ExperienceOperationType.REPLACE,
            target_experience_id="exp-005",  # active with hit_count > 0
            new_content="better content",
            reason="should be rejected",
            evidence_refs=[],
        )
        assert governor.validate_operation("bash-tool", op) is False

    def test_noop_always_approved(self):
        skills_dir = _create_test_skills_dir(entries_count=0)
        governor = ExperienceGovernor(skills_dir=skills_dir, max_per_skill=10)
        op = ExperienceOperation(
            op=ExperienceOperationType.NOOP,
            reason="existing experience covers this",
            evidence_refs=[],
        )
        assert governor.validate_operation("bash-tool", op) is True

    def test_deprecate_rejected_for_protected_experience(self):
        skills_dir = _create_test_skills_dir(entries_count=5)
        governor = ExperienceGovernor(skills_dir=skills_dir, max_per_skill=10)
        ctx = governor.get_context("bash-tool")
        protected_id = ctx.protected_experiences[0]
        op = ExperienceOperation(
            op=ExperienceOperationType.DEPRECATE,
            target_experience_id=protected_id,
            reason="should not deprecate protected experience",
            evidence_refs=[],
        )
        assert governor.validate_operation("bash-tool", op) is False

    def test_nonexistent_skill_returns_empty_context(self):
        skills_dir = _create_test_skills_dir(entries_count=0)
        governor = ExperienceGovernor(skills_dir=skills_dir, max_per_skill=10)
        ctx = governor.get_context("nonexistent-skill")
        assert ctx.current_count == 0
        assert ctx.can_add is True
