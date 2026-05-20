"""配置生效策略 ``template_ref`` 字段归一化与读写。"""

from __future__ import annotations

from typing import Any

KNOWN_SLOT_KEYS = frozenset({
    "default_model",
    "video_model",
    "audio_model",
    "vision_model",
    "skill_whitelist",
})

_LEGACY_FLAT_KEYS = tuple(KNOWN_SLOT_KEYS)


def normalize_template_ref(value: Any) -> dict[str, str]:
    """将 ``template_ref`` 规范为 ``{slot: ref_string}``；空值键省略。"""
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


def template_ref_from_api_body(data: Any) -> dict[str, str]:
    """从 API 请求体解析 ``template_ref``（仅接受 ``template_ref`` 字段）。"""
    if not isinstance(data, dict):
        return {}
    if "template_ref" in data:
        return normalize_template_ref(data.get("template_ref"))
    return {}


def read_template_ref_from_row(row: Any) -> dict[str, str]:
    """从 ORM/行对象读取 ``template_ref``；无列时返回 ``{}``。"""
    raw = getattr(row, "template_ref", None)
    if isinstance(raw, dict):
        return normalize_template_ref(raw)
    return {}


def merge_template_ref(
    base: dict[str, str],
    patch: Any,
) -> dict[str, str]:
    """合并更新：``patch`` 中出现的键覆盖 ``base``，未出现键保留。"""
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
