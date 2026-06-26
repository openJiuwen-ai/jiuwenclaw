"""LLM-based trace processor: judge whether the skills used in a trace were correctly selected."""

from __future__ import annotations

import json
import logging
from typing import Any

from .models import TraceRecord

LOGGER = logging.getLogger(__name__)


class TraceEvaluator:
    """Judge whether the skills used in each TraceRecord were correctly selected via LLM.

    When ``messages`` are available, uses an enhanced prompt that includes
    the full interaction process and skill selection context. Otherwise, falls back
    to the simple query+result prompt.

    ``success=True`` means the skill selection was correct — these traces are
    used to build experience patterns. ``success=False`` means the skills were
    incorrectly chosen or the execution failed — these traces are excluded from
    the experience bank.

    Usage::

        processor = TraceEvaluator(llm_client=openai_client, llm_model="qwen3-32b")
        records = parse_session("session_abc")
        processor.process(records)
        # records[0].success / .error_type / .error_detail are now filled
    """

    def __init__(
            self,
            llm_client: Any | None = None,
            llm_model: str = "",
    ) -> None:
        self._llm_model = str(llm_model or "").strip()
        self._llm = llm_client if llm_client is not None else None

    def process(self, records: list[TraceRecord]) -> list[TraceRecord]:
        """Judge skill correctness for each record, skip those with empty skills, return a new processed list."""
        result = []
        for record in records:
            if not record.skills:
                continue
            self._judge_one(record)
            result.append(record)
        return result

    def _judge_one(self, record: TraceRecord) -> None:
        pass


__all__ = ["TraceEvaluator"]