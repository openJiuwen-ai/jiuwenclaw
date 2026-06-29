"""Test skill_writer and ExperienceGovernor format compatibility.

Verifies that:
1. skill_writer writes in EXISTING format (change + usage_stats)
2. ExperienceGovernor reads from EXISTING format (usage_stats.times_used)
3. REPLACE/DEPRECATE operations modify correct fields (change.content)
"""

import json
import tempfile
from pathlib import Path
from datetime import datetime, timezone

from jiuwenswarm.evolve.apply_writers.skill_writer import SkillExperienceWriter
from jiuwenswarm.evolve.ahe.experience_governor import ExperienceGovernor
from jiuwenswarm.evolve.models import (
    Proposal,
    ProposalState,
    ProposalTargetType,
    TargetStore,
    EvidenceRef,
    ExperienceOperation,
    ExperienceOperationType,
)


def test_skill_writer_build_record_format():
    """Test that _build_record creates EXISTING format with change + usage_stats."""
    # Create a test proposal
    proposal = Proposal(
        proposal_id="test-12345678",
        state=ProposalState.ACTIVE,
        target_type=ProposalTargetType.SKILL,
        target_id="test-skill",
        root_cause="Test root cause",
        predicted_impact="Test impact",
        targeted_fix={"suggestion": "Test suggestion"},
        failure_evidence=[
            EvidenceRef(trace_id="trace-001", description="Test evidence")
        ],
        metadata={"max_score": 0.65},
    )

    # Build record
    writer = SkillExperienceWriter()
    record = writer._build_record(proposal)

    # Verify EXISTING format structure
    assert "change" in record, "Record must have 'change' field (EXISTING format)"
    assert "usage_stats" in record, "Record must have 'usage_stats' field (EXISTING format)"
    assert "applied" in record, "Record must have 'applied' field (EXISTING format)"

    # Verify change structure
    change = record["change"]
    assert change["section"] == "Troubleshooting"
    assert change["action"] == "append"
    assert change["content"] == "Test suggestion"
    assert change["target"] == "body"

    # Verify usage_stats structure
    usage_stats = record["usage_stats"]
    assert usage_stats["times_presented"] == 0
    assert usage_stats["times_used"] == 0
    assert usage_stats["times_positive"] == 0
    assert usage_stats["times_negative"] == 0

    # Verify no flat fields (old format should not exist)
    assert "content" not in record, "Flat 'content' field should not exist"
    assert "section" not in record, "Flat 'section' field should not exist"
    assert "action" not in record, "Flat 'action' field should not exist"
    assert "target" not in record, "Flat 'target' field should not exist"

    print("✅ _build_record creates correct EXISTING format")


def test_skill_writer_apply_add_format():
    """Test that _apply_add creates EXISTING format."""
    # Create test proposal and operation
    proposal = Proposal(
        proposal_id="test-add-001",
        state=ProposalState.ACTIVE,
        target_type=ProposalTargetType.SKILL,
        target_id="test-skill",
        root_cause="Test root cause",
        predicted_impact="Test impact",
        targeted_fix={"suggestion": "Add test content"},
        failure_evidence=[],
        metadata={"max_score": 0.7},
    )

    operation = ExperienceOperation(
        op=ExperienceOperationType.ADD,
        new_content="New experience content",
        reason="Test ADD operation",
        evidence_refs=[],
    )

    # Apply ADD operation
    writer = SkillExperienceWriter()
    evolution_log = {
        "skill_id": "test-skill",
        "version": "1.0.0",
        "entries": [],
    }
    record = writer._apply_add(evolution_log, proposal, operation)

    # Verify EXISTING format
    assert "change" in record, "ADD must create 'change' field (EXISTING format)"
    assert "usage_stats" in record, "ADD must create 'usage_stats' field (EXISTING format)"
    assert "applied" in record, "ADD must create 'applied' field"

    # Verify content in change.content (not flat content)
    assert record["change"]["content"] == "New experience content"
    assert "content" not in record, "Flat 'content' should not exist"

    print("✅ _apply_add creates correct EXISTING format")


def test_skill_writer_replace_operation():
    """Test that REPLACE modifies change.content (not flat content)."""
    # Create existing entry in EXISTING format
    evolution_log = {
        "skill_id": "test-skill",
        "version": "1.0.0",
        "entries": [
            {
                "id": "ev_test_replace",
                "source": "test",
                "change": {
                    "section": "Troubleshooting",
                    "action": "append",
                    "content": "Old content",
                    "target": "body",
                },
                "usage_stats": {
                    "times_used": 0,
                },
                "score": 0.5,
                "applied": False,
            }
        ],
    }

    # Create REPLACE operation
    proposal = Proposal(
        proposal_id="test-replace-001",
        state=ProposalState.ACTIVE,
        target_type=ProposalTargetType.SKILL,
        target_id="test-skill",
        root_cause="",
        predicted_impact="",
        targeted_fix={},
        failure_evidence=[],
        metadata={},
    )

    operation = ExperienceOperation(
        op=ExperienceOperationType.REPLACE,
        target_experience_id="ev_test_replace",
        new_content="New replaced content",
    )

    # Apply REPLACE
    writer = SkillExperienceWriter()
    writer._apply_replace(evolution_log, proposal, operation)

    # Verify change.content was modified
    entry = evolution_log["entries"][0]
    assert entry["change"]["content"] == "New replaced content", \
        "REPLACE must modify change.content (EXISTING format)"

    # Verify no flat content field created
    assert "content" not in entry, "REPLACE should not create flat 'content' field"

    print("✅ REPLACE modifies change.content correctly")


