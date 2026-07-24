"""Shared helpers for compact LLM request payloads."""

from __future__ import annotations

import json
from typing import Any


def compact_json(payload: Any) -> str:
    return json.dumps(
        prune_empty(payload),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def prune_empty(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: cleaned
            for key, item in value.items()
            if (cleaned := prune_empty(item)) not in (None, "", [], {})
        }
    if isinstance(value, list):
        return [
            cleaned
            for item in value
            if (cleaned := prune_empty(item)) not in (None, "", [], {})
        ]
    return value
