# Copyright (c) Huawei Technologies, Co., Ltd. 2026. All rights reserved.
"""DiagnosisAgent data models — independent from evolve/models.py.

Pluggable design: PDA algorithm owns these models. Other algorithms
(LLMProposer, RulePolicy) never import from this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field

ALLOWED_ISSUE_TYPES = {"工具错误", "幻觉", "循环", "不合规", "截断"}


@dataclass
class DiagnosisIssue:
    """Single diagnostic finding from trace analysis."""

    issue_type: str
    summary: str
    evidence: str
    trace_id: str
    span_index: int
    root_cause: str | None = None
    suggested_fix: str | None = None

    def __post_init__(self):
        if self.issue_type not in ALLOWED_ISSUE_TYPES:
            raise ValueError(
                f"issue_type must be one of {sorted(ALLOWED_ISSUE_TYPES)}, "
                f"got '{self.issue_type}'"
            )


@dataclass
class DiagnosisResult:
    """Complete diagnosis output from DiagnosisAgent."""

    mode: str  # "diagnose" | "propose"
    issues: list[DiagnosisIssue] = field(default_factory=list)
    response: str = ""
    iterations: int = 0
    budget_exceeded: bool = False
    # In propose mode, this carries Proposal objects
    # (imported at runtime to avoid circular dependency)
    proposals: list | None = None
