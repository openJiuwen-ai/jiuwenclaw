# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Message scoreboard for prune-before-share decisions (AgentDropoutV2 idea)."""

from __future__ import annotations

from typing import Any, Iterable

from jiuwenswarm.agents.dropout.types import AuditJudgement, ScoreboardEntry


class ContributionScoreboard:
    """Tracks audited contributions and filters pruned ones from shared history."""

    def __init__(self, *, prune_enabled: bool = True) -> None:
        self.prune_enabled = prune_enabled
        self._entries: dict[str, ScoreboardEntry] = {}

    def reset(self) -> None:
        self._entries.clear()

    def update(
        self,
        *,
        message_id: str,
        content: str,
        source: str,
        judgements: list[AuditJudgement],
        is_pruned: bool,
    ) -> ScoreboardEntry:
        entry = ScoreboardEntry(
            message_id=message_id,
            content=content,
            source=source,
            judgements=list(judgements),
            is_pruned=bool(is_pruned) if self.prune_enabled else False,
        )
        self._entries[message_id] = entry
        return entry

    def is_pruned(self, message_id: str) -> bool:
        entry = self._entries.get(message_id)
        if entry is None:
            return False
        return bool(entry.is_pruned) if self.prune_enabled else False

    def get(self, message_id: str) -> ScoreboardEntry | None:
        return self._entries.get(message_id)

    def prune_messages(
        self,
        messages: Iterable[dict[str, Any]],
        *,
        id_key: str = "id",
        source_key: str = "source",
    ) -> list[dict[str, Any]]:
        """Return messages that are not pruned (ADv2 ``prune_info``).

        Messages without a scoreboard entry are kept, matching ADv2 behavior for
        non-audited / user messages.
        """
        if not self.prune_enabled:
            return list(messages)

        kept: list[dict[str, Any]] = []
        for msg in messages:
            message_id = str(msg.get(id_key, "") or "")
            if message_id and message_id in self._entries:
                if not self._entries[message_id].is_pruned:
                    kept.append(msg)
                continue
            kept.append(msg)
        return kept

    def get_messages_above_threshold(self) -> list[ScoreboardEntry]:
        """Return non-pruned scoreboard entries (ADv2 naming preserved)."""
        if not self.prune_enabled:
            return list(self._entries.values())
        return [entry for entry in self._entries.values() if not entry.is_pruned]

    def dump(self) -> dict[str, dict[str, Any]]:
        return {
            message_id: {
                "message_id": entry.message_id,
                "content": entry.content,
                "source": entry.source,
                "judgements": [
                    {
                        "metric": j.metric,
                        "verdict": j.verdict,
                        "evidence_quote": j.evidence_quote,
                        "reasoning": j.reasoning,
                        "suggestion": j.suggestion,
                        "impact": j.impact,
                    }
                    for j in entry.judgements
                ],
                "is_pruned": entry.is_pruned,
            }
            for message_id, entry in self._entries.items()
        }
