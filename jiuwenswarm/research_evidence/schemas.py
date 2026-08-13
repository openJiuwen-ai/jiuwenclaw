# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Serializable schemas used by the research-evidence subsystem."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class EvidenceKind(str, Enum):
    """Kinds used to preserve source diversity during context selection."""

    LITERATURE = "literature"
    EXPERIMENT = "experiment"
    METHOD = "method"
    NEGATIVE_RESULT = "negative_result"
    CONSTRAINT = "constraint"
    NOTE = "note"


def utc_now_iso() -> str:
    """Return a stable, timezone-aware ISO timestamp."""

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(slots=True)
class Evidence:
    """One traceable piece of research evidence.

    ``supports`` and ``contradicts`` contain claim identifiers.  Explicit
    ``conflict_ids`` capture evidence-to-evidence conflicts when both sides are
    already known.  ``metadata`` may carry domain data such as a DOI, random
    seed, configuration hash, or a structured numeric result.
    """

    evidence_id: str
    kind: EvidenceKind | str
    content: str
    source: str
    summary: str = ""
    reliability: float = 0.5
    created_at: str = field(default_factory=utc_now_iso)
    supports: list[str] = field(default_factory=list)
    contradicts: list[str] = field(default_factory=list)
    conflict_ids: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    token_count: int = 0

    def __post_init__(self) -> None:
        self.evidence_id = str(self.evidence_id).strip()
        self.kind = self.kind if isinstance(self.kind, EvidenceKind) else EvidenceKind(str(self.kind))
        self.content = str(self.content).strip()
        self.source = str(self.source).strip()
        self.summary = str(self.summary).strip()
        self.reliability = min(1.0, max(0.0, float(self.reliability)))
        self.supports = _clean_strings(self.supports)
        self.contradicts = _clean_strings(self.contradicts)
        self.conflict_ids = _clean_strings(self.conflict_ids)
        self.tags = _clean_strings(self.tags)
        self.token_count = max(0, int(self.token_count or 0))
        if not self.evidence_id:
            raise ValueError("evidence_id must not be empty")
        if not self.content:
            raise ValueError("content must not be empty")
        if not self.source:
            raise ValueError("source must not be empty")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["kind"] = self.kind.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Evidence":
        return cls(**dict(data))


@dataclass(slots=True)
class Claim:
    """A paper or analysis claim linked to supporting evidence."""

    claim_id: str
    text: str
    evidence_ids: list[str] = field(default_factory=list)
    required_support: int = 1
    strength: float = 0.5
    status: str = "draft"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.claim_id = str(self.claim_id).strip()
        self.text = str(self.text).strip()
        self.evidence_ids = _clean_strings(self.evidence_ids)
        self.required_support = max(1, int(self.required_support))
        self.strength = min(1.0, max(0.0, float(self.strength)))
        self.status = str(self.status or "draft").strip().lower()
        if not self.claim_id or not self.text:
            raise ValueError("claim_id and text must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Claim":
        return cls(**dict(data))


@dataclass(slots=True)
class SelectionScore:
    """Auditable score components for one selected evidence item."""

    evidence_id: str
    relevance: float
    reliability: float
    novelty: float
    claim_coverage: float
    conflict_coverage: float
    risk: float
    total: float
    marginal_tokens: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SelectionResult:
    """Evidence selection output plus a complete decision trace."""

    selected: list[Evidence]
    scores: list[SelectionScore]
    token_budget: int
    used_tokens: int
    query: str
    required_claims: list[str] = field(default_factory=list)
    uncovered_claims: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected": [item.to_dict() for item in self.selected],
            "scores": [score.to_dict() for score in self.scores],
            "token_budget": self.token_budget,
            "used_tokens": self.used_tokens,
            "query": self.query,
            "required_claims": list(self.required_claims),
            "uncovered_claims": list(self.uncovered_claims),
        }


@dataclass(slots=True)
class VerificationIssue:
    """A machine-readable claim-grounding failure or warning."""

    code: str
    claim_id: str
    message: str
    severity: str = "error"
    evidence_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean_strings(values: list[str] | tuple[str, ...] | None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        cleaned = str(value).strip()
        if cleaned and cleaned not in seen:
            result.append(cleaned)
            seen.add(cleaned)
    return result
