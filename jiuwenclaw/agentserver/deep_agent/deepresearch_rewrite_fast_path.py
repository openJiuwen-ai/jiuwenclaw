"""Single-call fast path for strict DeepResearch report rewrite requests."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


_ENVELOPE_RE = re.compile(
    r"\A\s*<deepresearch_rewrite_request>(?P<body>.*?)"
    r"</deepresearch_rewrite_request>\s*\Z",
    re.DOTALL,
)
_REQUEST_KEYS = {"report_path", "action", "selection", "instruction"}
_ACTIONS = {"polish", "expand", "shorten"}


class RewriteFastPathError(ValueError):
    """Safe error raised after a rewrite envelope has been recognized."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RewriteRequest:
    """Validated top-level rewrite request.

    Protocol-v2 selection details remain owned by the existing prepare tool.
    """

    report_path: str
    action: str
    selection: dict[str, Any]
    instruction: str


def _invalid_request() -> RewriteFastPathError:
    return RewriteFastPathError("BAD_REQUEST", "invalid rewrite request")


def parse_rewrite_envelope(query: object) -> RewriteRequest | None:
    """Parse an exact rewrite envelope, or return None for unrelated messages."""
    if not isinstance(query, str):
        return None
    match = _ENVELOPE_RE.fullmatch(query)
    if match is None:
        return None
    try:
        payload = json.loads(match.group("body"))
    except (json.JSONDecodeError, TypeError) as exc:
        raise _invalid_request() from exc
    if not isinstance(payload, dict) or set(payload) != _REQUEST_KEYS:
        raise _invalid_request()

    report_path = payload.get("report_path")
    action = payload.get("action")
    selection = payload.get("selection")
    instruction = payload.get("instruction")
    if (
        not isinstance(report_path, str)
        or not report_path
        or action not in _ACTIONS
        or not isinstance(selection, dict)
        or not isinstance(instruction, str)
    ):
        raise _invalid_request()
    return RewriteRequest(
        report_path=report_path,
        action=action,
        selection=selection,
        instruction=instruction,
    )
