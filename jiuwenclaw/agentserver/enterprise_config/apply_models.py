"""将 ``EffectiveEnterpriseConfig`` 中的模型模板写入 config 快照，并按策略解析生效模型。"""

from __future__ import annotations

import copy
import logging
from typing import Any

from jiuwenclaw.config import get_config

from .loader import (
    DEFAULT_AGENT_LOAD_SLOTS,
    EffectiveEnterpriseConfig,
    TemplateRefSlot,
    load_effective_enterprise_config,
)

logger = logging.getLogger(__name__)

MODEL_SOURCE_CONFIG = "config.yaml"
MODEL_SOURCE_ENTERPRISE = "enterprise_policy"

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


def build_routing_agent_request(
    *,
    request_id: str = "models.list",
    channel_id: str = "web",
    session_id: str | None = None,
    routing: dict[str, Any] | None = None,
) -> Any:
    """由路由字段构造 ``AgentRequest``（供 ``routing_context_from_request`` 解析）。"""
    from jiuwenclaw.schema.agent import AgentRequest

    params: dict[str, str] = {}
    metadata_query: dict[str, list[str]] = {}
    if isinstance(routing, dict):
        for key in ("user_id", "group_id", "bot_id"):
            raw = routing.get(key)
            if raw is None:
                continue
            text = str(raw).strip()
            if not text:
                continue
            params[key] = text
            metadata_query[key] = [text]

    metadata = {"query": metadata_query} if metadata_query else None
    return AgentRequest(
        request_id=request_id,
        channel_id=channel_id,
        session_id=session_id,
        params=params,
        metadata=metadata,
    )


async def resolve_effective_models_config(
    request: Any | None = None,
) -> tuple[dict[str, Any], str]:
    """三级策略匹配企业模型并合并进 config 快照；未命中则返回 ``config.yaml``。

    返回 ``(config_snapshot, model_source)``，``model_source`` 为
    ``enterprise_policy`` 或 ``config.yaml``。
    """
    config_base = get_config()
    if request is None:
        return config_base, MODEL_SOURCE_CONFIG

    try:
        loaded = await load_effective_enterprise_config(
            request,
            DEFAULT_AGENT_LOAD_SLOTS,
        )
    except Exception as exc:
        logger.warning("[enterprise_config] resolve_effective_models_config failed: %s", exc)
        return config_base, MODEL_SOURCE_CONFIG

    if loaded is None:
        return config_base, MODEL_SOURCE_CONFIG

    merged, applied = apply_enterprise_models_to_config(config_base, loaded)
    if applied:
        return merged, MODEL_SOURCE_ENTERPRISE
    return config_base, MODEL_SOURCE_CONFIG
