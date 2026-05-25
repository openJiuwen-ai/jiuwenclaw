"""配置生效策略 ``template_ref`` 字段读写辅助（归一化逻辑见 infrastructure.template_ref）。"""

from __future__ import annotations

from typing import Any

from jiuwenclaw_manager.infrastructure.template_ref import (
    KNOWN_SLOT_KEYS,
    coerce_template_ref,
    coerce_template_ref_optional,
    normalize_template_ref,
)

_LEGACY_FLAT_KEYS = tuple(KNOWN_SLOT_KEYS)

__all__ = (
    "KNOWN_SLOT_KEYS",
    "coerce_template_ref",
    "coerce_template_ref_optional",
    "normalize_template_ref",
    "template_ref_from_api_body",
    "read_template_ref_from_row",
    "merge_template_ref",
    "apply_template_ref_to_updates",
)


def template_ref_from_api_body(data: Any) -> dict[str, list[str]]:
    """从 API 请求体解析 ``template_ref``（仅接受 ``template_ref`` 字段）。"""
    if not isinstance(data, dict):
        return {}
    if "template_ref" in data:
        return normalize_template_ref(data.get("template_ref"))
    return {}


def read_template_ref_from_row(row: Any) -> dict[str, list[str]]:
    """从 ORM/行对象读取 ``template_ref``；无列时返回 ``{}``。"""
    raw = getattr(row, "template_ref", None)
    if isinstance(raw, dict):
        return normalize_template_ref(raw)
    return {}


def merge_template_ref(
    base: dict[str, list[str]],
    patch: Any,
) -> dict[str, list[str]]:
    """合并更新：``patch`` 中出现的槽位整组覆盖 ``base``，未出现槽位保留。"""
    merged = dict(base)
    merged.update(normalize_template_ref(patch))
    return merged


def apply_template_ref_to_updates(
    updates: dict[str, Any],
    *,
    existing_row: Any | None,
) -> dict[str, Any]:
    """处理 update 载荷中的 ``template_ref``（合并后写回整列）。"""
    payload = dict(updates)
    if "template_ref" not in payload:
        for key in _LEGACY_FLAT_KEYS:
            payload.pop(key, None)
        return payload
    patch = payload.pop("template_ref")
    base = read_template_ref_from_row(existing_row) if existing_row is not None else {}
    payload["template_ref"] = merge_template_ref(base, patch)
    for key in _LEGACY_FLAT_KEYS:
        payload.pop(key, None)
    return payload
