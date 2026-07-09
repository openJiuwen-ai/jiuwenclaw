# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Core data models for the self-evolution framework — **the integration contract**.

All pluggable components (ProposalGenerator, DecisionPolicy, ApplyWriter)
exchange data exclusively through the types defined here. Custom
algorithms MUST produce / consume these types to be compatible with the
pipeline.

Data flow overview::

    TraceBatch ──→ ProposalGenerator.generate() ──→ Proposal[]
      → DecisionPolicy.evaluate(Proposal) ──→ DecisionResult[]
        → ApplyWriter.apply(Proposal) ──→ ApplyRecord[]

Audit chain (IDs link every record back to its source trace)::

    trace_id
      └── proposal_id (via failure_evidence[].trace_id)
            ├── decision_id (via DecisionResult.proposal_id)
            └── apply_id    (via ApplyRecord.proposal_id)
                  └── stored_object_id

Every core model carries ``schema_version`` for future migration and
``metadata`` for non-critical extension (NOT for fields that the pipeline
depends on).

Schema design follows :file:`evolve-draft.md` §6.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ============================================================================
# Type alias
# ============================================================================

# Use Any for dict values to avoid recursive type alias that triggers
# Pydantic 2.x RecursionError.  Semantic constraints are enforced by
# schema_version and by each pluggable component's own validation.
JsonValue = Any


# ============================================================================
# Enums
# ============================================================================


class ProposalTargetType(StrEnum):
    """Which component class a Proposal targets.

    The value determines which :class:`ApplyWriter` handles the Proposal:

    ============  ==============================================
    Value         Writer
    ============  ==============================================
    ``skill``     :class:`~apply_writers.SkillExperienceWriter`
    ``memory``    :class:`~apply_writers.MemoryPolicyWriter`
    ``training``  :class:`~apply_writers.TrainingCandidateWriter`
    ============  ==============================================
    """

    SKILL = "skill"
    MEMORY = "memory"
    TRAINING = "training"


class ProposalState(StrEnum):
    """Life-cycle state of a Proposal.

    State transitions are driven by :class:`DecisionPolicy` results
    (never by the policies themselves — the :class:`Pipeline` applies
    changes after collecting all :class:`DecisionResult` objects).

    ============  =================================================
    State         Meaning
    ============  =================================================
    ``candidate`` Newly generated, awaiting decision (initial state)
    ``active``    Passed all decisions; eligible for Apply
    ``rejected``  Blocked by one or more decisions; will NOT apply
    ============  =================================================
    """

    CANDIDATE = "candidate"
    ACTIVE = "active"
    REJECTED = "rejected"


class DecisionSuggestion(StrEnum):
    """Suggested outcome for a Proposal from a single :class:`DecisionPolicy`.

    The Pipeline aggregates suggestions from all policies to determine
    the final :class:`ProposalState`.
    """

    CANDIDATE = "candidate"
    ACTIVE = "active"
    REJECTED = "rejected"


class ApplyStatus(StrEnum):
    """Final outcome reported by an :class:`ApplyWriter`.

    ==========  ===================================================
    Status      Meaning
    ==========  ===================================================
    ``applied`` Write-back succeeded; ``stored_object_id`` is set
    ``skipped`` Not applicable (wrong type, wrong state, etc.)
    ``failed``  Write-back attempted but failed
    ==========  ===================================================
    """

    APPLIED = "applied"
    SKIPPED = "skipped"
    FAILED = "failed"


class TargetStore(StrEnum):
    """Concrete storage destination for an :class:`ApplyRecord`.

    Each :class:`ApplyWriter` maps to one target store.
    """

    SKILL_EXPERIENCE_STORE = "skill_experience_store"
    MEMORY_POLICY_STORE = "memory_policy_store"
    TRAINING_CANDIDATE_STORE = "training_candidate_store"


# ============================================================================
# Core data types
# ============================================================================


class EvidenceRef(BaseModel):
    """A precise pointer to a location within an OTEL trace span.

    Used by :class:`Proposal.failure_evidence` to anchor each claim
    to a specific piece of observable data.  The ``trace_id`` is
    mandatory; ``span_id`` and ``field_path`` add granularity.

    Example::

        EvidenceRef(
            trace_id="abc123",
            span_id="span-07",
            field_path="events[0].attributes.exception.message",
            description="bash: command not found",
        )
    """

    model_config = ConfigDict(extra="forbid")

    trace_id: str
    """OTEL trace ID — the only mandatory reference."""

    span_id: str | None = None
    """Optional OTEL span ID within the trace."""

    field_path: str | None = None
    """Optional JSON-path-like pointer into the span (events, attributes)."""

    description: str
    """Human-readable summary of the observed evidence at this location."""


