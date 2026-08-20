# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Correlation-aware acceptance gate for self-graded verdicts.

An agent framework that evolves its own skills has to decide, repeatedly,
whether to keep a change. The cheap way is to ask a model: *did this work?* The
problem is that the model answering shares a blind spot with the model that
produced the thing being judged -- same training data, same failure modes, same
sense of what a good answer looks like. A "pass" from that arrangement is not
worth its face value, and using a *different* model only partly helps: family
effects and a shared familiarity bias leave residual correlation behind.

This gate prices that in. Given how correlated the judge is with the generator
(``contamination_rho``) and how much residual error is tolerable (``alpha``), it
returns the number of *independent external checks* the verdict still needs
before it may be accepted.

The risk model is one line: each independent anchor is a fresh look that
multiplies the residual risk by rho again, so with k anchors plus the
(contaminated) self-grade the residual is ``rho**(k+1)``. Bringing that under
alpha requires ``k+1 >= log(alpha)/log(rho)`` checks.

**This is a conservative routing policy for verification effort, not a proven
statistical bound.** It is stated that way here because a gate that overstates
its own guarantee is the exact failure it exists to prevent.

Two asymmetries do the real work:

* **Negative verdicts pass straight through.** Contamination inflates a model's
  acceptance of its *own* favoured outputs; it does not manufacture rejections.
  Keeping a change out is the safe direction and needs no anchor.
* **Unknown provenance is treated as self-graded.** If the generator or judge is
  not identified, the gate assumes the worst case rather than the convenient one.

Deterministic: no model call, so the gate cannot inherit the bias it is measuring.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Model families that share a lineage closely enough for a cross-checkpoint
# judgement to stay substantially correlated.
_FAMILIES = (
    "gpt", "claude", "gemini", "llama", "qwen", "mistral", "deepseek", "gemma",
)

# How much of the contamination survives when the judge differs from the
# generator. Same family means different checkpoints of shared lineage; a
# genuinely different model still shares a familiarity bias, so neither
# decorrelates fully. Both are judgement calls, deliberately conservative.
_RHO_SAME_FAMILY = 0.7
_RHO_CROSS_FAMILY = 0.5

_POSITIVE_VERDICTS = frozenset(
    {"", "open", "accept", "pass", "novel", "supported", "true", "yes", "approve"}
)


@dataclass(frozen=True)
class GateDecision:
    """What the gate decided, and every input that produced it."""

    action: str                       # accept | require_external | abstain | accept_verdict
    self_graded: bool
    regime: str                       # self_graded | cross_model
    effective_rho: float
    residual_risk: float
    required_external: int
    external_anchors: int
    alpha: float
    trust_adjusted_confidence: float
    rationale: str

    def render(self) -> str:
        return (
            f"{self.action} [{self.regime}] rho_eff={self.effective_rho:.3f} "
            f"residual={self.residual_risk:.4f} vs alpha={self.alpha:.3f} "
            f"anchors={self.external_anchors} need={self.required_external} "
            f"-- {self.rationale}"
        )


def _family(model: str) -> str:
    m = (model or "").strip().lower()
    for fam in _FAMILIES:
        if fam in m:
            return fam
    return m


def _regime(generator_model: str, judge_model: str, rho: float,
            same_lineage: bool | None) -> tuple[bool, str, float]:
    gen, jud = (generator_model or "").strip().lower(), (judge_model or "").strip().lower()
    if same_lineage is True or (gen and jud and gen == jud):
        return True, "self_graded", rho
    if not (gen and jud):
        # Unknown provenance: assume the worst case, never the convenient one.
        return True, "self_graded", rho
    if _family(gen) == _family(jud):
        return False, "cross_model", rho * _RHO_SAME_FAMILY
    return False, "cross_model", rho * _RHO_CROSS_FAMILY


def evaluate(
    *,
    verdict: str = "",
    judge_confidence: float = 0.5,
    generator_model: str = "",
    judge_model: str = "",
    external_anchors: int = 0,
    contamination_rho: float = 0.7,
    alpha: float = 0.1,
    same_lineage: bool | None = None,
) -> GateDecision:
    """Decide whether a self-graded verdict may be accepted as it stands.

    Args:
        verdict: the judgement being gated; anything outside the positive set is
            treated as a rejection and passes through.
        judge_confidence: the judge's own confidence, in [0, 1]. Used only to
            report a trust-adjusted figure; it never relaxes the anchor
            requirement, because a contaminated judge is confident for the same
            reason it is wrong.
        generator_model / judge_model: model identifiers, used to detect whether
            the judgement is genuinely independent.
        external_anchors: independent checks already performed (a corpus lookup,
            a deterministic validator, a human spot-check).
        contamination_rho: measured or assumed generator/judge correlation.
        alpha: residual error budget.
        same_lineage: force the self-graded regime when provenance is known but
            the model strings do not match.
    """
    rho = min(max(float(contamination_rho), 0.0), 0.999)
    conf = min(max(float(judge_confidence), 0.0), 1.0)
    anchors = max(int(external_anchors), 0)
    a = min(max(float(alpha), 1e-6), 0.999)

    self_graded, regime, eff_rho = _regime(generator_model, judge_model, rho, same_lineage)

    if (verdict or "").strip().lower() not in _POSITIVE_VERDICTS:
        return GateDecision(
            action="accept_verdict", self_graded=self_graded, regime=regime,
            effective_rho=round(eff_rho, 4), residual_risk=0.0,
            required_external=0, external_anchors=anchors, alpha=round(a, 4),
            trust_adjusted_confidence=round(conf, 3),
            rationale="negative verdict is in the safe direction; contamination "
                      "inflates self-favoured accepts, not rejects",
        )

    if eff_rho <= 0.0:
        residual, needed_total = 0.0, 0
    else:
        residual = eff_rho ** (anchors + 1)
        needed_total = max(0, math.ceil(math.log(a) / math.log(eff_rho)) - 1)
    required = max(0, needed_total - anchors)

    if required <= 0:
        action = "accept"
        why = (f"{anchors} external anchor(s) bring residual risk {residual:.4f} "
               f"within the budget {a:.3f}")
    elif anchors == 0 and self_graded:
        # Nothing but the contaminated judgement itself. There is no confidence
        # level at which that becomes evidence, so the gate declines to rule.
        action = "abstain"
        why = (f"self-graded with no external anchor: residual risk {residual:.4f} "
               f"exceeds {a:.3f} and no amount of judge confidence substitutes "
               f"for an independent check")
    else:
        action = "require_external"
        why = (f"{required} more independent check(s) needed: residual risk "
               f"{residual:.4f} exceeds the budget {a:.3f}")

    return GateDecision(
        action=action, self_graded=self_graded, regime=regime,
        effective_rho=round(eff_rho, 4), residual_risk=round(residual, 6),
        required_external=required, external_anchors=anchors, alpha=round(a, 4),
        trust_adjusted_confidence=round(conf * (1.0 - residual), 3),
        rationale=why,
    )


__all__ = ["GateDecision", "evaluate"]
