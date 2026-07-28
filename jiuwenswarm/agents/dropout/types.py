# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared types for AgentDropout (rectify-or-reject) in team mode."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ContributionAction(str, Enum):
    """Outcome of evaluating a teammate contribution."""

    PASS = "pass"
    RECTIFY = "rectify"
    REJECT = "reject"
    DROP = "drop"


@dataclass(frozen=True)
class AuditJudgement:
    """Per-indicator audit finding for one agent output."""

    metric: str
    verdict: str  # "correct" | "flawed"
    evidence_quote: str = "N/A"
    reasoning: str = "N/A"
    suggestion: str = "N/A"
    impact: str = "N/A"

    @property
    def is_correct(self) -> bool:
        return self.verdict.lower() == "correct"


@dataclass
class AuditResult:
    """Aggregate result of auditing one contribution."""

    passed: bool
    judgements: list[AuditJudgement] = field(default_factory=list)
    feedback: str | None = None
    metrics_used: list[dict[str, Any]] = field(default_factory=list)
    pass_count: int = 0
    total_metrics: int = 0


@dataclass
class ScoreboardEntry:
    """Scoreboard row for one shared contribution message."""

    message_id: str
    content: str
    source: str
    judgements: list[AuditJudgement] = field(default_factory=list)
    is_pruned: bool = False


@dataclass(frozen=True)
class DropoutDecision:
    """Whether a teammate should be dropped after failed corrections."""

    should_drop: bool
    reason: str
    failure_count: int
    active_members: int
    collapse_fallback: bool = False


@dataclass
class EvaluationResult:
    """Facade result from :class:`AgentDropoutService`."""

    action: ContributionAction
    audit: AuditResult
    dropout: DropoutDecision | None = None
    message_id: str = ""
    rectify_attempt: int = 0
