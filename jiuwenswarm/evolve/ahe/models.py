# Copyright (c) Huawei Technologies, Co., Ltd. 2026. All rights reserved.
"""AHE algorithm-specific data models.

These are AHE algorithm internals — not part of the PDA framework contract.
Framework models (Proposal, DecisionResult, EvidenceRef) remain in
evolve/models.py.

PDA framework owns the interfaces; AHE owns the implementation details.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TraceOutcome(BaseModel):
    """Task completion evaluation result for a single trace.

    Produced by TraceOutcomeEvaluator (AHE algorithm). Not used by
    non-AHE algorithms (LLMProposer directly reads spans).
    """

    model_config = ConfigDict(extra="forbid")

    trace_id: str
    task_name: str | None = None
    outcome: str
    """Must be "pass", "fail", or "uncertain"."""

    score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    judgment_method: str = ""
    """How determined: "span_error" | "heuristic" | "llm_evaluator"."""
    reason: str = ""
    key_evidence: str = ""
    missing_requirements: list[str] = Field(default_factory=list)
    needs_external_verification: bool = False

    @model_validator(mode="after")
    def _validate_outcome(self) -> "TraceOutcome":
        valid = {"pass", "fail", "uncertain"}
        if self.outcome not in valid:
            raise ValueError(
                f"outcome must be one of {sorted(valid)}, got '{self.outcome}'"
            )
        return self
