from __future__ import annotations

from jiuwenswarm.research_evidence.claim_graph import ClaimEvidenceGraph
from jiuwenswarm.research_evidence.schemas import Claim, Evidence, EvidenceKind


def test_claim_verification_accepts_grounded_numeric_claim():
    evidence = [
        Evidence(
            "E1",
            EvidenceKind.EXPERIMENT,
            "Across five seeds, evidence recall was 92.5%.",
            "results.json",
            reliability=0.96,
            supports=["C1"],
        )
    ]
    claim = Claim(
        "C1",
        "Evidence recall was 92.5% across five seeds.",
        ["E1"],
        strength=0.9,
    )
    assert ClaimEvidenceGraph(evidence, [claim]).verify_all() == []


def test_claim_verification_blocks_missing_number_and_unresolved_conflict():
    evidence = [
        Evidence(
            "E1",
            EvidenceKind.EXPERIMENT,
            "Recall was 81%.",
            "run-a",
            supports=["C1"],
            conflict_ids=["E2"],
        ),
        Evidence(
            "E2",
            EvidenceKind.NEGATIVE_RESULT,
            "Recall degraded under distractors.",
            "run-b",
            contradicts=["C1"],
            conflict_ids=["E1"],
        ),
    ]
    claim = Claim("C1", "Recall was 95% in every condition.", ["E1", "E2"], strength=1.0)
    codes = {issue.code for issue in ClaimEvidenceGraph(evidence, [claim]).verify_all()}
    assert {"unsupported_number", "unresolved_contradiction"}.issubset(codes)
