"""Bounded process-local stores used by Celia prompt and ingest flows."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field

from .sanitizer import sanitize_prompt_text


def make_store_key(tenant_id: str, user_id: str, conversation_id: str) -> str:
    return f"{tenant_id}:{user_id}:{conversation_id}"


@dataclass
class _PromptEntry:
    values: list[str] = field(default_factory=list)


class CeliaRuntimeStore:
    def __init__(self, max_entries: int = 1024) -> None:
        self._max_entries = max_entries
        self._prompt: OrderedDict[str, _PromptEntry] = OrderedDict()
        self._urgent: set[str] = set()
        self._served_l1: OrderedDict[str, set[str]] = OrderedDict()
        self._rounds: OrderedDict[str, int] = OrderedDict()

    def append_prompt(self, key: str, text: str) -> None:
        clean = sanitize_prompt_text(text)
        if not clean:
            return
        entry = self._prompt.setdefault(key, _PromptEntry())
        self._prompt.move_to_end(key)
        if len(entry.values) >= 16:
            entry.values.pop(0)
        entry.values.append(clean.encode("utf-8")[:800].decode("utf-8", errors="ignore"))
        while sum(len(item.encode("utf-8")) for item in entry.values) > 4000:
            entry.values.pop(0)
        self._trim()

    def prompt_values(self, key: str) -> list[str]:
        entry = self._prompt.get(key)
        if entry is None:
            return []
        self._prompt.move_to_end(key)
        return list(entry.values)

    def mark_urgent(self, key: str) -> None:
        self._urgent.add(key)

    def consume_urgent(self, key: str) -> bool:
        if key not in self._urgent:
            return False
        self._urgent.discard(key)
        return True

    def record_l1_paths(self, key: str, paths: list[str]) -> None:
        if not paths:
            return
        self._served_l1.setdefault(key, set()).update(paths)
        self._served_l1.move_to_end(key)
        self._trim()

    def served_l1_paths(self, key: str) -> list[str]:
        paths = self._served_l1.get(key, set())
        if key in self._served_l1:
            self._served_l1.move_to_end(key, last=True)
        return sorted(paths)

    def clear_session(self, key: str) -> None:
        self._prompt.pop(key, None)
        self._urgent.discard(key)
        self._served_l1.pop(key, None)
        self._rounds.pop(key, None)

    def next_round(self, key: str) -> int:
        value = self._rounds.get(key, 0) + 1
        self._rounds[key] = value
        self._rounds.move_to_end(key)
        while len(self._rounds) > self._max_entries:
            self._rounds.popitem(last=False)
        return value

    def clear_all(self) -> None:
        self._prompt.clear()
        self._urgent.clear()
        self._served_l1.clear()
        self._rounds.clear()

    def _trim(self) -> None:
        while len(self._prompt) > self._max_entries:
            old, _ = self._prompt.popitem(last=False)
            self._urgent.discard(old)
            self._served_l1.pop(old, None)
        while len(self._served_l1) > self._max_entries:
            old, _ = self._served_l1.popitem(last=False)
            self._urgent.discard(old)


_STORE = CeliaRuntimeStore()


def get_runtime_store() -> CeliaRuntimeStore:
    return _STORE
