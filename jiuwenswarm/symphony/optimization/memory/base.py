"""Prompt memory interface + a dependency-light JSONL default.

Stores optimization outcomes (prompt, reward, task characteristics, metrics,
observations, timestamp) and retrieves similar prior optimizations to warm-start
the policy and to feed the runtime rail. The FAISS-backed
:class:`ExperienceBankPromptMemory` (see :mod:`.experience_backend`) plugs into the
same interface for real semantic retrieval; :class:`JsonlPromptMemory` is the
fallback when embeddings are not configured.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from abc import ABC, abstractmethod
from pathlib import Path

from jiuwenswarm.symphony.optimization.models import PromptRecord, TaskSpec

LOGGER = logging.getLogger(__name__)

_WORD = re.compile(r"[\w]+", re.UNICODE)


class PromptMemory(ABC):
    """Persistent store of prior optimizations with similarity retrieval."""

    @abstractmethod
    def add(self, record: PromptRecord) -> None:
        ...

    @abstractmethod
    def search_similar(self, task: TaskSpec, top_k: int = 3) -> list[PromptRecord]:
        ...


class NullPromptMemory(PromptMemory):
    """No-op memory (used when ``memory_enabled=false``)."""

    def add(self, record: PromptRecord) -> None:  # noqa: D401
        return None

    def search_similar(self, task: TaskSpec, top_k: int = 3) -> list[PromptRecord]:
        return []


class JsonlPromptMemory(PromptMemory):
    """Append-only JSONL store with cheap lexical (token-overlap) retrieval.

    No embedding backend required. Good enough to warm-start the policy from past
    runs on similar objectives; upgrade to :class:`ExperienceBankPromptMemory` when
    an embedding endpoint is configured.
    """

    def __init__(self, directory: str | Path) -> None:
        self._dir = Path(directory)
        self._path = self._dir / "records.jsonl"
        self._lock = threading.Lock()
        self._records: list[PromptRecord] = []
        self._load()

    def _load(self) -> None:
        if not self._path.is_file():
            return
        try:
            for line in self._path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    self._records.append(PromptRecord.from_dict(json.loads(line)))
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("JsonlPromptMemory: failed to load %s: %s", self._path, exc)

    def add(self, record: PromptRecord) -> None:
        with self._lock:
            self._records.append(record)
            self._dir.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

    def search_similar(self, task: TaskSpec, top_k: int = 3) -> list[PromptRecord]:
        query_tokens = _tokens(task.characteristics)
        if not query_tokens or not self._records:
            # fall back to best-reward records so we still warm-start
            return sorted(self._records, key=lambda r: r.reward, reverse=True)[:top_k]
        scored: list[tuple[float, PromptRecord]] = []
        for record in self._records:
            overlap = _jaccard(query_tokens, _tokens(record.task_characteristics or record.objective))
            if overlap > 0:
                # blend similarity with reward so we surface prompts that were both
                # relevant and effective
                scored.append((overlap * (0.5 + 0.5 * record.reward), record))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:top_k]]


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _WORD.findall(text or "")}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


__all__ = ["PromptMemory", "NullPromptMemory", "JsonlPromptMemory"]
