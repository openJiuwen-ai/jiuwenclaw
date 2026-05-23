"""将 ``EffectiveEnterpriseConfig`` 中的模型模板写入 config 快照（覆盖 ``config.yaml`` 对应槽位）。"""

from __future__ import annotations

import copy
from typing import Any

from .schemas import (
    EffectiveEnterpriseConfig,
    SLOT_TO_CONFIG_KEY,
    TemplateRefSlot,
)


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
    """深拷贝 ``config_base`` 并写入企业模型槽位；返回 ``(merged, applied_any)``。"""
    if not enterprise.models:
        return config_base, False

    merged = copy.deepcopy(config_base)
    models_section = merged.get("models")
    if not isinstance(models_section, dict):
        models_section = {}
        merged["models"] = models_section

    applied = False
    default_entries: list[dict[str, Any]] = []

    for slot, entity in enterprise.models.items():
        if not isinstance(entity, dict):
            continue
        try:
            slot_key = TemplateRefSlot(slot)
        except ValueError:
            continue
        config_key = SLOT_TO_CONFIG_KEY.get(slot_key)
        if not config_key:
            continue
        entry = model_entity_to_config_entry(entity)
        mcc = entry.get("model_client_config") or {}
        if not str(mcc.get("model_name") or "").strip():
            continue

        models_section[config_key] = {
            "model_client_config": dict(mcc),
            "model_config_obj": dict(entry.get("model_config_obj") or {}),
        }
        if config_key == "default":
            default_entries.append(entry)
        applied = True

    if default_entries:
        models_section["defaults"] = default_entries
        models_section["default"] = default_entries[0]
        react = merged.get("react")
        if isinstance(react, dict):
            mcc = default_entries[0].get("model_client_config") or {}
            if mcc.get("model_name"):
                react["model_name"] = mcc["model_name"]
            if mcc:
                react["model_client_config"] = dict(mcc)
            mco = default_entries[0].get("model_config_obj")
            if isinstance(mco, dict) and mco:
                react["model_config_obj"] = dict(mco)

    return merged, applied
