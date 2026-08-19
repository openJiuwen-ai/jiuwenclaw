# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Helpers for the SkillTurbo guided-mode (interactive_ask) flag."""

from __future__ import annotations

from typing import Any


def resolve_interactive_ask_from_inputs(inputs: dict[str, Any] | None) -> bool | None:
    """Read guided-mode flag from SkillTurbo inputs / metadata.

    Returns True/False when the request declared the flag, otherwise None so
    callers can keep the ContextVar default (False).
    """
    if not isinstance(inputs, dict):
        return None
    raw = inputs.get("interactive_ask", inputs.get("interactiveAsk"))
    metadata = inputs.get("metadata")
    if raw is None and isinstance(metadata, dict):
        raw = metadata.get("interactive_ask", metadata.get("interactiveAsk"))
    if raw is None:
        return None
    return bool(raw)


def apply_interactive_ask_to_inputs(
    inputs: dict[str, Any] | None,
    raw_interactive: Any,
) -> dict[str, Any]:
    """Copy inputs and stamp ``metadata.interactive_ask`` when the flag is present."""
    merged = dict(inputs or {})
    if raw_interactive is None:
        return merged
    metadata = merged.get("metadata")
    meta = dict(metadata) if isinstance(metadata, dict) else {}
    meta["interactive_ask"] = bool(raw_interactive)
    merged["metadata"] = meta
    return merged
