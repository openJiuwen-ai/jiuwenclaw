# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""配置生效策略 ``template_ref`` 字段归一化（与 Claw Manager 对齐）。"""

from __future__ import annotations

from typing import Any

KNOWN_SLOT_KEYS = frozenset({
    "default_model",
    "video_model",
    "audio_model",
    "vision_model",
    "skill_whitelist",
})


def normalize_template_ref(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("template_ref must be a JSON object")
    out: dict[str, str] = {}
    for key, raw in value.items():
        slot = str(key).strip()
        if not slot:
            continue
        if raw is None:
            continue
        text = str(raw).strip()
        if text:
            out[slot] = text
    return out


def read_template_ref_from_policy_dict(policy: dict[str, Any]) -> dict[str, str]:
    return normalize_template_ref(policy.get("template_ref"))


def merge_template_ref(base: dict[str, str], patch: Any) -> dict[str, str]:
    merged = dict(base)
    merged.update(normalize_template_ref(patch))
    return merged


def read_template_ref_from_row(row: Any) -> dict[str, str]:
    raw = getattr(row, "template_ref", None)
    if isinstance(raw, dict):
        return normalize_template_ref(raw)
    return {}


def apply_template_ref_to_updates(
    updates: dict[str, Any],
    *,
    existing_row: Any | None,
) -> dict[str, Any]:
    payload = dict(updates)
    if "template_ref" not in payload:
        return payload
    patch = payload.pop("template_ref")
    base = read_template_ref_from_row(existing_row) if existing_row is not None else {}
    payload["template_ref"] = merge_template_ref(base, patch)
    return payload