# ============================================================================
# Proposal — the core intermediate object
# ============================================================================


class Proposal(BaseModel):
    """A structured candidate improvement discovered from trace analysis.

    A Proposal captures the complete reasoning chain from evidence to
    fix.  Every Proposal MUST answer five questions:

    1. **What evidence?**  → :attr:`failure_evidence`
    2. **What root cause?** → :attr:`root_cause`
    3. **What fix?** → :attr:`targeted_fix`
    4. **What impact?** → :attr:`predicted_impact`
    5. **What risk?** → :attr:`risk`

    .. attention::

       The fields ``failure_evidence``, ``root_cause``,
       ``targeted_fix``, and ``predicted_impact`` are **required**.
       Do not leave them empty or the :class:`~decision_policies.RulePolicy`
       will reject the Proposal.

    **Guidance for ProposalGenerator authors:**

    - Use a unique ``proposer_name`` (registered in the
      ``proposal_generators`` registry) so results can be traced back
      to a specific algorithm.
    - Put algorithm-specific state in ``metadata``, NOT in ad-hoc
      top-level fields.  The pipeline does not read your custom keys.
    - ``proposal_type`` is a free-form string.  Recommended convention:
      ``{action}_{component}``, e.g. ``add_skill_experience``,
      ``add_memory_retrieval_hint``.
    - ``target_id`` should be the name/path of the target entity (skill
      name, memory ID, etc.) when known; otherwise leave ``None``.
    """

    model_config = ConfigDict(extra="forbid")

    # -- Identity ----------------------------------------------------------

    proposal_id: str = Field(
        default_factory=lambda: f"prop-{uuid.uuid4().hex[:12]}"
    )
    """Unique proposal identifier.  Auto-generated UUID-based string."""

    proposer_name: str = "unknown"
    """Name of the :class:`~proposal_generators.ProposalGenerator` that
    created this proposal.  Must match a registry key."""

    # -- Target ------------------------------------------------------------

    target_type: ProposalTargetType
    """Which component class this proposal targets.

    Determines which :class:`~apply_writers.ApplyWriter` handles it.

    ============  =========================================
    ``skill``     Write to ``{skills_dir}/{name}/evolutions.json``
    ``memory``    Write to ``{memory_dir}/policies/``
    ``training``  Insert into ``training_candidates`` table
    ============  =========================================
    """

    target_id: str | None = None
    """Optional identifier of the specific target entity.

    - For ``skill``: the skill directory name.
    - For ``memory``: the memory policy name.
    - For ``training``: not required (uses trace_ids directly).
    """

    proposal_type: str
    """Fine-grained proposal category.  Free-form string with convention
    ``{action}_{component}``, e.g.:

    - ``add_skill_experience``
    - ``add_memory_retrieval_hint``
    - ``add_memory_usage_policy``
    - ``flag_training_candidate``
    """

    # -- Evidence & reasoning (the four mandatory fields) ------------------

    failure_evidence: list[EvidenceRef]
    """List of specific trace locations supporting this proposal.

    MUST be non-empty.  Each entry must include at least a ``trace_id``
    and a ``description``.  For ``training`` proposals, the
    :class:`~apply_writers.TrainingCandidateWriter` uses these trace IDs
    after Decision promotes the proposal to ``active``.
    """

    root_cause: str
    """The root cause analysis — WHY the issue occurred.

    MUST be non-empty.  Be specific; vague text like "something went
    wrong" lowers the :class:`~decision_policies.EvalPolicy` score.
    """

    targeted_fix: dict[str, JsonValue]
    """WHAT should change.  Structure depends on ``proposal_type``.

    For Skill proposals, include at minimum ``{"action": "...",
    "suggestion": "..."}``.  The ``action`` key is recommended for all
    proposal types as a one-word summary of the fix strategy.

    Example (Skill)::

        {"action": "add_error_handling", "tool": "bash",
         "suggestion": "Wrap command in try/except and retry once"}

    Example (Memory)::

        {"action": "add_retrieval_hint", "query_pattern": "k8s",
         "hint": "Include namespace and pod name in search"}
    """

    predicted_impact: str
    """Expected improvement if this fix is applied.

    MUST be non-empty.  Used by :class:`~decision_policies.EvalPolicy`
    for quality scoring and as the ``EvolutionRecord.summary`` in the
    ``evolutions.json`` output.
    """

    risk: str | None = None
    """Potential downsides or side-effects.  Optional but recommended.

    - ``None`` → treated as "no assessment" (slightly lowers EvalPolicy score).
    - Text containing high-risk words (``break``, ``disrupt``, etc.) lowers score.
    """

    # -- Lifecycle (managed by the pipeline) -------------------------------

    state: ProposalState = ProposalState.CANDIDATE
    """Current state.  Managed by the :class:`Pipeline`, NOT by generators
    or policies.  Generators should always leave this at ``CANDIDATE``."""

    # -- Metadata ----------------------------------------------------------

    schema_version: str = "proposal.v1"
    """Schema version for forward/backward compatibility.

    Do NOT change.  The pipeline writes this automatically.
    """

    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    """Extension point for algorithm-specific data.

    .. warning::
       Do NOT put fields here that the pipeline depends on.  The pipeline
       only reads ``max_score`` (set during the Decision phase) and
       ``batch_id`` (set during the Generate phase).  Everything else is
       passthrough.
    """


