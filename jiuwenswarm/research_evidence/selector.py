# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Budget-aware, evidence-diverse context selection."""

from __future__ import annotations

from dataclasses import dataclass, field

from jiuwenswarm.research_evidence.conflicts import conflict_map
from jiuwenswarm.research_evidence.schemas import (
    Evidence,
    EvidenceKind,
    SelectionResult,
    SelectionScore,
)
from jiuwenswarm.research_evidence.text import (
    cosine_similarity,
    estimate_tokens,
    idf_overlap,
)


@dataclass(slots=True)
class SelectorConfig:
    """Weights and hard constraints for :class:`EvidenceSelector`."""

    token_budget: int = 2048
    min_reliability: float = 0.0
    relevance_weight: float = 0.38
    reliability_weight: float = 0.18
    novelty_weight: float = 0.16
    claim_coverage_weight: float = 0.18
    conflict_coverage_weight: float = 0.10
    risk_weight: float = 0.20
    min_score: float = 0.05
    required_kinds: tuple[EvidenceKind, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        self.token_budget = max(1, int(self.token_budget))
        self.min_reliability = min(1.0, max(0.0, float(self.min_reliability)))
        self.required_kinds = tuple(
            kind if isinstance(kind, EvidenceKind) else EvidenceKind(kind)
            for kind in self.required_kinds
        )


@dataclass(slots=True)
class RankingContext:
    """Shared inputs used while ranking an evidence pool."""

    query: str
    corpus: list[str]
    selected: list[Evidence]
    required_claims: list[str]
    conflicts: dict[str, set[str]]


class EvidenceSelector:
    """Greedy submodular-style selection with traceable score components."""

    def __init__(self, config: SelectorConfig | None = None) -> None:
        self.config = config or SelectorConfig()

    def select(
        self,
        query: str,
        evidence: list[Evidence],
        *,
        required_claims: list[str] | None = None,
    ) -> SelectionResult:
        required_claims = _clean(required_claims or [])
        candidates = [
            ensure_token_count(item)
            for item in evidence
            if item.reliability >= self.config.min_reliability
        ]
        ranking_context = RankingContext(
            query=str(query or ""),
            corpus=[evidence_search_text(item) for item in candidates],
            selected=[],
            required_claims=required_claims,
            conflicts=conflict_map(candidates),
        )
        selected = ranking_context.selected
        scores: list[SelectionScore] = []
        used = 0

        # Seed mandatory source categories first.  This prevents a highly
        # repetitive evidence type from exhausting the entire context budget.
        for kind in self.config.required_kinds:
            pool = [item for item in candidates if item.kind == kind and item not in selected]
            scored = self._rank(pool, ranking_context)
            chosen = self._first_fitting(scored, self.config.token_budget - used)
            if chosen is not None:
                item, score = chosen
                selected.append(item)
                scores.append(score)
                used += item.token_count

        while used < self.config.token_budget:
            pool = [item for item in candidates if item not in selected]
            if not pool:
                break
            ranked = self._rank(pool, ranking_context)
            chosen = self._first_fitting(ranked, self.config.token_budget - used)
            if chosen is None or chosen[1].total < self.config.min_score:
                break
            item, score = chosen
            selected.append(item)
            scores.append(score)
            used += item.token_count

        covered = {
            claim_id
            for item in selected
            for claim_id in (*item.supports, *item.contradicts)
        }
        return SelectionResult(
            selected=selected,
            scores=scores,
            token_budget=self.config.token_budget,
            used_tokens=used,
            query=str(query or ""),
            required_claims=required_claims,
            uncovered_claims=[claim for claim in required_claims if claim not in covered],
        )

    def _rank(
        self,
        pool: list[Evidence],
        context: RankingContext,
    ) -> list[tuple[Evidence, SelectionScore]]:
        ranked: list[tuple[Evidence, SelectionScore]] = []
        selected_ids = {item.evidence_id for item in context.selected}
        already_covered = {
            claim_id
            for item in context.selected
            for claim_id in (*item.supports, *item.contradicts)
        }
        for item in pool:
            text = evidence_search_text(item)
            relevance = idf_overlap(context.query, text, context.corpus)
            max_similarity = max(
                (
                    cosine_similarity(text, evidence_search_text(other))
                    for other in context.selected
                ),
                default=0.0,
            )
            novelty = 1.0 - max_similarity
            item_claims = set(item.supports) | set(item.contradicts)
            needed = set(context.required_claims) - already_covered
            claim_coverage = (
                len(item_claims & needed) / len(needed) if needed else 0.0
            )
            linked_conflicts = context.conflicts.get(item.evidence_id, set())
            conflict_coverage = 1.0 if linked_conflicts & selected_ids else 0.0
            risk = min(1.0, max(0.0, float(item.metadata.get("risk", 0.0) or 0.0)))
            total = (
                self.config.relevance_weight * relevance
                + self.config.reliability_weight * item.reliability
                + self.config.novelty_weight * novelty
                + self.config.claim_coverage_weight * claim_coverage
                + self.config.conflict_coverage_weight * conflict_coverage
                - self.config.risk_weight * risk
            )
            ranked.append(
                (
                    item,
                    SelectionScore(
                        evidence_id=item.evidence_id,
                        relevance=relevance,
                        reliability=item.reliability,
                        novelty=novelty,
                        claim_coverage=claim_coverage,
                        conflict_coverage=conflict_coverage,
                        risk=risk,
                        total=total,
                        marginal_tokens=item.token_count,
                    ),
                )
            )
        ranked.sort(key=lambda pair: (-pair[1].total, pair[0].token_count, pair[0].evidence_id))
        return ranked

    @staticmethod
    def _first_fitting(
        ranked: list[tuple[Evidence, SelectionScore]], remaining: int
    ) -> tuple[Evidence, SelectionScore] | None:
        return next((pair for pair in ranked if pair[0].token_count <= remaining), None)


def vector_top_k(
    query: str, evidence: list[Evidence], *, token_budget: int
) -> SelectionResult:
    """Deterministic bag-of-words retrieval baseline used by the benchmark."""

    prepared = [ensure_token_count(item) for item in evidence]
    ranked = sorted(
        prepared,
        key=lambda item: (
            -cosine_similarity(query, evidence_search_text(item)),
            item.token_count,
            item.evidence_id,
        ),
    )
    selected: list[Evidence] = []
    used = 0
    for item in ranked:
        if used + item.token_count <= token_budget:
            selected.append(item)
            used += item.token_count
    return SelectionResult(selected, [], token_budget, used, query)


def last_k(
    query: str, evidence: list[Evidence], *, token_budget: int
) -> SelectionResult:
    """Most-recent-first truncation baseline."""

    prepared = [ensure_token_count(item) for item in evidence]
    selected_reversed: list[Evidence] = []
    used = 0
    for item in reversed(prepared):
        if used + item.token_count <= token_budget:
            selected_reversed.append(item)
            used += item.token_count
    return SelectionResult(list(reversed(selected_reversed)), [], token_budget, used, query)


def _clean(values: list[str]) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def evidence_search_text(item: Evidence) -> str:
    """Build the deterministic lexical representation used by selectors."""

    parts = [
        item.summary,
        item.content,
        " ".join(item.tags),
        " ".join(item.supports),
        " ".join(item.contradicts),
    ]
    return " ".join(filter(None, parts))


def ensure_token_count(item: Evidence) -> Evidence:
    """Populate a deterministic token estimate when an item has no count."""

    if not item.token_count:
        item.token_count = estimate_tokens(
            f"[{item.evidence_id}] {item.summary or item.content}\nSource: {item.source}"
        )
    return item
