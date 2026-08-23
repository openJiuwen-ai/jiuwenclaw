# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Stage-boundary quality gate for the DeepAgent task loop.

``QualityGateRail`` scores the output of each outer task-loop iteration and
decides whether the round passes, should be revised and retried, or should be
rejected. It follows the same structural pattern as openjiuwen's
``SwarmflowBudgetRail`` (``openjiuwen/agent_teams/workflow/backends/budget_rail.py``):
a rail that observes a lifecycle boundary, compares a measured quantity against a
ceiling, and records the outcome for the host to act on.

The rail is deliberately mechanism-only. It contains **no** scoring logic: the
caller injects a ``scorer`` callable, so the same rail serves an LLM reviewer, a
rule-based checker, or a stub in tests. This keeps the class clean enough to
upstream while every domain-specific policy stays outside it.

Control contract
----------------
The rail records a :class:`GateVerdict` for each judged round and exposes the
decision via ``last_verdict`` / ``verdicts`` / ``last_decision`` / ``last_payload``
on the rail instance. A host runner reads these fields and drives the retry loop
deterministically — revise-and-retry or reject — since ``ctx.request_force_finish``
is a no-op inside ``after_task_iteration`` under the current agent-core and
cannot itself stop the round:

* **pass** — writes the verdict into ``ctx.extra`` and returns; the round
  completes normally.
* **revise** — rewrites ``ctx.inputs.query`` with the reviewer feedback (the
  documented query-injection field), records a ``needs_revision`` decision on
  ``last_decision`` / ``last_payload`` for the host to read, and also calls
  ``request_force_finish`` with the same payload as a best-effort,
  forward-compatible signal so the host re-invokes the stage with the revised
  query.
* **reject** — records a ``rejected`` decision on ``last_decision`` /
  ``last_payload`` for the host to read, and also calls
  ``request_force_finish`` with the same payload as a best-effort,
  forward-compatible signal.

Why the host drives the retry rather than the rail forcing an in-place redo:
rewriting ``ctx.inputs.query`` inside ``after_task_iteration`` does not by itself
start another task-loop iteration (the query for the finished round was already
consumed, and loop continuation is governed by the coordinator / follow-up
queue). Mapping one stage attempt to one ``invoke`` matches how a SwarmFlow
worker runs a stage, and keeps correctness independent of loop-continuation
internals.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail

logger = logging.getLogger(__name__)

#: Decision the gate made for one judged round.
GateDecision = Literal["pass", "revise", "reject"]

#: What to do when a round fails: rewrite-and-retry, or reject immediately.
OnFail = Literal["revise", "reject"]


@dataclass
class GateVerdict:
    """The outcome of scoring one round's output.

    Attributes:
        score: The reviewer's numeric score for the produced text.
        passed: The scorer's own view of whether the output is acceptable.
            Advisory only: the gate decision uses ``score >= threshold`` as the
            authority (see :meth:`QualityGateRail.after_task_iteration`), so the
            pass/fail boundary stays in one place. Recorded for logs / analysis.
        feedback: Concrete, actionable revision instructions. Injected verbatim
            into the next attempt's query on the ``revise`` path.
        details: Free-form scorer diagnostics (sub-scores, matched rules, ...).
    """

    score: float
    passed: bool
    feedback: str = ""
    details: dict = field(default_factory=dict)


#: A scorer maps ``(produced_text, context)`` to a :class:`GateVerdict`.
#: ``context`` carries ``gate``, ``attempt``, ``query``, ``result`` and ``extra``.
Scorer = Callable[[str, dict], GateVerdict]


