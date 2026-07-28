# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Rectify-or-reject auditor adapted from AgentDropoutV2 Supervisor.judge."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from jiuwenswarm.agents.dropout.metrics import get_simple_team_metrics
from jiuwenswarm.agents.dropout.prompts import (
    TEAM_METRIC_AUDIT_TEMPLATE,
    build_rectify_feedback,
)
from jiuwenswarm.agents.dropout.types import AuditJudgement, AuditResult

logger = logging.getLogger(__name__)

# Injectable LLM callable: prompt -> raw response text.
AuditorLLM = Callable[[str], Awaitable[str]]


def _safe_parse_json(raw: str) -> Any | None:
    """Parse JSON from an LLM response, tolerating fenced blocks."""
    text = (raw or "").strip()
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Best-effort: first {...} or [...] slice.
        for opener, closer in (("{", "}"), ("[", "]")):
            start = text.find(opener)
            end = text.rfind(closer)
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    continue
    return None


def _judgement_from_finding(metric_name: str, finding: dict[str, Any]) -> AuditJudgement:
    is_flawed = bool(finding.get("is_flawed", False))
    return AuditJudgement(
        metric=metric_name,
        verdict="flawed" if is_flawed else "correct",
        evidence_quote=str(finding.get("evidence_quote", "N/A") or "N/A"),
        reasoning=str(finding.get("analysis", finding.get("reasoning", "N/A")) or "N/A"),
        suggestion=str(finding.get("suggestion", "N/A") or "N/A"),
        impact=str(finding.get("impact_assessment", finding.get("impact", "N/A")) or "N/A"),
    )


class RectifyOrRejectAuditor:
    """Audit teammate outputs and produce rectify feedback or a prune decision.

    Structured so a future metric-pool matcher can replace ``_select_metrics``
    without changing callers.
    """

    def __init__(
        self,
        *,
        llm: AuditorLLM | None = None,
        pass_rate: float = 1.0,
        use_simple_audit: bool = True,
        prune_enabled: bool = True,
    ) -> None:
        self._llm = llm
        self.pass_rate = float(pass_rate)
        self.use_simple_audit = bool(use_simple_audit)
        self.prune_enabled = bool(prune_enabled)

    def _select_metrics(self) -> list[dict[str, Any]]:
        # v1: fixed simple team metrics only (no embedding pool).
        if self.use_simple_audit:
            return get_simple_team_metrics()
        return get_simple_team_metrics()

    async def _audit_one_metric(
        self,
        *,
        task: str,
        role: str,
        agent_output: str,
        metric: dict[str, Any],
    ) -> AuditJudgement:
        metric_name = str(metric.get("name", "unknown"))
        evaluator = metric.get("evaluator_prompt") or {}
        trigger = str(
            evaluator.get("trigger_condition")
            or metric.get("detailed_definition")
            or metric_name
        )
        risk_alert = str(evaluator.get("risk_alert") or "")

        if self._llm is None:
            # Safe default without an LLM: presume validity (ADv2 presumption).
            return AuditJudgement(metric=metric_name, verdict="correct")

        prompt = TEAM_METRIC_AUDIT_TEMPLATE.format(
            trigger_condition=trigger,
            risk_alert=risk_alert,
            task=task,
            role=role,
            agent_output=agent_output,
        )
        try:
            raw = await self._llm(prompt)
            parsed = _safe_parse_json(raw)
            if isinstance(parsed, list) and parsed:
                parsed = parsed[0]
            if not isinstance(parsed, dict):
                logger.warning(
                    "[AgentDropout] auditor returned non-JSON for metric=%s; treating as correct",
                    metric_name,
                )
                return AuditJudgement(metric=metric_name, verdict="correct")
            return _judgement_from_finding(metric_name, parsed)
        except Exception as exc:  # noqa: BLE001 — keep team path resilient
            logger.warning(
                "[AgentDropout] auditor LLM failed for metric=%s: %s; treating as correct",
                metric_name,
                exc,
            )
            return AuditJudgement(metric=metric_name, verdict="correct")

    async def judge(
        self,
        *,
        task: str,
        agent_output: str,
        attempt_num: int = 1,
        role: str = "teammate",
        metrics: list[dict[str, Any]] | None = None,
    ) -> AuditResult:
        """Audit one contribution; return pass flag and optional rectify feedback.

        Mirrors AgentDropoutV2 ``Supervisor.judge`` pass-rate + feedback assembly.
        """
        if not self.prune_enabled:
            return AuditResult(passed=True, judgements=[], feedback=None)

        selected = metrics if metrics is not None else self._select_metrics()
        judgements: list[AuditJudgement] = []
        for metric in selected:
            judgements.append(
                await self._audit_one_metric(
                    task=task,
                    role=role,
                    agent_output=agent_output,
                    metric=metric,
                )
            )

        pass_count = sum(1 for j in judgements if j.is_correct)
        total = len(judgements)
        passed = (pass_count / total) >= self.pass_rate if total > 0 else True

        feedback: str | None = None
        if not passed:
            lines: list[str] = []
            for j in judgements:
                if j.is_correct:
                    continue
                reason = j.reasoning
                short_reason = (reason[:1000] + "...") if len(reason) > 1000 else reason
                lines.append(
                    f"- [{j.metric}]: {j.suggestion}\n"
                    f"  (Auditor's Note: {short_reason})"
                )
            if lines:
                feedback = build_rectify_feedback(attempt_num, lines)

        return AuditResult(
            passed=passed,
            judgements=judgements,
            feedback=feedback,
            metrics_used=list(selected),
            pass_count=pass_count,
            total_metrics=total,
        )

    @staticmethod
    def should_prune(audit: AuditResult, *, prune_enabled: bool = True) -> bool:
        """Return whether a finished contribution should be pruned from share history."""
        if not prune_enabled:
            return False
        return not audit.passed
