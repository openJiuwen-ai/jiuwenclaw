# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Cheap LAS-style gate for MACRO mode selection (no LLM)."""

from __future__ import annotations

import re
from typing import Any

from jiuwenswarm.agents.harness.macro_routing.schemas import MacroRoutingDecision, normalize_macro_mode

_GREETING_RE = re.compile(
    r"^(hi|hello|hey|thanks|thank you|你好|您好|谢谢)[!.?\s]*$",
    re.IGNORECASE,
)

_DEFAULT_TEAM_MARKERS = (
    "full stack",
    "full-stack",
    "multi-agent",
    "multi agent",
    "team of",
    "in parallel",
    "concurrently",
    "debate",
    "specialists",
    "frontend and backend",
    "microservice",
    "end to end",
    "end-to-end",
    "协作",
    "多角色",
    "并行",
    "辩论",
)
_DEFAULT_PLAN_MARKERS = (
    "plan",
    "design",
    "architecture",
    "tradeoff",
    "trade-off",
    "should we",
    "pros and cons",
    "compare options",
    "roadmap",
    "strategy",
    "规划",
    "方案",
    "架构",
    "权衡",
)
_DEFAULT_FAST_MARKERS = (
    "fix",
    "rename",
    "write file",
    "edit",
    "implement",
    "add test",
    "run",
    "install",
    "refactor this",
    "change",
    "update",
    "删除",
    "修复",
    "实现",
    "修改",
)


def _marker_hits(text: str, markers: tuple[str, ...] | list[str]) -> list[str]:
    lowered = text.lower()
    hits: list[str] = []
    for marker in markers:
        m = str(marker or "").strip().lower()
        if m and m in lowered:
            hits.append(m)
    return hits


def route_with_gate(
    query: str,
    *,
    config: dict[str, Any] | None = None,
) -> MacroRoutingDecision:
    """Score the query and pick a MACRO mode with a confidence estimate."""
    cfg = config or {}
    text = str(query or "").strip()
    team_markers = tuple(cfg.get("team_markers") or _DEFAULT_TEAM_MARKERS)
    plan_markers = tuple(cfg.get("plan_markers") or _DEFAULT_PLAN_MARKERS)
    fast_markers = tuple(cfg.get("fast_markers") or _DEFAULT_FAST_MARKERS)
    confidence_threshold = float(cfg.get("confidence_threshold", 0.72))

    features: dict[str, Any] = {
        "length": len(text),
        "is_greeting": bool(_GREETING_RE.match(text)) if text else False,
        "team_hits": _marker_hits(text, team_markers),
        "plan_hits": _marker_hits(text, plan_markers),
        "fast_hits": _marker_hits(text, fast_markers),
        "question_marks": text.count("?"),
    }

    if not text or features["is_greeting"]:
        return MacroRoutingDecision(
            mode="agent.plan",
            confidence=0.9,
            rationale="Greeting or empty query — stay in Planning Mode.",
            source="rules",
            features=features,
            gate_confident=True,
        )

    team_score = len(features["team_hits"]) * 2.0
    if features["length"] > 500:
        team_score += 1.0
    if features["length"] > 900:
        team_score += 1.0

    plan_score = len(features["plan_hits"]) * 1.5
    if features["question_marks"] >= 2:
        plan_score += 0.5
    if 120 <= features["length"] <= 500 and not features["fast_hits"]:
        plan_score += 0.4

    fast_score = len(features["fast_hits"]) * 1.5
    if features["length"] < 160 and features["fast_hits"]:
        fast_score += 1.0
    # Do NOT boost Performance for short messages without execution markers —
    # casual Q&A like "tell me the weather" should stay ambiguous → Planning bias.

    scores = {
        "team": team_score,
        "agent.plan": plan_score,
        "agent.fast": fast_score,
    }
    features["scores"] = dict(scores)
    best_mode = max(scores, key=scores.get)
    best = scores[best_mode]
    second = sorted(scores.values(), reverse=True)[1] if len(scores) > 1 else 0.0
    margin = best - second

    if best <= 0.2:
        # Ambiguous — prefer planning bias, low confidence.
        return MacroRoutingDecision(
            mode="agent.plan",
            confidence=0.45,
            rationale="Ambiguous intent — defaulting toward Planning Mode.",
            source="rules",
            features=features,
            gate_confident=False,
        )

    # Map raw score + margin into [0, 1] confidence.
    confidence = min(0.98, 0.45 + 0.15 * best + 0.2 * margin)
    gate_confident = confidence >= confidence_threshold and margin >= 0.5

    rationale_bits = {
        "team": "Multi-area / collaborative markers — Cluster Mode.",
        "agent.plan": "Design / tradeoff / planning markers — Planning Mode.",
        "agent.fast": "Direct execution markers — Performance Mode.",
    }
    return MacroRoutingDecision(
        mode=normalize_macro_mode(best_mode),
        confidence=float(confidence),
        rationale=rationale_bits[best_mode],
        source="rules",
        features=features,
        gate_confident=gate_confident,
    )


__all__ = ["route_with_gate"]