# ============================================================================
# DecisionResult — output of a single DecisionPolicy
# ============================================================================


class DecisionResult(BaseModel):
    """A scored evaluation of one Proposal by one DecisionPolicy.

    **Guidance for DecisionPolicy authors:**

    - A DecisionPolicy MUST NOT mutate the Proposal.  It only produces
      a DecisionResult.
    - ``blocking: True`` means the Proposal should be REJECTED
      regardless of other policies' scores.
    - ``suggestion`` is the policy's recommended state, but the final
      state is determined by the Pipeline after aggregating ALL policies.
    - ``score`` is 0.0–1.0.  Use a consistent scoring rubric so scores
      are comparable across policies.
    - ``failed_checks`` should list short machine-readable identifiers
      (e.g. ``empty_root_cause``, ``duplicate_proposal``) for each
      check that failed.
    """

    model_config = ConfigDict(extra="forbid")

    decision_id: str = Field(
        default_factory=lambda: f"dec-{uuid.uuid4().hex[:12]}"
    )
    """Unique decision identifier.  Auto-generated."""

    proposal_id: str
    """Reference to the :class:`Proposal` being evaluated."""

    policy_name: str
    """Name of this DecisionPolicy (must match a registry key)."""

    policy_version: str
    """Version string of this policy implementation."""

    score: float = Field(ge=0.0, le=1.0)
    """Quality score, **0.0** (worst) to **1.0** (best).

    The Pipeline records ``max(score)`` from all policies into
    ``Proposal.metadata["max_score"]`` for downstream use.
    """

    reason: str
    """Human-readable explanation for the score and suggestion."""

    suggestion: DecisionSuggestion
    """Recommended state for the Proposal."""

    blocking: bool = False
    """If ``True``, the Proposal MUST be rejected regardless of other
    policies.  Use for hard validation failures (empty required fields,
    unsupported types, duplicates)."""

    failed_checks: list[str] = Field(default_factory=list)
    """Machine-readable identifiers of failed validation checks.

    Example: ``["empty_root_cause", "unsupported_target_type"]``"""

    # -- Metadata ----------------------------------------------------------

    schema_version: str = "decision_result.v1"

    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    """Extension point for algorithm-specific data."""


# ============================================================================
# ApplyRecord — proof of write-back
# ============================================================================


class ApplyRecord(BaseModel):
    """Proof that a Proposal was (or was not) written to a target store.

    This is the last link in the audit chain.  Given an ``apply_id`` you
    can find the ``stored_object_id`` (e.g. the ``evolutions.json`` path
    or the training_candidates row), and from there trace back through
    ``proposal_id`` → ``trace_id``.

    **Guidance for ApplyWriter authors:**

    - Return ``SKIPPED`` when the Proposal's ``target_type`` does not
      match your writer or when ``state != ACTIVE``.
    - Return ``FAILED`` only when you attempted the write but it errored.
    - Set ``stored_object_id`` to the persistent identifier of the
      written object (file path, DB row ID, etc.).
    """

    model_config = ConfigDict(extra="forbid")

    apply_id: str = Field(
        default_factory=lambda: f"apply-{uuid.uuid4().hex[:12]}"
    )
    """Unique apply identifier.  Auto-generated."""

    proposal_id: str
    """Reference to the :class:`Proposal` being applied."""

    target_type: ProposalTargetType
    """Component class that was the target."""

    target_store: TargetStore
    """Which concrete store received the write."""

    target_id: str | None = None
    """Optional identifier of the specific target entity within the store."""

    status: ApplyStatus
    """Outcome of the write operation."""

    stored_object_id: str | None = None
    """Persistent identifier of the written object.

    For SkillExperienceWriter, this is the ``evolutions.json`` path.
    For TrainingCandidateWriter, this is a row-count summary string.
    For MemoryPolicyWriter, this is the policy file path.
    """

    reason: str
    """Explanation of the outcome.  For SKIPPED, say why.  For FAILED,
    include the exception message."""

    applier_name: str = "unknown"
    """Name of the :class:`~apply_writers.ApplyWriter` that executed this
    write (must match a registry key)."""

    # -- Metadata ----------------------------------------------------------

    schema_version: str = "apply_record.v1"

    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    """Extension point for algorithm-specific data."""


