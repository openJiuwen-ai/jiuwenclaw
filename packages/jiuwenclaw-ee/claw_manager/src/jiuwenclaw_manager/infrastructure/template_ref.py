"""配置生效策略 ``template_ref`` 字段归一化（无 core/schemas 依赖，供 Pydantic 与业务层共用）。"""

from __future__ import annotations

from typing import Any

KNOWN_SLOT_KEYS = frozenset({
    "default_model",
    "video_model",
    "audio_model",
    "vision_model",
    "skill_whitelist",
    "extension_config",
})


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
        return out
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


def coerce_template_ref(value: Any) -> dict[str, list[str]]:
    """Pydantic 入参校验：``None`` 视为 ``{}``，其余走 ``normalize_template_ref``。"""
    if value is None:
        return {}
    return normalize_template_ref(value)


def coerce_template_ref_optional(value: Any) -> dict[str, list[str]] | None:
    """Pydantic 入参校验：保留 ``None``，否则规范为 ``dict[str, list[str]]``。"""
    if value is None:
        return None
    return normalize_template_ref(value)
