# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Core data models for the self-evolution framework.

Defines the three cross-module objects that form the evolution pipeline's
data contract: Proposal, DecisionResult, ApplyRecord — plus supporting
types and the TraceBatch input abstraction.

Schema design follows :file:`evolve-draft.md` §6.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# JSON value type alias
# ---------------------------------------------------------------------------

# Use Any for metadata/fix dicts to avoid recursive type alias that causes
# Pydantic 2.x RecursionError. The schema versioning protects against drift.
JsonValue = Any


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ProposalTargetType(StrEnum):
    """Top-level target component of a Proposal."""

    SKILL = "skill"
    MEMORY = "memory"
    TRAINING = "training"


class ProposalState(StrEnum):
    """Lifecycle state of a Proposal."""

    CANDIDATE = "candidate"
    ACTIVE = "active"
    REJECTED = "rejected"


class DecisionSuggestion(StrEnum):
    """Suggested state from a DecisionPolicy."""

    CANDIDATE = "candidate"
    ACTIVE = "active"
    REJECTED = "rejected"


class ApplyStatus(StrEnum):
    """Outcome of an Apply operation."""

    APPLIED = "applied"
    SKIPPED = "skipped"
    FAILED = "failed"


class TargetStore(StrEnum):
    """Concrete storage target for Apply."""

    SKILL_EXPERIENCE_STORE = "skill_experience_store"
    MEMORY_POLICY_STORE = "memory_policy_store"
    TRAINING_CANDIDATE_STORE = "training_candidate_store"


# ---------------------------------------------------------------------------
# Core models
# ---------------------------------------------------------------------------

class EvidenceRef(BaseModel):
    """Pointer into a specific location within an OTEL trace span."""

    model_config = ConfigDict(extra="forbid")

    trace_id: str
    span_id: str | None = None
    field_path: str | None = None
    description: str


class Proposal(BaseModel):
    """The core intermediate object representing a candidate improvement.

    Every Proposal must answer: *what evidence?*, *what root cause?*,
    *what fix?*, *what expected impact?*, *what risk?*
    """

    model_config = ConfigDict(extra="forbid")

    proposal_id: str = Field(default_factory=lambda: f"prop-{uuid.uuid4().hex[:12]}")
    target_type: ProposalTargetType
    target_id: str | None = None
    proposal_type: str  # e.g. "add_skill_experience", "add_memory_retrieval_hint"
    failure_evidence: list[EvidenceRef]
    root_cause: str
    targeted_fix: dict[str, JsonValue]
    predicted_impact: str
    risk: str | None = None
    state: ProposalState = ProposalState.CANDIDATE
    proposer_name: str = "unknown"
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    schema_version: str = "proposal.v1"
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class DecisionResult(BaseModel):
    """Output of a DecisionPolicy evaluating a single Proposal."""

    model_config = ConfigDict(extra="forbid")

    decision_id: str = Field(
        default_factory=lambda: f"dec-{uuid.uuid4().hex[:12]}"
    )
    proposal_id: str
    policy_name: str
    policy_version: str
    score: float = Field(ge=0.0, le=1.0)
    reason: str
    suggestion: DecisionSuggestion
    blocking: bool = False
    failed_checks: list[str] = Field(default_factory=list)
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    schema_version: str = "decision_result.v1"
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class ApplyRecord(BaseModel):
    """Write-back record proving a Proposal was applied (or not) to a store."""

    model_config = ConfigDict(extra="forbid")

    apply_id: str = Field(
        default_factory=lambda: f"apply-{uuid.uuid4().hex[:12]}"
    )
    proposal_id: str
    target_type: ProposalTargetType
    target_store: TargetStore
    target_id: str | None = None
    status: ApplyStatus
    stored_object_id: str | None = None
    reason: str
    applier_name: str = "unknown"
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    schema_version: str = "apply_record.v1"
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Trace batch (dataclass — not a Pydantic model)
# ---------------------------------------------------------------------------

@dataclass
class TraceBatch:
    """Describes a batch of traces to evolve. Does not hold span data itself.

    Consumers (ProposalGenerators) use *trace_ids* to read spans on demand
    via the EvolutionStore / TraceReader.
    """

    batch_id: str = field(
        default_factory=lambda: f"batch-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    )
    trace_ids: list[str] = field(default_factory=list)
    source: str = "manual"  # "manual" | "periodic" | "benchmark"
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    metadata: dict[str, JsonValue] = field(default_factory=dict)
