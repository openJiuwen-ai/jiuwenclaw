# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verification-aware planning primitives for team / swarm deliverables.

This module ports the core idea of veriMAP (Verification-Aware Planning for
Multi-Agent Systems, https://github.com/megagonlabs/veriMAP) into JiuwenSwarm:
the planner emits per-subtask *acceptance criteria*, and an inline *verifier*
gates a member's deliverable against those criteria before it feeds downstream,
retrying a bounded number of times and escalating on exhaustion.

The pieces here are deliberately free of any rail / DeepAgent coupling so they
can be unit-tested with plain fakes:

* :class:`VerificationOutcome` — a single pass/fail judgement.
* :class:`Verifier` implementations — ``GenericVerifier`` (LLM judge, the
  paper's MAP-V) and ``StructuredVerifier`` (output enforcement + optional
  judge).
* :func:`extract_criteria` — pull the acceptance-criteria block out of a
  subtask prompt (how criteria propagate from Leader to teammate).
* :func:`run_verification_loop` — the pure bounded verify -> revise -> re-verify
  loop, returning the final output, outcome and whether it escalated.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional, Protocol

logger = logging.getLogger(__name__)

# Verification modes, mirroring veriMAP's ``--verification`` choices.
MODE_NONE = "none"
MODE_GENERIC = "generic"  # LLM judge against criteria (MAP-V).
MODE_STRUCTURED = "structured"  # Output enforcement + optional judge.
# ``vanilla`` is accepted as an alias of ``generic`` for CLI parity with veriMAP.
MODE_VANILLA = "vanilla"

_ENABLED_MODES = frozenset({MODE_GENERIC, MODE_STRUCTURED, MODE_VANILLA})

# Markers the Leader is instructed to emit; the teammate rail parses the block
# after the first marker as the subtask's acceptance criteria.
CRITERIA_MARKERS: tuple[str, ...] = (
    "Acceptance Criteria:",
    "验收标准：",
    "验收标准:",
)

# Optional callable that produces a revised deliverable given the failing one.
ReviseFn = Callable[[str, str, str], Awaitable[Optional[str]]]
# Optional async LLM judge: takes a prompt, returns the raw model text.
JudgeFn = Callable[[str], Awaitable[str]]


def normalize_mode(mode: Any) -> str:
    """Return a canonical verification mode string (``none`` when unknown)."""
    text = str(mode or MODE_NONE).strip().lower()
    if text == MODE_VANILLA:
        return MODE_GENERIC
    if text in {MODE_GENERIC, MODE_STRUCTURED, MODE_NONE}:
        return text
    return MODE_NONE


def is_enabled(mode: Any) -> bool:
    """Return whether *mode* turns the verification gate on."""
    return str(mode or "").strip().lower() in _ENABLED_MODES


@dataclass(frozen=True)
class VerificationOutcome:
    """The result of verifying one deliverable against its criteria.

    Attributes:
        passed: Whether the deliverable satisfies the criteria.
        reason: Short human-readable justification.
        mode: The verification mode that produced this outcome.
        verifiable: ``False`` when there was nothing to check (no criteria or no
            judge configured). A non-verifiable outcome never triggers a retry
            or an escalation.
        score: Optional numeric score in ``[0, 1]`` when the judge supplies one.
    """

    passed: bool
    reason: str = ""
    mode: str = MODE_NONE
    verifiable: bool = True
    score: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly report dict."""
        report: dict[str, Any] = {
            "passed": self.passed,
            "mode": self.mode,
            "verifiable": self.verifiable,
            "reason": self.reason,
        }
        if self.score is not None:
            report["score"] = self.score
        return report

    @classmethod
    def not_applicable(cls, reason: str, mode: str = MODE_NONE) -> "VerificationOutcome":
        """Build a non-verifiable (skipped) outcome that always passes."""
        return cls(passed=True, reason=reason, mode=mode, verifiable=False)


@dataclass
class VerificationLoopResult:
    """Aggregate result of :func:`run_verification_loop`."""

    output: str
    outcome: VerificationOutcome
    attempts: int = 0
    escalated: bool = False


def extract_criteria(text: Any) -> Optional[str]:
    """Extract the acceptance-criteria block from a subtask prompt.

    The Leader is prompted to append a block introduced by one of
    :data:`CRITERIA_MARKERS`. Everything after the first marker occurrence is
    treated as the criteria body.

    Args:
        text: The subtask prompt (any type; only ``str`` is inspected).

    Returns:
        The stripped criteria text, or ``None`` when no marker is present or the
        body is empty.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    for marker in CRITERIA_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            body = text[idx + len(marker):].strip()
            return body or None
    return None


class Verifier(Protocol):
    """Async verifier protocol: judge a deliverable against criteria."""

    async def verify(self, output: str, criteria: str) -> VerificationOutcome:
        """Return a :class:`VerificationOutcome` for *output* vs *criteria*."""
        ...


_JUDGE_PROMPT = (
    "You are a strict verifier for a multi-agent system. Decide whether the "
    "DELIVERABLE fully satisfies every point of the ACCEPTANCE CRITERIA.\n\n"
    "ACCEPTANCE CRITERIA:\n{criteria}\n\n"
    "DELIVERABLE:\n{output}\n\n"
    "Respond with ONLY a JSON object and nothing else: "
    '{{"passed": true or false, "reason": "one short sentence of decisive '
    'evidence"}}.'
)


def _parse_judge_response(raw: str) -> tuple[Optional[bool], str, Optional[float]]:
    """Parse a judge response into ``(passed, reason, score)`` leniently.

    Accepts either a ``passed`` boolean or a numeric ``score`` (>= 0.5 counts as
    passed). Tolerates surrounding prose by extracting the first JSON object.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None, "empty judge response", None
    text = raw.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start: end + 1]
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None, "unparseable judge response", None
    if not isinstance(payload, dict):
        return None, "judge response is not an object", None

    reason = payload.get("reason")
    reason_text = reason if isinstance(reason, str) else ""

    score: Optional[float] = None
    raw_score = payload.get("score")
    if isinstance(raw_score, (int, float)) and not isinstance(raw_score, bool):
        score = float(raw_score)

    passed = payload.get("passed")
    if isinstance(passed, bool):
        return passed, reason_text, score
    if score is not None:
        return score >= 0.5, reason_text, score
    return None, reason_text or "judge response missing 'passed'", score


class GenericVerifier:
    """LLM-judge verifier (veriMAP MAP-V generic verifier).

    Uses an async ``judge`` callable to score the deliverable against the
    criteria. When no judge is configured the outcome is *not applicable* so the
    gate degrades to a no-op instead of blocking delivery.
    """

    mode = MODE_GENERIC

    def __init__(self, judge: Optional[JudgeFn] = None) -> None:
        self._judge = judge

    async def verify(self, output: str, criteria: str) -> VerificationOutcome:
        if self._judge is None:
            return VerificationOutcome.not_applicable(
                "No verification judge is configured.", self.mode
            )
        prompt = _JUDGE_PROMPT.format(criteria=criteria, output=output)
        try:
            raw = await self._judge(prompt)
        except Exception as exc:  # noqa: BLE001 - fail soft, never block delivery
            logger.warning("GenericVerifier judge failed: %s", exc)
            return VerificationOutcome.not_applicable(
                f"Verification judge error: {exc}", self.mode
            )
        passed, reason, score = _parse_judge_response(raw)
        if passed is None:
            return VerificationOutcome.not_applicable(
                reason or "Verifier could not decide.", self.mode
            )
        return VerificationOutcome(
            passed=passed,
            reason=reason or ("criteria satisfied" if passed else "criteria not met"),
            mode=self.mode,
            score=score,
        )


class StructuredVerifier:
    """Structured verifier: output enforcement plus an optional semantic judge.

    Mirrors veriMAP's structured verification with ``--output_enforcement``:
    when ``require_json`` is set the deliverable must be a parseable JSON object
    or array; the semantic criteria are then delegated to the wrapped generic
    judge (when available).
    """

    mode = MODE_STRUCTURED

    def __init__(
        self,
        *,
        require_json: bool = True,
        judge: Optional[JudgeFn] = None,
    ) -> None:
        self._require_json = require_json
        self._generic = GenericVerifier(judge)

    async def verify(self, output: str, criteria: str) -> VerificationOutcome:
        if self._require_json:
            structural = self._check_json(output)
            if structural is not None:
                return structural
        return await self._generic.verify(output, criteria)

    def _check_json(self, output: str) -> Optional[VerificationOutcome]:
        """Return a failing outcome when JSON output enforcement is violated."""
        text = output.strip() if isinstance(output, str) else ""
        if not text:
            return VerificationOutcome(
                passed=False,
                reason="Structured output required but deliverable is empty.",
                mode=self.mode,
            )
        candidate = _strip_code_fence(text)
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            return VerificationOutcome(
                passed=False,
                reason="Structured output must be valid JSON (object or array).",
                mode=self.mode,
            )
        if not isinstance(parsed, (dict, list)):
            return VerificationOutcome(
                passed=False,
                reason="Structured output must be a JSON object or array.",
                mode=self.mode,
            )
        return None


def _strip_code_fence(text: str) -> str:
    """Strip a leading/trailing Markdown code fence if present."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped


def build_verifier(
    mode: str,
    *,
    output_enforcement: bool = False,
    judge: Optional[JudgeFn] = None,
) -> Optional[Verifier]:
    """Build the verifier for *mode*, or ``None`` when verification is off."""
    canonical = normalize_mode(mode)
    if canonical == MODE_GENERIC:
        return GenericVerifier(judge)
    if canonical == MODE_STRUCTURED:
        return StructuredVerifier(require_json=output_enforcement, judge=judge)
    return None


async def run_verification_loop(
    *,
    output: str,
    criteria: str,
    verifier: Verifier,
    revise: Optional[ReviseFn] = None,
    max_iterations: int = 2,
) -> VerificationLoopResult:
    """Run the bounded verify -> revise -> re-verify loop.

    Args:
        output: The deliverable to verify.
        criteria: The acceptance criteria to verify against.
        verifier: The verifier to apply.
        revise: Optional async callable ``(output, criteria, reason) -> str`` that
            produces a corrected deliverable. When ``None`` the loop verifies
            once and escalates on failure (no revision possible).
        max_iterations: Maximum number of revision attempts.

    Returns:
        A :class:`VerificationLoopResult` with the final (possibly revised)
        output, the last outcome, the number of revision attempts, and whether
        the deliverable still failed verification (``escalated``).
    """
    current = output
    outcome = await verifier.verify(current, criteria)
    attempts = 0
    budget = max(0, int(max_iterations))

    while (
        not outcome.passed
        and outcome.verifiable
        and revise is not None
        and attempts < budget
    ):
        attempts += 1
        try:
            revised = await revise(current, criteria, outcome.reason)
        except Exception as exc:  # noqa: BLE001 - revision is best-effort
            logger.warning("Verification revision failed: %s", exc)
            break
        if not revised or not str(revised).strip() or revised == current:
            break
        current = revised
        outcome = await verifier.verify(current, criteria)

    escalated = not outcome.passed and outcome.verifiable
    return VerificationLoopResult(
        output=current,
        outcome=outcome,
        attempts=attempts,
        escalated=escalated,
    )


__all__ = [
    "MODE_NONE",
    "MODE_GENERIC",
    "MODE_STRUCTURED",
    "MODE_VANILLA",
    "CRITERIA_MARKERS",
    "normalize_mode",
    "is_enabled",
    "VerificationOutcome",
    "VerificationLoopResult",
    "extract_criteria",
    "Verifier",
    "GenericVerifier",
    "StructuredVerifier",
    "build_verifier",
    "run_verification_loop",
]
