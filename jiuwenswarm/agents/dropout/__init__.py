# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""AgentDropout: test-time rectify-or-reject pruning for team mode.

Transfers the key idea from AgentDropoutV2 without porting AutoGen, datasets,
or embedding metric pools. See ``service.AgentDropoutService``.
"""

from jiuwenswarm.agents.dropout.auditor import RectifyOrRejectAuditor
from jiuwenswarm.agents.dropout.member_tracker import MemberDropoutTracker
from jiuwenswarm.agents.dropout.metrics import get_simple_team_metrics
from jiuwenswarm.agents.dropout.scoreboard import ContributionScoreboard
from jiuwenswarm.agents.dropout.service import AgentDropoutConfig, AgentDropoutService
from jiuwenswarm.agents.dropout.types import (
    AuditJudgement,
    AuditResult,
    ContributionAction,
    DropoutDecision,
    EvaluationResult,
    ScoreboardEntry,
)

__all__ = [
    "AgentDropoutConfig",
    "AgentDropoutService",
    "AuditJudgement",
    "AuditResult",
    "ContributionAction",
    "ContributionScoreboard",
    "DropoutDecision",
    "EvaluationResult",
    "MemberDropoutTracker",
    "RectifyOrRejectAuditor",
    "ScoreboardEntry",
    "get_simple_team_metrics",
]
