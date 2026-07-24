# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Facade: evaluate a teammate contribution (pass / rectify / reject / drop)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from jiuwenswarm.agents.dropout.auditor import AuditorLLM, RectifyOrRejectAuditor
from jiuwenswarm.agents.dropout.member_tracker import MemberDropoutTracker
from jiuwenswarm.agents.dropout.scoreboard import ContributionScoreboard
from jiuwenswarm.agents.dropout.types import (
    ContributionAction,
    EvaluationResult,
)


@dataclass
class AgentDropoutConfig:
    """Runtime knobs for AgentDropout (mirrors config.yaml agent_dropout)."""

    enabled: bool = False
    max_rectify_attempts: int = 2
    pass_rate: float = 1.0
    drop_after_failures: int = 2
    min_active_members: int = 2
    use_simple_audit: bool = True

    @classmethod
    def from_mapping(cls, raw: dict[str, Any] | None) -> AgentDropoutConfig:
        data = raw if isinstance(raw, dict) else {}
        return cls(
            enabled=bool(data.get("enabled", False)),
            max_rectify_attempts=max(1, int(data.get("max_rectify_attempts", 2))),
            pass_rate=float(data.get("pass_rate", 1.0)),
            drop_after_failures=max(1, int(data.get("drop_after_failures", 2))),
            min_active_members=max(1, int(data.get("min_active_members", 2))),
            use_simple_audit=bool(data.get("use_simple_audit", True)),
        )


class AgentDropoutService:
    """Coordinates audit → rectify → reject → optional member dropout."""

    def __init__(
        self,
        config: AgentDropoutConfig | None = None,
        *,
        llm: AuditorLLM | None = None,
        auditor: RectifyOrRejectAuditor | None = None,
        scoreboard: ContributionScoreboard | None = None,
        tracker: MemberDropoutTracker | None = None,
    ) -> None:
        self.config = config or AgentDropoutConfig()
        self.auditor = auditor or RectifyOrRejectAuditor(
            llm=llm,
            pass_rate=self.config.pass_rate,
            use_simple_audit=self.config.use_simple_audit,
            prune_enabled=True,
        )
        self.scoreboard = scoreboard or ContributionScoreboard(prune_enabled=True)
        self.tracker = tracker or MemberDropoutTracker(
            drop_after_failures=self.config.drop_after_failures,
            min_active_members=self.config.min_active_members,
        )
        # Per-member rectify attempt counters within a contribution cycle.
        self._rectify_attempts: dict[str, int] = {}
        self._pending_feedback: dict[str, str] = {}

    def reset(self) -> None:
        self.scoreboard.reset()
        self.tracker.reset()
        self._rectify_attempts.clear()
        self._pending_feedback.clear()

    def get_pending_feedback(self, member_name: str) -> str | None:
        return self._pending_feedback.get(member_name)

    def clear_pending_feedback(self, member_name: str) -> None:
        self._pending_feedback.pop(member_name, None)

    def rectify_attempt(self, member_name: str) -> int:
        return self._rectify_attempts.get(member_name, 0)

    async def evaluate_contribution(
        self,
        *,
        task: str,
        content: str,
        member_name: str,
        role: str = "teammate",
        active_members: int = 2,
        message_id: str | None = None,
    ) -> EvaluationResult:
        """Evaluate one outbound contribution.

        Returns:
            EvaluationResult with action in {PASS, RECTIFY, REJECT, DROP}.
        """
        mid = message_id or str(uuid.uuid4())
        attempt = self._rectify_attempts.get(member_name, 0) + 1
        self._rectify_attempts[member_name] = attempt

        audit = await self.auditor.judge(
            task=task,
            agent_output=content,
            attempt_num=attempt,
            role=role,
        )

        if audit.passed:
            self.scoreboard.update(
                message_id=mid,
                content=content,
                source=member_name,
                judgements=audit.judgements,
                is_pruned=False,
            )
            self.tracker.record_pass(member_name)
            self._rectify_attempts.pop(member_name, None)
            self.clear_pending_feedback(member_name)
            return EvaluationResult(
                action=ContributionAction.PASS,
                audit=audit,
                message_id=mid,
                rectify_attempt=attempt,
            )

        # Failed audit: rectify if attempts remain, else reject (+ maybe drop).
        max_attempts = self.config.max_rectify_attempts
        if attempt < max_attempts and audit.feedback:
            self._pending_feedback[member_name] = audit.feedback
            return EvaluationResult(
                action=ContributionAction.RECTIFY,
                audit=audit,
                message_id=mid,
                rectify_attempt=attempt,
            )

        # Final failure: prune from shared history and count a member strike.
        self.scoreboard.update(
            message_id=mid,
            content=content,
            source=member_name,
            judgements=audit.judgements,
            is_pruned=True,
        )
        self.clear_pending_feedback(member_name)
        self._rectify_attempts.pop(member_name, None)

        dropout = self.tracker.record_failure(
            member_name,
            active_members=active_members,
        )
        action = (
            ContributionAction.DROP
            if dropout.should_drop
            else ContributionAction.REJECT
        )
        return EvaluationResult(
            action=action,
            audit=audit,
            dropout=dropout,
            message_id=mid,
            rectify_attempt=attempt,
        )
