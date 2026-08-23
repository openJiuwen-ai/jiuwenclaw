# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A small registry so :class:`QualityGateRail` can be built by name.

The rail takes a ``scorer`` callable by dependency injection, which is ideal when
a host constructs the rail directly. When the rail is built from a serializable
spec (the manifest / config path), the callable cannot live in ``params``; the
spec carries a scorer *name* instead, which this registry resolves to a callable.

A few domain-neutral scorers ship here so the mechanism is usable out of the box
and testable; real, domain-specific scorers (e.g. an LLM reviewer) are registered
by the host / strategy layer.
"""

from __future__ import annotations

from typing import Dict

from jiuwenswarm.agents.harness.common.rails.quality_gate_rail import GateVerdict, Scorer

_REGISTRY: Dict[str, Scorer] = {}


def register_scorer(name: str, scorer: Scorer) -> None:
    """Register *scorer* under *name* (overwrites an existing entry)."""
    if not callable(scorer):
        raise TypeError("scorer must be callable")
    _REGISTRY[str(name)] = scorer


def resolve_scorer(name: str) -> Scorer:
    """Return the scorer registered under *name*, or raise ``KeyError``."""
    try:
        return _REGISTRY[str(name)]
    except KeyError as exc:
        raise KeyError(
            f"no scorer registered as {name!r}; register one via "
            f"register_scorer({name!r}, ...). Known: {sorted(_REGISTRY)}"
        ) from exc


def has_scorer(name: str) -> bool:
    return str(name) in _REGISTRY


# -- domain-neutral built-in scorers --------------------------------------

def _always_pass(text: str, context: dict) -> GateVerdict:
    return GateVerdict(score=1.0, passed=True, feedback="", details={"builtin": "always_pass"})


def _always_fail(text: str, context: dict) -> GateVerdict:
    return GateVerdict(score=0.0, passed=False, feedback="Output rejected by always_fail scorer.",
                       details={"builtin": "always_fail"})


def _min_length(text: str, context: dict) -> GateVerdict:
    """Score by output length: 1.0 if >= 200 chars, else proportional."""
    n = len(text or "")
    score = min(1.0, n / 200.0)
    return GateVerdict(
        score=score,
        passed=score >= 1.0,
        feedback="Produce a longer, more complete answer (target >= 200 chars).",
        details={"builtin": "min_length", "chars": n},
    )


register_scorer("always_pass", _always_pass)
register_scorer("always_fail", _always_fail)
register_scorer("min_length", _min_length)


__all__ = ["register_scorer", "resolve_scorer", "has_scorer"]
