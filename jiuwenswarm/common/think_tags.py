"""Utilities for splitting MiniMax-style ``<think>`` content blocks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ThinkPartKind = Literal["text", "reasoning"]


@dataclass(frozen=True)
class ThinkTagPart:
    kind: ThinkPartKind
    content: str


def _trailing_prefix_len(text: str, marker: str) -> int:
    """Return the suffix length that may be the start of ``marker``."""
    lower_text = text.lower()
    marker = marker.lower()
    max_len = min(len(lower_text), len(marker) - 1)
    for size in range(max_len, 0, -1):
        if marker.startswith(lower_text[-size:]):
            return size
    return 0


class ThinkTagStreamParser:
    """Incrementally split ``<think>...</think>`` blocks from visible text.

    Some OpenAI-compatible providers, notably MiniMax-M2.7, encode model
    reasoning inside the regular content stream. This parser keeps partial tags
    buffered across chunks so raw tag fragments are not leaked to users.
    """

    _OPEN_PREFIX = "<think"
    _CLOSE_TAG = "</think>"

    def __init__(self) -> None:
        self._buffer = ""
        self._in_reasoning = False

    @property
    def has_pending(self) -> bool:
        return bool(self._buffer or self._in_reasoning)

    def feed(self, text: str) -> list[ThinkTagPart]:
        if not text:
            return []
        self._buffer += text
        return self._drain(complete=False)

    def flush(self) -> list[ThinkTagPart]:
        return self._drain(complete=True)

    def _find_open_tag(self, lower: str) -> int:
        start = 0
        while True:
            idx = lower.find(self._OPEN_PREFIX, start)
            if idx < 0:
                return -1
            next_idx = idx + len(self._OPEN_PREFIX)
            if next_idx >= len(lower) or lower[next_idx].isspace() or lower[next_idx] == ">":
                return idx
            start = idx + 1

    def _drain(self, *, complete: bool) -> list[ThinkTagPart]:
        parts: list[ThinkTagPart] = []

        while self._buffer:
            lower = self._buffer.lower()
            if self._in_reasoning:
                close_idx = lower.find(self._CLOSE_TAG)
                if close_idx < 0:
                    hold = 0 if complete else _trailing_prefix_len(self._buffer, self._CLOSE_TAG)
                    emit_len = len(self._buffer) - hold
                    if emit_len > 0:
                        parts.append(ThinkTagPart("reasoning", self._buffer[:emit_len]))
                        self._buffer = self._buffer[emit_len:]
                    break

                if close_idx > 0:
                    parts.append(ThinkTagPart("reasoning", self._buffer[:close_idx]))
                self._buffer = self._buffer[close_idx + len(self._CLOSE_TAG):]
                self._in_reasoning = False
                continue

            open_idx = self._find_open_tag(lower)
            orphan_close_idx = lower.find(self._CLOSE_TAG)
            if orphan_close_idx >= 0 and (open_idx < 0 or orphan_close_idx < open_idx):
                if orphan_close_idx > 0:
                    parts.append(ThinkTagPart("reasoning", self._buffer[:orphan_close_idx]))
                self._buffer = self._buffer[orphan_close_idx + len(self._CLOSE_TAG):]
                self._in_reasoning = False
                continue

            if open_idx < 0:
                hold = 0 if complete else _trailing_prefix_len(self._buffer, self._OPEN_PREFIX)
                emit_len = len(self._buffer) - hold
                if emit_len > 0:
                    parts.append(ThinkTagPart("text", self._buffer[:emit_len]))
                    self._buffer = self._buffer[emit_len:]
                break

            if open_idx > 0:
                parts.append(ThinkTagPart("text", self._buffer[:open_idx]))
                self._buffer = self._buffer[open_idx:]
                lower = self._buffer.lower()

            tag_end = self._buffer.find(">")
            if tag_end < 0:
                if complete:
                    self._buffer = ""
                break
            self._buffer = self._buffer[tag_end + 1:]
            self._in_reasoning = True

        if complete and self._buffer:
            parts.append(ThinkTagPart("reasoning" if self._in_reasoning else "text", self._buffer))
            self._buffer = ""
            self._in_reasoning = False

        return [part for part in parts if part.content]


def split_think_tags(text: str) -> list[ThinkTagPart]:
    parser = ThinkTagStreamParser()
    return [*parser.feed(text), *parser.flush()]


def strip_think_tags(text: str) -> str:
    return "".join(part.content for part in split_think_tags(text) if part.kind == "text")
