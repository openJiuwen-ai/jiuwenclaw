from __future__ import annotations

from jiuwenswarm.research_evidence.schemas import Evidence, EvidenceKind
from jiuwenswarm.research_evidence.selector import EvidenceSelector, SelectorConfig


def _item(
    evidence_id: str,
    kind: EvidenceKind,
    content: str,
    *,
    supports: list[str] | None = None,
    contradicts: list[str] | None = None,
    conflicts: list[str] | None = None,
    reliability: float = 0.9,
) -> Evidence:
    return Evidence(
        evidence_id,
        kind,
        content,
        f"source:{evidence_id}",
        supports=supports or [],
        contradicts=contradicts or [],
        conflict_ids=conflicts or [],
        reliability=reliability,
    )


def test_selector_respects_budget_and_required_kind_coverage():
    evidence = [
        _item("L1", EvidenceKind.LITERATURE, "long horizon agent memory context", supports=["C1"]),
        _item("E1", EvidenceKind.EXPERIMENT, "evidence rail improves recall", supports=["C1"]),
        _item(
            "N1",
            EvidenceKind.NEGATIVE_RESULT,
            "evidence rail does not improve latency",
            contradicts=["C2"],
        ),
        _item("D1", EvidenceKind.NOTE, "unrelated cooking recipe and travel notes"),
    ]
    selector = EvidenceSelector(
        SelectorConfig(
            token_budget=40,
            required_kinds=(
                EvidenceKind.LITERATURE,
                EvidenceKind.EXPERIMENT,
                EvidenceKind.NEGATIVE_RESULT,
            ),
        )
    )
    result = selector.select(
        "long horizon evidence rail recall and latency",
        evidence,
        required_claims=["C1", "C2"],
    )

    assert result.used_tokens <= result.token_budget
    assert {item.kind for item in result.selected}.issuperset(
        {EvidenceKind.LITERATURE, EvidenceKind.EXPERIMENT, EvidenceKind.NEGATIVE_RESULT}
    )
    assert result.uncovered_claims == []
    assert "D1" not in {item.evidence_id for item in result.selected}


def test_selector_adds_counterevidence_when_linked_to_selected_item():
    evidence = [
        _item(
            "E-positive",
            EvidenceKind.EXPERIMENT,
            "retrieval increases task success",
            supports=["C1"],
            conflicts=["E-negative"],
        ),
        _item(
            "E-negative",
            EvidenceKind.NEGATIVE_RESULT,
            "retrieval fails under adversarial distractors",
            contradicts=["C1"],
            conflicts=["E-positive"],
        ),
        _item("D", EvidenceKind.NOTE, "retrieval task success repeated repeated repeated"),
    ]
    selector = EvidenceSelector(SelectorConfig(token_budget=40))
    result = selector.select("retrieval task success", evidence, required_claims=["C1"])
    selected_ids = {item.evidence_id for item in result.selected}
    assert {"E-positive", "E-negative"}.issubset(selected_ids)
    negative_score = next(score for score in result.scores if score.evidence_id == "E-negative")
    assert negative_score.conflict_coverage == 1.0
