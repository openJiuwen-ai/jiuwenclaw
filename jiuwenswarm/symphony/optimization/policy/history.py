"""Structured optimization history — the textual policy-update mechanism.

No weights are trained; instead each iteration's outcome is recorded and then
compressed (by an LLM, mirroring
:class:`jiuwenswarm.symphony.experience.distiller.TraceDistiller`) into four buckets
— what improved reward, what reduced it, patterns to preserve, patterns to avoid —
which are fed back into the next round of prompt generation. That compressed text
*is* the "policy gradient": it steers the next candidates without any fine-tuning.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from jiuwenswarm.symphony.llm import llm_usage_context

LOGGER = logging.getLogger(__name__)


@dataclass
class HistoryEntry:
    iteration: int
    prompt: str
    reward: float
    observations: list[str] = field(default_factory=list)

    def render(self, *, prompt_limit: int = 600) -> str:
        prompt = self.prompt if len(self.prompt) <= prompt_limit else self.prompt[:prompt_limit] + "…"
        lines = [f"### Iteration {self.iteration} — reward {self.reward:.3f}", "Prompt:", prompt]
        if self.observations:
            lines.append("Observations:")
            lines.extend(f"- {o}" for o in self.observations)
        return "\n".join(lines)


@dataclass
class OptimizationHistory:
    entries: list[HistoryEntry] = field(default_factory=list)
    compressed: str = ""

    def add(self, entry: HistoryEntry) -> None:
        self.entries.append(entry)

    @property
    def best_reward(self) -> float:
        return max((e.reward for e in self.entries), default=0.0)

    @property
    def best_prompt(self) -> str:
        if not self.entries:
            return ""
        return max(self.entries, key=lambda e: e.reward).prompt

    def render_recent(self, limit: int = 4) -> str:
        recent = self.entries[-limit:]
        return "\n\n".join(e.render() for e in recent)

    def render(self) -> str:
        parts: list[str] = []
        if self.compressed:
            parts.append("## Distilled lessons so far\n" + self.compressed)
        if self.entries:
            parts.append("## Recent iterations\n" + self.render_recent())
        return "\n\n".join(parts)


_COMPRESS_SYSTEM = (
    "You distill lessons from prompt-optimization iterations into a compact policy "
    "update. Output only valid JSON."
)

_COMPRESS_PROMPT = """\
Below are optimization iterations, each with the system prompt tried, the scalar reward
it earned, and observations. Summarize what to do next.

{history}

Return ONLY valid JSON with these keys (each a list of short bullet strings):
{{
  "improved_reward": ["patterns that raised reward"],
  "reduced_reward": ["patterns that lowered reward"],
  "preserve": ["patterns to keep in future prompts"],
  "avoid": ["patterns to avoid in future prompts"]
}}
"""


class HistoryCompressor:
    """Compress raw history into a short textual policy update (LLM-backed)."""

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    async def compress(self, history: OptimizationHistory) -> str:
        if not history.entries:
            return ""
        if self._client is None:
            return self._heuristic(history)
        prompt = _COMPRESS_PROMPT.format(history=history.render_recent(limit=8))
        try:
            with llm_usage_context("optimization", "history_compress"):
                raw = await self._client.complete_json_async(
                    system_prompt=_COMPRESS_SYSTEM,
                    user_content=prompt,
                    error_context="Prompt optimizer history compressor",
                    request_overrides={"extra_body": {"thinking": {"type": "disabled"}}},
                )
            data = json.loads(raw)
            return self._render_buckets(data)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("HistoryCompressor: LLM compression failed, using heuristic: %s", exc)
            return self._heuristic(history)

    @staticmethod
    def _render_buckets(data: dict[str, Any]) -> str:
        labels = [
            ("improved_reward", "What improved reward"),
            ("reduced_reward", "What reduced reward"),
            ("preserve", "Patterns to preserve"),
            ("avoid", "Patterns to avoid"),
        ]
        lines: list[str] = []
        for key, label in labels:
            items = data.get(key) or []
            if not isinstance(items, list) or not items:
                continue
            lines.append(f"**{label}:**")
            lines.extend(f"- {str(item).strip()}" for item in items if str(item).strip())
        return "\n".join(lines)

    @staticmethod
    def _heuristic(history: OptimizationHistory) -> str:
        ordered = sorted(history.entries, key=lambda e: e.reward)
        lines: list[str] = []
        if ordered:
            best = ordered[-1]
            worst = ordered[0]
            lines.append(f"**Best so far (reward {best.reward:.3f})** — preserve its structure.")
            for obs in best.observations[:3]:
                lines.append(f"- keep: {obs}")
            if worst is not best:
                lines.append(f"**Weakest (reward {worst.reward:.3f})** — avoid its choices.")
                for obs in worst.observations[:3]:
                    lines.append(f"- avoid: {obs}")
        return "\n".join(lines)


__all__ = ["HistoryEntry", "OptimizationHistory", "HistoryCompressor"]
