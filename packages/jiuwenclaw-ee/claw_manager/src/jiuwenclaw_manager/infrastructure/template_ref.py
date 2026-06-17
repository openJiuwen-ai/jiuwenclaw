"""配置生效策略 ``template_ref`` 字段归一化（无 core/schemas 依赖，供 Pydantic 与业务层共用）。"""

from __future__ import annotations

from typing import Any

from jiuwenclaw_manager.schemas.template_slot_schemas import TEMPLATE_REF_SLOTS

KNOWN_SLOT_KEYS = TEMPLATE_REF_SLOTS
_LEGACY_FLAT_KEYS = tuple(TEMPLATE_REF_SLOTS)

__all__ = (
    "KNOWN_SLOT_KEYS",
    "normalize_template_ref",
    "normalize_template_ref_optional",
    "read_template_ref_from_row",
    "merge_template_ref",
    "apply_template_ref_to_updates",
)


def _dedupe_preserve_order(refs: list[str]) -> list[str]:
    """同槽位内按引用字符串去重，保留首次出现顺序。"""
    seen: set[str] = set()
    out: list[str] = []
    for ref in refs:
        if ref in seen:
            continue
        seen.add(ref)
        out.append(ref)
    return out


def _normalize_slot_refs(raw: Any) -> list[str]:
    """将单槽位原始值规范为引用字符串列表（兼容历史单字符串写法）。"""
    if raw is None:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        return [text] if text else []
    if isinstance(raw, list):
        out: list[str] = []
        for item in raw:
            if item is None:
                continue
            text = str(item).strip()
            if text:
                out.append(text)
        return _dedupe_preserve_order(out)
    text = str(raw).strip()
    return [text] if text else []


def normalize_template_ref(value: Any) -> dict[str, list[str]]:
    """将 ``template_ref`` 规范为 ``{slot: [ref_string, ...]}``；空值键省略。"""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("template_ref must be a JSON object")
    out: dict[str, list[str]] = {}
    for key, raw in value.items():
        slot = str(key).strip()
        if not slot:
            continue
        refs = _normalize_slot_refs(raw)
        if refs:
            out[slot] = refs
    return out


def normalize_template_ref_optional(value: Any) -> dict[str, list[str]] | None:
    """规范 ``template_ref``；``None`` 原样保留（用于 PATCH 未传字段）。"""
    if value is None:
        return None
    return normalize_template_ref(value)


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