def test_experience_governor_read_existing_format():
    """Test that ExperienceGovernor reads from usage_stats (EXISTING format)."""
    # Create test entries in EXISTING format
    entries = [
        {
            "id": "ev_001",
            "change": {
                "content": "High quality experience",
            },
            "usage_stats": {
                "times_used": 10,
            },
            "score": 0.8,
            "applied": True,
        },
        {
            "id": "ev_002",
            "change": {
                "content": "Unused experience",
            },
            "usage_stats": {
                "times_used": 0,
            },
            "score": 0.5,
            "applied": False,
        },
        {
            "id": "ev_003",
            "change": {
                "content": "Low score experience",
            },
            "usage_stats": {
                "times_used": 5,
            },
            "score": 0.3,
            "applied": True,
        },
    ]

    # Test _find_replaceable
    replaceable = ExperienceGovernor._find_replaceable(entries)

    # ev_002 should be replaceable (times_used=0, score<0.6, applied=false)
    # ev_003 should be replaceable (score<0.6)
    assert len(replaceable) == 2, f"Expected 2 replaceable, got {len(replaceable)}"
    replaceable_ids = {r["id"] for r in replaceable}
    assert "ev_002" in replaceable_ids, "Unused experience should be replaceable"
    assert "ev_003" in replaceable_ids, "Low-score experience should be replaceable"

    print(f"✅ _find_replaceable found {len(replaceable)} entries using usage_stats.times_used")

    # Test _find_protected
    protected = ExperienceGovernor._find_protected(entries)

    # ev_001 should be protected (times_used>0, score>=0.7, applied=true)
    assert len(protected) == 1, f"Expected 1 protected, got {len(protected)}"
    assert protected[0] == "ev_001", "High-quality used experience should be protected"

    print(f"✅ _find_protected found {len(protected)} entries using usage_stats.times_used")


def test_full_integration():
    """Integration test: skill_writer writes → ExperienceGovernor reads."""
    # Create temp directory
    with tempfile.TemporaryDirectory() as tmpdir:
        skills_dir = Path(tmpdir) / "skills"
        skill_dir = skills_dir / "test-skill"
        skill_dir.mkdir(parents=True)

        # Create initial evolutions.json
        evolution_path = skill_dir / "evolutions.json"
        evolution_log = {
            "skill_id": "test-skill",
            "version": "1.0.0",
            "entries": [],
        }
        evolution_path.write_text(json.dumps(evolution_log, indent=2))

        # Write using skill_writer
        proposal = Proposal(
            proposal_id="integration-001",
            state=ProposalState.ACTIVE,
            target_type=ProposalTargetType.SKILL,
            target_id="test-skill",
            root_cause="Test integration",
            predicted_impact="Test",
            targeted_fix={"suggestion": "Integration test content"},
            failure_evidence=[],
            metadata={"max_score": 0.7},
        )

        writer = SkillExperienceWriter(skills_dir=str(skills_dir))
        record = writer._build_record(proposal)
        evolution_log["entries"].append(record)
        evolution_path.write_text(json.dumps(evolution_log, indent=2))

        # Read using ExperienceGovernor
        governor = ExperienceGovernor(skills_dir=str(skills_dir))
        context = governor.get_context("test-skill")

        # Verify context has entries
        assert context.current_count == 1, "Should have 1 entry"
        assert len(context.existing_experiences) == 1, "Should see 1 existing experience"

        # Verify classification
        # Entry should be replaceable (times_used=0, score=0.7, applied=false)
        # Note: score=0.7 >= 0.6 threshold, but times_used=0, so should be replaceable
        assert len(context.replaceable_experiences) == 1, \
            "New entry (times_used=0) should be replaceable"

        # Entry should NOT be protected (times_used=0, not >0)
        assert len(context.protected_experiences) == 0, \
            "New entry should not be protected"

        print("✅ Integration test: skill_writer writes → ExperienceGovernor reads correctly")


def main():
    """Run all tests."""
    print("=" * 60)
    print("Testing skill_writer and ExperienceGovernor format compatibility")
    print("=" * 60)

    try:
        test_skill_writer_build_record_format()
        test_skill_writer_apply_add_format()
        test_skill_writer_replace_operation()
        test_experience_governor_read_existing_format()
        test_full_integration()

        print("\n" + "=" * 60)
        print("✅ All tests passed!")
        print("=" * 60)

    except AssertionError as e:
        print("\n" + "=" * 60)
        print(f"❌ Test failed: {e}")
        print("=" * 60)
        raise


if __name__ == "__main__":
    main()