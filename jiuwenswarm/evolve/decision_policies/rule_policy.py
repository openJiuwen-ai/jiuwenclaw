# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Rule-based Decision policy — structural validation of Proposals."""

from __future__ import annotations

import logging

from jiuwenswarm.evolve.decision_policies.base import DecisionPolicy
from jiuwenswarm.evolve.models import (
    DecisionResult,
    DecisionSuggestion,
    Proposal,
    ProposalTargetType,
)
from jiuwenswarm.evolve.registry import decision_policies

logger = logging.getLogger(__name__)

VALID_TARGET_TYPES = {t.value for t in ProposalTargetType}


@decision_policies.register("rule_policy")
class RulePolicy(DecisionPolicy):
    """Validates Proposal structural integrity with hard rules.

    Checks:
    - Required fields are non-empty
    - target_type is supported
    - failure_evidence is non-empty
    """

    def __init__(self) -> None:
        super().__init__(name="rule_policy", version="1.0")
        # Keep a set of seen (target_type, target_id, targeted_fix_keys)
        # for basic duplicate detection within a session.
        self._seen: set[tuple[str, str, str]] = set()

    async def evaluate(self, proposal: Proposal) -> DecisionResult:
        failed_checks: list[str] = []

        # 1. Required fields
        if not proposal.root_cause.strip():
            failed_checks.append("empty_root_cause")
        if not proposal.predicted_impact.strip():
            failed_checks.append("empty_predicted_impact")
        if not proposal.targeted_fix:
            failed_checks.append("empty_targeted_fix")
        if not proposal.failure_evidence:
            failed_checks.append("empty_failure_evidence")
        if not proposal.proposal_type.strip():
            failed_checks.append("empty_proposal_type")

        # 2. Target type check
        target_type_str = (
            proposal.target_type.value
            if hasattr(proposal.target_type, "value")
            else str(proposal.target_type)
        )
        if target_type_str not in VALID_TARGET_TYPES:
            failed_checks.append("unsupported_target_type")

        # 3. Basic duplication check
        fix_keys = "-".join(sorted(proposal.targeted_fix.keys()))
        dup_key = (
            target_type_str,
            proposal.target_id or "",
            fix_keys,
        )
        if dup_key in self._seen and fix_keys:
            failed_checks.append("duplicate_proposal")
        else:
            self._seen.add(dup_key)

        # Determine result
        if failed_checks:
            blocking = any(
                c in ("empty_root_cause", "empty_failure_evidence",
                      "empty_targeted_fix", "unsupported_target_type",
                      "duplicate_proposal")
                for c in failed_checks
            )
            return DecisionResult(
                proposal_id=proposal.proposal_id,
                policy_name=self.name,
                policy_version=self.version,
                score=0.0 if blocking else 0.3,
                reason=f"Failed checks: {', '.join(failed_checks)}",
                suggestion=DecisionSuggestion.REJECTED
                if blocking
                else DecisionSuggestion.CANDIDATE,
                blocking=blocking,
                failed_checks=failed_checks,
            )

        # All checks pass
        return DecisionResult(
            proposal_id=proposal.proposal_id,
            policy_name=self.name,
            policy_version=self.version,
            score=0.7,
            reason="All structural checks passed",
            suggestion=DecisionSuggestion.CANDIDATE,
            blocking=False,
        )