class QualityGateRail(DeepAgentRail):
    """Score each task-loop round; pass, revise-and-retry, or reject.

    Args:
        scorer: Injected callable that scores the produced text. The rail holds
            no scoring logic of its own.
        threshold: Minimum score to pass. The gate decision is
            ``verdict.score >= threshold``.
        max_retries: How many ``revise`` cycles are allowed before the gate
            rejects. ``0`` means the first failure rejects immediately (when
            ``on_fail="revise"``).
        on_fail: ``"revise"`` rewrites the query and asks the host to retry;
            ``"reject"`` rejects on the first failure without retrying.
        gate_name: Label used in logs, verdict payloads and the ``ctx.extra`` key.
        log_path: Optional path for JSON-lines decision logs. Parent directories
            are created on demand.
        extra_key: ``ctx.extra`` key under which a passing verdict is published
            for downstream rails / stages. Defaults to ``"quality_gate"``.

    Attributes:
        verdicts: Every :class:`GateVerdict` produced, in order.
        last_verdict: The most recent verdict, or ``None`` before the first round.
        last_decision: The most recent :data:`GateDecision`, or ``None``.
        last_payload: The most recent ``force_finish`` payload (revise/reject), or
            ``None`` on a pass.
    """

    #: Runs late at the round boundary (lower priority = later); a gate should
    #: judge after content-shaping rails have finished with the round.
    priority: int = 40

    def __init__(
        self,
        scorer: Scorer,
        *,
        threshold: float,
        max_retries: int = 1,
        on_fail: OnFail = "revise",
        gate_name: str = "quality_gate",
        log_path: Optional[str | Path] = None,
        extra_key: str = "quality_gate",
    ) -> None:
        super().__init__()
        if not callable(scorer):
            raise TypeError("scorer must be callable")
        self._scorer = scorer
        self.threshold = float(threshold)
        self.max_retries = int(max_retries)
        self.on_fail: OnFail = on_fail
        self.gate_name = gate_name
        self._log_path = Path(log_path) if log_path else None
        self._extra_key = extra_key

        self._retries_used = 0
        self.verdicts: list[GateVerdict] = []
        self.last_verdict: Optional[GateVerdict] = None
        self.last_decision: Optional[GateDecision] = None
        self.last_payload: Optional[dict] = None

    # -- lifecycle ---------------------------------------------------------

    def reset(self) -> None:
        """Clear per-run state so one instance can be reused across runs."""
        self._retries_used = 0
        self.verdicts = []
        self.last_verdict = None
        self.last_decision = None
        self.last_payload = None

    async def after_task_iteration(self, ctx: AgentCallbackContext) -> None:
        """Score the finished round and act on the verdict."""
        inputs = ctx.inputs
        produced = self._extract_output(inputs)
        original_query = self._extract_query(inputs)

        attempt = self._retries_used  # prior revise cycles for this stage
        context = {
            "gate": self.gate_name,
            "attempt": attempt,
            "threshold": self.threshold,
            "query": original_query,
            "result": getattr(inputs, "result", None),
            "extra": getattr(ctx, "extra", None),
        }

        try:
            verdict = self._scorer(produced, context)
        except Exception:  # a broken scorer must not crash the agent loop
            logger.exception("[quality_gate:%s] scorer raised; treating as reject", self.gate_name)
            verdict = GateVerdict(score=0.0, passed=False, feedback="scorer error", details={"scorer_error": True})

        passed = verdict.score >= self.threshold
        self.verdicts.append(verdict)
        self.last_verdict = verdict

        if passed:
            self.last_decision = "pass"
            self.last_payload = None
            self._publish_pass(ctx, verdict)
            self._log(attempt, verdict, "pass", None)
            return

        # Failed the threshold. Decide revise vs reject.
        can_revise = self.on_fail == "revise" and self._retries_used < self.max_retries
        if can_revise:
            self._retries_used += 1
            revised_query = self._compose_revised_query(original_query, verdict.feedback, self._retries_used)
            # Honour the documented query-injection field; also expose on extra.
            try:
                inputs.query = revised_query
            except Exception:  # inputs may not carry a query field on error rounds
                pass
            self._write_extra(ctx, self._extra_key + "_revision", {
                "gate": self.gate_name,
                "attempt": self._retries_used,
                "feedback": verdict.feedback,
                "revised_query": revised_query,
            })
            payload = {
                "status": "needs_revision",
                "gate": self.gate_name,
                "score": verdict.score,
                "threshold": self.threshold,
                "attempt": self._retries_used,
                "max_retries": self.max_retries,
                "feedback": verdict.feedback,
                "revised_query": revised_query,
            }
            self.last_decision = "revise"
            self.last_payload = payload
            self._log(self._retries_used, verdict, "revise", payload)
            # Best-effort only: a no-op inside after_task_iteration under the current
            # agent-core; the host runner drives revise/reject via last_decision.
            ctx.request_force_finish(payload)
            return

        reason = "retries_exhausted" if self.on_fail == "revise" else "on_fail_reject"
        payload = {
            "status": "rejected",
            "gate": self.gate_name,
            "score": verdict.score,
            "threshold": self.threshold,
            "attempt": attempt,
            "reason": reason,
            "feedback": verdict.feedback,
        }
        self.last_decision = "reject"
        self.last_payload = payload
        self._log(attempt, verdict, "reject", payload)
        # Best-effort only: a no-op inside after_task_iteration under the current
            # agent-core; the host runner drives revise/reject via last_decision.
        ctx.request_force_finish(payload)

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _extract_output(inputs: Any) -> str:
        """Pull the produced text from a task-iteration result.

        The executor sets ``TaskIterationInputs.result`` to the inner ReAct
        invoke result, whose human-facing answer lives under ``output``.
        """
        result = getattr(inputs, "result", None)
        if isinstance(result, dict):
            return str(result.get("output", "") or "")
        if isinstance(result, str):
            return result
        return ""

    @staticmethod
    def _extract_query(inputs: Any) -> str:
        query = getattr(inputs, "query", None)
        return str(query or "")

    def _compose_revised_query(self, original_query: str, feedback: str, attempt: int) -> str:
        """Build the retry query by appending the reviewer's revision request."""
        feedback = (feedback or "").strip() or "The previous output did not meet the quality bar; revise it."
        base = (original_query or "").strip()
        return (
            f"{base}\n\n"
            f"[Quality gate '{self.gate_name}' — revision {attempt}/{self.max_retries}]\n"
            f"The previous attempt did not pass. Address the following and produce an improved version:\n"
            f"{feedback}"
        )

    def _publish_pass(self, ctx: AgentCallbackContext, verdict: GateVerdict) -> None:
        payload = {
            "gate": self.gate_name,
            "score": verdict.score,
            "threshold": self.threshold,
            "passed": True,
            "details": verdict.details,
        }
        self._write_extra(ctx, self._extra_key, payload)

    @staticmethod
    def _write_extra(ctx: AgentCallbackContext, key: str, value: Any) -> None:
        extra = getattr(ctx, "extra", None)
        if isinstance(extra, dict):
            extra[key] = value

    def _log(self, attempt: int, verdict: GateVerdict, decision: GateDecision, payload: Optional[dict]) -> None:
        record = {
            "ts": time.time(),
            "gate": self.gate_name,
            "attempt": attempt,
            "decision": decision,
            "score": verdict.score,
            "threshold": self.threshold,
            "scorer_passed": verdict.passed,
            "feedback": verdict.feedback,
            "details": verdict.details,
            "payload": payload,
        }
        logger.info(
            "[quality_gate:%s] decision=%s score=%.4f threshold=%.4f attempt=%d",
            self.gate_name, decision, verdict.score, self.threshold, attempt,
        )
        if self._log_path is None:
            return
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            logger.exception("[quality_gate:%s] failed to write decision log", self.gate_name)


__all__ = ["GateVerdict", "GateDecision", "OnFail", "Scorer", "QualityGateRail"]