# ============================================================================
# TraceBatch — pipeline input (dataclass, not a Pydantic model)
# ============================================================================


@dataclass
class TraceBatch:
    """Describes a batch of traces to feed into the evolution pipeline.

    This is a lightweight descriptor — it does **not** hold span data.
    :class:`~proposal_generators.ProposalGenerator` instances use
    ``trace_ids`` to read spans on demand from the
    :class:`~storage.SqliteStore`.

    Created by a :class:`~trigger.TraceSampler` (CLI or periodic scheduler).
    """

    batch_id: str = field(
        default_factory=lambda: (
            f"batch-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
            f"-{uuid.uuid4().hex[:8]}"
        )
    )
    """Unique batch identifier.  Auto-generated with timestamp."""

    trace_ids: list[str] = field(default_factory=list)
    """OTEL trace IDs that the pipeline will analyse.

    Samplers populate this from ``traces.db`` via ``SqliteStore``."""

    source: str = "manual"
    """How this batch was triggered.

    ============  ============================================
    ``manual``    CLI command (``jiuwenswarm-evolve run``)
    ``periodic``  AgentServer EvolutionScheduler
    ``benchmark`` Benchmark run
    ============  ============================================
    """

    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    metadata: dict[str, JsonValue] = field(default_factory=dict)
    """Extension point (e.g. ``benchmark_run_id``)."""


# ============================================================================
# PDA Phase 1 — Experience governance & task evaluation models
# ============================================================================


class ExperienceOperationType(StrEnum):
    """Operation types for experience governance.

    Propose phase explicitly declares governance intent; Decision validates;
    Apply faithfully executes. These are pluggable — PDA algorithm defines
    them independently; other algorithms (LLMProposer) do not use them.
    """

    ADD = "add"
    MERGE = "merge"
    UPDATE = "update"
    DEPRECATE = "deprecate"
    REPLACE = "replace"
    NOOP = "noop"


class ExperienceOperation(BaseModel):
    """A single experience operation within a Proposal.

    Carried in ``Proposal.metadata["operations"]``. Only used by PDA-style
    algorithms — existing algorithms (LLMProposer, RulePolicy) ignore this
    field entirely.
    """

    model_config = ConfigDict(extra="forbid")

    op: ExperienceOperationType
    """Operation type — determines what Apply does."""

    target_experience_id: str | None = None
    """Target experience for MERGE/REPLACE/UPDATE/DEPRECATE.
    Required when op != ADD/NOOP."""

    new_content: str | None = None
    """New experience content for ADD/REPLACE/UPDATE.
    Required when op in {ADD, REPLACE, UPDATE}."""

    reason: str
    """Why this operation was chosen over alternatives."""

    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    """Evidence supporting this operation."""

    expected_effect: str | None = None
    """Predicted improvement if this operation is applied."""

    risk: str | None = None
    """Potential downsides."""

    @model_validator(mode="after")
    def _validate_op_requirements(self) -> "ExperienceOperation":
        """Ensure required fields are present for each operation type."""
        content_required = {
            ExperienceOperationType.ADD,
            ExperienceOperationType.REPLACE,
            ExperienceOperationType.UPDATE,
        }
        target_required = {
            ExperienceOperationType.MERGE,
            ExperienceOperationType.REPLACE,
            ExperienceOperationType.UPDATE,
            ExperienceOperationType.DEPRECATE,
        }

        if self.op in content_required and not self.new_content:
            raise ValueError(f"op={self.op.value} requires new_content")
        if self.op in target_required and not self.target_experience_id:
            raise ValueError(f"op={self.op.value} requires target_experience_id")
        return self


# NOTE: GovernanceContext and TraceOutcome have been moved to
# jiuwenswarm/evolve/ahe/models.py — they are AHE algorithm internals.
