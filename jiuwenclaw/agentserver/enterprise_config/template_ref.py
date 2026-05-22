"""配置生效策略 ``template_ref`` 字段归一化（与 manager_ws_client / Claw Manager 对齐）。"""

from __future__ import annotations

from typing import Any

SLOT_KEYS = (
    "default_model",
    "video_model",
    "audio_model",
    "vision_model",
)

EXTENSION_CONFIG_REFS_KEY = "extension_config_refs"


def normalize_template_ref(value: Any) -> dict[str, Any]:
    """归一化 ``template_ref`` JSON 字段。

    模型槽位键的值为字符串；``extension_config_refs`` 键的值为字符串列表。
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("template_ref must be a JSON object")
    out: dict[str, Any] = {}
    for key, raw in value.items():
        slot = str(key).strip()
        if not slot:
            continue
        if raw is None:
            continue
        if slot == EXTENSION_CONFIG_REFS_KEY:
            if isinstance(raw, list):
                out[slot] = [str(r).strip() for r in raw if str(r).strip()]
            else:
                text = str(raw).strip()
                if text:
                    out[slot] = [text]
            continue
        text = str(raw).strip()
        if text:
            out[slot] = text
    return out


def read_template_ref_from_policy(policy: dict[str, Any]) -> dict[str, Any]:
    """从策略行读取槽位引用；兼容 ``template_ref`` 与旧版平铺字段。"""
    refs = normalize_template_ref(policy.get("template_ref"))
    if refs:
        return refs
    legacy: dict[str, Any] = {}
    for key in SLOT_KEYS:
        raw = policy.get(key)
        if raw is None:
            continue
        text = str(raw).strip()
        if text:
            legacy[key] = text
    return legacy


def read_extension_config_refs(policy: dict[str, Any]) -> list[str]:
    """从策略行读取扩展配置引用列表。"""
    refs = read_template_ref_from_policy(policy)
    value = refs.get(EXTENSION_CONFIG_REFS_KEY)
    if isinstance(value, list):
        return value
    return []
