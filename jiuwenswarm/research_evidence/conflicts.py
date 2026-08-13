# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Conflict detection for explicit and structured research evidence."""

from __future__ import annotations

from dataclasses import dataclass

from jiuwenswarm.research_evidence.schemas import Evidence


@dataclass(slots=True, frozen=True)
class EvidenceConflict:
    """A symmetric conflict between two evidence items."""

    left_id: str
    right_id: str
    reason: str


def detect_conflicts(items: list[Evidence]) -> list[EvidenceConflict]:
    """Detect explicit links and opposing structured outcomes.

    An evidence producer can set ``metadata.fact_key`` and
    ``metadata.polarity`` (``positive``/``negative``) before natural-language
    interpretation.  This avoids brittle sentiment guessing while keeping the
    detector useful for literature disagreements and experimental reversals.
    """

    by_id = {item.evidence_id: item for item in items}
    found: dict[tuple[str, str], EvidenceConflict] = {}

    for item in items:
        for other_id in item.conflict_ids:
            if other_id not in by_id or other_id == item.evidence_id:
                continue
            key = tuple(sorted((item.evidence_id, other_id)))
            found[key] = EvidenceConflict(*key, reason="explicit_conflict_link")

    by_fact: dict[str, list[Evidence]] = {}
    for item in items:
        fact_key = str(item.metadata.get("fact_key") or "").strip()
        if fact_key:
            by_fact.setdefault(fact_key, []).append(item)
    for fact_key, group in by_fact.items():
        for index, left in enumerate(group):
            left_polarity = _polarity(left)
            if not left_polarity:
                continue
            first_remaining = index + 1
            for right in group[first_remaining:]:
                right_polarity = _polarity(right)
                if right_polarity and right_polarity != left_polarity:
                    key = tuple(sorted((left.evidence_id, right.evidence_id)))
                    found[key] = EvidenceConflict(
                        *key, reason=f"opposing_polarity:{fact_key}"
                    )
    return [found[key] for key in sorted(found)]


def conflict_map(items: list[Evidence]) -> dict[str, set[str]]:
    """Return a symmetric evidence-id adjacency map."""

    result: dict[str, set[str]] = {item.evidence_id: set() for item in items}
    for conflict in detect_conflicts(items):
        result.setdefault(conflict.left_id, set()).add(conflict.right_id)
        result.setdefault(conflict.right_id, set()).add(conflict.left_id)
    return result


def _polarity(item: Evidence) -> str:
    raw = str(item.metadata.get("polarity") or "").strip().lower()
    aliases = {
        "positive": "positive",
        "+": "positive",
        "supports": "positive",
        "negative": "negative",
        "-": "negative",
        "refutes": "negative",
    }
    return aliases.get(raw, "")
