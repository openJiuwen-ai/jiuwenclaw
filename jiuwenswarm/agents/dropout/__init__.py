# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""AgentDropout: test-time rectify-or-reject pruning for team mode.

Transfers the key idea from AgentDropoutV2 without porting AutoGen, datasets,
or embedding metric pools. See ``service.AgentDropoutService``.
"""

from jiuwenswarm.agents.dropout.auditor import RectifyOrRejectAuditor
from jiuwenswarm.agents.dropout.member_tracker import MemberDropoutTracker
from jiuwenswarm.agents.dropout.metrics import get_simple_team_metrics
from jiuwenswarm.agents.dropout.resolve import (
    DEFAULT_TEAM_PRUNING_STRATEGY,
    TEAM_PRUNING_STRATEGIES,
    resolve_agent_dropout_config,
    resolve_team_pruning,
)
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
    "DEFAULT_TEAM_PRUNING_STRATEGY",
    "DropoutDecision",
    "EvaluationResult",
    "MemberDropoutTracker",
    "RectifyOrRejectAuditor",
    "ScoreboardEntry",
    "TEAM_PRUNING_STRATEGIES",
    "get_simple_team_metrics",
    "resolve_agent_dropout_config",
    "resolve_team_pruning",
]
