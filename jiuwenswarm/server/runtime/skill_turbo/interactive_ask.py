# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Helpers for the SkillTurbo guided-mode (interactive_ask) flag."""

from __future__ import annotations

from typing import Any, Mapping


def _raw_interactive_ask(source: Mapping[str, Any] | None) -> Any:
    if not isinstance(source, Mapping):
        return None
    return source.get("interactive_ask", source.get("interactiveAsk"))


def extract_interactive_ask(*sources: Mapping[str, Any] | None) -> bool:
    """Guided mode is explicit opt-in only.

    Missing / None means off. Never infer from ``supports_user_interaction``.
    """
    for source in sources:
        raw = _raw_interactive_ask(source)
        if raw is not None:
            return bool(raw)
    return False


def resolve_interactive_ask_from_inputs(inputs: dict[str, Any] | None) -> bool:
    """Read guided-mode flag from SkillTurbo inputs / metadata."""
    if not isinstance(inputs, dict):
        return False
    metadata = inputs.get("metadata")
    return extract_interactive_ask(
        inputs,
        metadata if isinstance(metadata, dict) else None,
    )


def apply_interactive_ask_to_inputs(
    inputs: dict[str, Any] | None,
    raw_interactive: Any,
) -> dict[str, Any]:
    """Copy inputs and stamp ``metadata.interactive_ask``.

    Omitted resume params mean guided mode is off, so a polluted saved True
    from ``supports_user_interaction`` fallback cannot leak into P5.
    """
    merged = dict(inputs or {})
    metadata = merged.get("metadata")
    meta = dict(metadata) if isinstance(metadata, dict) else {}
    meta["interactive_ask"] = bool(raw_interactive)
    merged["metadata"] = meta
    return merged
