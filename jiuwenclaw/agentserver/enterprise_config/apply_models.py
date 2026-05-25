"""将 ``EffectiveEnterpriseConfig`` 中的模型模板写入 config 快照（覆盖 ``config.yaml`` 对应槽位）。"""

from __future__ import annotations

import copy
from typing import Any

from .loader import (
    EffectiveEnterpriseConfig,
    TemplateRefSlot,
)

SLOT_TO_CONFIG_KEY: dict[TemplateRefSlot, str] = {
    TemplateRefSlot.DEFAULT_MODEL: "default",
    TemplateRefSlot.VISION_MODEL: "vision",
    TemplateRefSlot.AUDIO_MODEL: "audio",
    TemplateRefSlot.VIDEO_MODEL: "video",
}


def model_entity_to_config_entry(entity: dict[str, Any]) -> dict[str, Any]:
    """将 ``model_template`` 行转为 ``get_default_models`` 兼容的条目。"""
    parameters = entity.get("parameters")
    if not isinstance(parameters, dict):
        parameters = {}

    mcc: dict[str, Any] = {
        "api_base": str(entity.get("api_base") or "").strip(),
        "api_key": str(entity.get("api_key") or "").strip(),
        "model_name": str(entity.get("model_id") or "").strip(),
        "client_provider": str(entity.get("model_provider") or "").strip(),
        "timeout": entity.get("timeout", 1800),
        "verify_ssl": bool(entity.get("verify_ssl", False)),
    }
    retry_count = entity.get("retry_count")
    if retry_count is not None:
        mcc["max_retries"] = retry_count

    return {
        "template_name": str(entity.get("template_name") or "").strip(),
        "template_id": str(entity.get("template_id") or "").strip(),
        "model_client_config": mcc,
        "model_config_obj": parameters,
    }


def apply_enterprise_models_to_config(
    config_base: dict[str, Any],
    enterprise: EffectiveEnterpriseConfig,
) -> tuple[dict[str, Any], bool]:
    """深拷贝 ``config_base`` 并写入企业模型槽位；返回 ``(merged, applied_any)``。

    ``template_ref`` 各槽位可解析出多个 ``template_id``，但写入 ``config.yaml`` 兼容结构时，
    **每个模型槽位仅取列表首项**（第一个有效模板）写入 ``SLOT_TO_CONFIG_KEY`` 对应的 config 键
    （如 ``default_model`` → ``models.default``）。其余 ``template_id`` 不在此函数中展开。
    """
    if not enterprise.models:
        return config_base, False

    merged = copy.deepcopy(config_base)
    models_section = merged.get("models")
    if not isinstance(models_section, dict):
        models_section = {}
        merged["models"] = models_section

    applied = False
    default_entry: dict[str, Any] | None = None

    for slot, entities in enterprise.models.items():
        if not isinstance(entities, list):
            entities = [entities] if isinstance(entities, dict) else []
        try:
            slot_key = TemplateRefSlot(slot)
        except ValueError:
            continue
        config_key = SLOT_TO_CONFIG_KEY.get(slot_key)
        if not config_key:
            continue

        entry: dict[str, Any] | None = None
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            candidate = model_entity_to_config_entry(entity)
            mcc = candidate.get("model_client_config") or {}
            if not str(mcc.get("model_name") or "").strip():
                continue
            entry = candidate
            break

        if entry is None:
            continue

        mcc = entry.get("model_client_config") or {}
        models_section[config_key] = {
            "model_client_config": dict(mcc),
            "model_config_obj": dict(entry.get("model_config_obj") or {}),
        }
        if config_key == "default":
            default_entry = entry
        applied = True

    if default_entry is not None:
        models_section["defaults"] = [default_entry]
        models_section["default"] = default_entry
        react = merged.get("react")
        if isinstance(react, dict):
            mcc = default_entry.get("model_client_config") or {}
            if mcc.get("model_name"):
                react["model_name"] = mcc["model_name"]
            if mcc:
                react["model_client_config"] = dict(mcc)
            mco = default_entry.get("model_config_obj")
            if isinstance(mco, dict) and mco:
                react["model_config_obj"] = dict(mco)

    return merged, applied
