# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Evaluation-based Decision policy — heuristic scoring of Proposal quality."""

from __future__ import annotations

import logging

from jiuwenswarm.evolve.decision_policies.base import DecisionPolicy
from jiuwenswarm.evolve.models import (
    DecisionResult,
    DecisionSuggestion,
    Proposal,
)
from jiuwenswarm.evolve.registry import decision_policies

logger = logging.getLogger(__name__)


@decision_policies.register("eval_policy")
class EvalPolicy(DecisionPolicy):
    """Score a Proposal on evidence strength, root cause specificity, and
    fix executability.

    This is a heuristic evaluator — no LLM required. It produces a
    score 0–1 by examining the structure and specificity of the Proposal
    fields.
    """

    def __init__(self) -> None:
        super().__init__(name="eval_policy", version="1.0")

    async def evaluate(self, proposal: Proposal) -> DecisionResult:
        score = 0.0
        reasons: list[str] = []

        # 1. Evidence strength (0–0.35)
        evidence_count = len(proposal.failure_evidence)
        if evidence_count >= 3:
            score += 0.35
            reasons.append("strong evidence (3+ refs)")
        elif evidence_count >= 1:
            score += 0.20
            reasons.append("moderate evidence (1-2 refs)")
        else:
            reasons.append("no evidence")

        # 2. Root cause specificity (0–0.25)
        root_cause = proposal.root_cause.lower()
        vague_words = ("maybe", "possibly", "something", "unknown", "might be")
        if len(root_cause) > 50 and not any(w in root_cause for w in vague_words):
            score += 0.25
            reasons.append("specific root cause")
        elif len(root_cause) > 20:
            score += 0.10
            reasons.append("vague root cause")
        else:
            reasons.append("too-short root cause")

        # 3. Fix executability (0–0.25)
        fix = proposal.targeted_fix
        if fix and isinstance(fix, dict) and fix.get("action"):
            score += 0.25
            reasons.append("actionable fix with explicit action")
        elif fix:
            score += 0.10
            reasons.append("fix present but no action key")

        # 4. Risk assessment (0–0.15)
        risk = (proposal.risk or "").lower()
        high_risk_words = ("break", "disrupt", "dangerous", "critical")
        if not proposal.risk:
            score += 0.05
            reasons.append("no risk assessment")
        elif any(w in risk for w in high_risk_words):
            reasons.append("high risk flagged")
            # Keep score low for high-risk proposals
        else:
            score += 0.15
            reasons.append("acceptable risk")

        # Determine suggestion based on score
        suggestion = DecisionSuggestion.CANDIDATE
        if score >= 0.60:
            suggestion = DecisionSuggestion.ACTIVE
        elif score < 0.30:
            suggestion = DecisionSuggestion.CANDIDATE  # not rejected, just weak

        return DecisionResult(
            proposal_id=proposal.proposal_id,
            policy_name=self.name,
            policy_version=self.version,
            score=round(score, 2),
            reason="; ".join(reasons),
            suggestion=suggestion,
            blocking=False,
        )
