# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""tokenjuice Python port — text processing utilities.

Port of src/core/text.ts and parts of src/core/reduce-utils.ts.
All functions are pure (no side effects).
"""

from __future__ import annotations

import hashlib
import math
import re

# ---------------------------------------------------------------------------
# Optional grapheme support
# ---------------------------------------------------------------------------

try:
    import grapheme as _grapheme

    def count_text_chars(text: str) -> int:
        return _grapheme.length(text)

    def slice_text_chars(text: str, start: int, end: int | None = None) -> str:
        graphemes = list(_grapheme.graphemes(text))
        return "".join(graphemes[start:end])

except ImportError:
    def count_text_chars(text: str) -> int:
        return len(text)

    def slice_text_chars(text: str, start: int, end: int | None = None) -> str:
        return text[start:end]


# ---------------------------------------------------------------------------
# ANSI escape stripping
# ---------------------------------------------------------------------------

_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_INCOMPLETE_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*$")
_INCOMPLETE_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*$")
_SINGLE_RE = re.compile(r"\x1b[@-_]")


def strip_ansi(text: str) -> str:
    """Remove ANSI/VT escape sequences from *text*."""
    text = _OSC_RE.sub("", text)
    text = _CSI_RE.sub("", text)
    text = _INCOMPLETE_OSC_RE.sub("", text)
    text = _INCOMPLETE_CSI_RE.sub("", text)
    text = _SINGLE_RE.sub("", text)
    text = text.replace("\x1b", "")
    return text


# ---------------------------------------------------------------------------
# Line operations
# ---------------------------------------------------------------------------


def normalize_lines(text: str) -> list[str]:
    """Split text into lines, normalize CRLF, strip trailing whitespace."""
    return [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]


def trim_empty_edges(lines: list[str]) -> list[str]:
    """Remove leading and trailing blank lines."""
    start = 0
    end = len(lines)
    while start < end and lines[start].strip() == "":
        start += 1
    while end > start and lines[end - 1].strip() == "":
        end -= 1
    return lines[start:end]


def dedupe_adjacent(lines: list[str]) -> list[str]:
    """Collapse consecutive identical lines."""
    result: list[str] = []
    for line in lines:
        if not result or result[-1] != line:
            result.append(line)
    return result


# ---------------------------------------------------------------------------
# Head / tail summarization
# ---------------------------------------------------------------------------

_TRUNCATION_SUFFIX = "\n... truncated ..."
_MIDDLE_MARKER = "\n... omitted ...\n"


def head_tail(
    lines: list[str],
    head: int,
    tail: int,
    *,
    no_omit: bool = False,
) -> dict:
    """Keep first *head* and last *tail* lines, insert omission marker."""
    from .types import create_compaction, create_passthrough_compaction, NO_COMPACTION

    safe_head = max(0, head)
    safe_tail = max(0, tail)

    if safe_head == 0 and safe_tail == 0:
        return {"lines": lines, "compaction": NO_COMPACTION}
    if len(lines) <= safe_head + safe_tail:
        return {"lines": lines, "compaction": NO_COMPACTION}

    omitted = len(lines) - safe_head - safe_tail

    if no_omit:
        return {"lines": lines, "compaction": create_passthrough_compaction("no-omit-head-tail-passthrough")}

    result = lines[:safe_head] + [f"... {omitted} lines omitted ..."] + lines[-safe_tail:]
    return {"lines": result, "compaction": create_compaction("head-tail-omission")}


# ---------------------------------------------------------------------------
# Clamping
# ---------------------------------------------------------------------------


def _trim_head_to_line_boundary(text: str) -> str:
    last_nl = text.rfind("\n")
    if last_nl == -1 or last_nl < len(text) * 0.5:
        return text
    return text[:last_nl]


def _trim_tail_to_line_boundary(text: str) -> str:
    first_nl = text.find("\n")
    if first_nl == -1 or first_nl > len(text) * 0.5:
        return text
    return text[first_nl + 1:]


def clamp_text(text: str, max_chars: int, *, no_omit: bool = False) -> dict:
    """Tail-truncate *text* to fit within *max_chars*."""
    from .types import create_compaction, create_passthrough_compaction, NO_COMPACTION

    if count_text_chars(text) <= max_chars:
        return {"text": text, "compaction": NO_COMPACTION}

    if no_omit:
        return {"text": text, "compaction": create_passthrough_compaction("no-omit-char-clip-passthrough")}

    suffix_len = count_text_chars(_TRUNCATION_SUFFIX)
    body_chars = max(0, max_chars - suffix_len)
    head = _trim_head_to_line_boundary(slice_text_chars(text, 0, body_chars))
    return {"text": f"{head}{_TRUNCATION_SUFFIX}", "compaction": create_compaction("tail-truncation")}


def clamp_text_middle(text: str, max_chars: int, *, no_omit: bool = False) -> dict:
    """Middle-truncate *text*: 70% head + marker + 30% tail."""
    from .types import create_compaction, create_passthrough_compaction, NO_COMPACTION

    if count_text_chars(text) <= max_chars:
        return {"text": text, "compaction": NO_COMPACTION}

    if no_omit:
        return {"text": text, "compaction": create_passthrough_compaction("no-omit-char-clip-passthrough")}

    marker_chars = count_text_chars(_MIDDLE_MARKER)
    body_chars = max(0, max_chars - marker_chars)
    head_chars = math.ceil(body_chars * 0.7)
    tail_chars = max(0, body_chars - head_chars)

    head = _trim_head_to_line_boundary(slice_text_chars(text, 0, head_chars))
    tail = _trim_tail_to_line_boundary(slice_text_chars(text, -tail_chars)) if tail_chars > 0 else ""

    return {
        "text": f"{head}{_MIDDLE_MARKER}{tail}",
        "compaction": create_compaction("middle-truncation"),
    }


# ---------------------------------------------------------------------------
# Reduce utilities
# ---------------------------------------------------------------------------


def short_hash(text: str) -> str:
    """SHA-256 first 12 hex chars."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def clip_middle_with_hash(text: str, max_chars: int, *, no_omit: bool = False) -> dict:
    """Middle-clip with content hash for long single lines."""
    from .types import create_compaction, create_passthrough_compaction, NO_COMPACTION

    if count_text_chars(text) <= max_chars:
        return {"text": text, "compaction": NO_COMPACTION}

    if no_omit:
        return {"text": text, "compaction": create_passthrough_compaction("no-omit-char-clip-passthrough")}

    omitted = count_text_chars(text) - max_chars
    head_chars = max(20, math.floor(max_chars * 0.55))
    tail_chars = max(20, max_chars - head_chars)
    hash_str = short_hash(text)

    head = slice_text_chars(text, 0, head_chars)
    tail = slice_text_chars(text, -tail_chars)

    return {
        "text": f"{head} ...[{omitted} chars omitted, sha256:{hash_str}]... {tail}",
        "compaction": create_compaction("hashed-middle-clip"),
    }


def compact_whitespace(text: str) -> str:
    """Collapse all whitespace runs to a single space."""
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Pluralization
# ---------------------------------------------------------------------------

_PASSED_FAILED_RE = re.compile(r"(?:passed|failed|skipped)$")
_SIBILANT_RE = re.compile(r"[sxz]$")
_SH_CH_RE = re.compile(r"(sh|ch)$")
_CONSONANT_Y_RE = re.compile(r"[^aeiou]y$")


def pluralize(count: int, noun: str) -> str:
    """English pluralization for fact strings."""
    if _PASSED_FAILED_RE.search(noun):
        return f"{count} {noun}"
    if count == 1:
        return f"{count} {noun}"
    if _SIBILANT_RE.search(noun) or _SH_CH_RE.search(noun):
        return f"{count} {noun}es"
    if _CONSONANT_Y_RE.search(noun):
        return f"{count} {noun[:-1]}ies"
    return f"{count} {noun}s"
