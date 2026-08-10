"""LLM-based semantic drift judge."""

from __future__ import annotations

import json
import logging
from typing import Any

from jiuwenswarm.symphony.llm import llm_usage_context
from jiuwenswarm.symphony.optimization.drift.base import DriftJudge
from jiuwenswarm.symphony.optimization.models import PromptCandidate

LOGGER = logging.getLogger(__name__)

_SYSTEM = (
    "You measure whether an optimized system prompt still serves the original task "
    "objective. Output only valid JSON."
)

_PROMPT = """\
Original task objective:
{objective}

Candidate system prompt:
{prompt}

Does the candidate system prompt still faithfully pursue the SAME objective, or has it
drifted toward a different task, added unrelated goals, or narrowed/broadened the scope?

Return ONLY valid JSON:
{{"deviation": <float 0..1>, "reason": "<short reason>"}}
where 0.0 means identical intent and 1.0 means a completely different task.
"""


class LLMDriftJudge(DriftJudge):
    """Approximate KL divergence from the objective with an LLM judge."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def deviation(self, original_objective: str, candidate: PromptCandidate) -> float:
        if not (candidate.prompt or "").strip():
            return 1.0
        prompt = _PROMPT.format(objective=original_objective, prompt=candidate.prompt)
        try:
            with llm_usage_context("optimization", "drift_judge"):
                raw = await self._client.complete_json_async(
                    system_prompt=_SYSTEM,
                    user_content=prompt,
                    error_context="Prompt optimizer drift judge",
                    request_overrides={"extra_body": {"thinking": {"type": "disabled"}}},
                )
            data = json.loads(raw)
            value = float(data.get("deviation", 0.0))
            return max(0.0, min(1.0, value))
        except Exception as exc:  # noqa: BLE001
            # Fail safe: assume no drift rather than penalize a candidate for a judge outage.
            LOGGER.warning("LLMDriftJudge: judge failed, assuming 0 drift: %s", exc)
            return 0.0


__all__ = ["LLMDriftJudge"]
