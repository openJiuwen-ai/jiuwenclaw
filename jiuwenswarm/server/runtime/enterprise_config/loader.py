"""从 Gateway DB 按实例 Agent 资源加载企业级生效配置。"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from jiuwenswarm.common.local_env_config import is_enterprise
from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.common.utils import logger

from . import gateway_db
from .schemas import (
    DEFAULT_AGENT_LOAD_SLOTS,
    MODEL_SLOT_KEYS,
    EffectiveEnterpriseConfig,
    RoutingContext,
    TemplateRefSlot,
    normalize_template_ref,
)


def routing_context_from_request(request: AgentRequest | Any) -> RoutingContext:
    """从 ``request.params`` 解析路由上下文（调用方保证字段格式正确）。"""
    p = getattr(request, "params", None) or {}
    if not isinstance(p, dict):
        p = {}
    return RoutingContext(
        group_id=str(p.get("group_id") or "").strip(),
        bot_id=str(p.get("bot_id") or "").strip(),
        user_id=str(p.get("user_id") or "").strip(),
    )


def _normalize_service_config_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    if "autoscale_interval" in out and out["autoscale_interval"] is not None:
        try:
            out["autoscale_interval"] = float(out["autoscale_interval"])
        except (TypeError, ValueError):
            pass
    if "container_port" in out and out["container_port"] is not None:
        try:
            out["container_port"] = int(out["container_port"])
        except (TypeError, ValueError):
            pass
    return out


def _apply_slot_entities(
    result: EffectiveEnterpriseConfig,
    slot: str,
    entities: list[dict[str, Any]],
) -> None:
    if slot in {s.value for s in MODEL_SLOT_KEYS}:
        result.models[slot] = entities
    elif slot == TemplateRefSlot.EMBEDDING_MODEL:
        result.embedding = entities
    elif slot == TemplateRefSlot.SKILL_WHITELIST:
        result.skill_whitelist = entities
    elif slot == TemplateRefSlot.EXTENSION_CONFIG:
        result.extension_config = entities
    elif slot == TemplateRefSlot.SERVICE_CONFIG:
        result.service_config = entities


def _any_requested_slot_loaded(
    result: EffectiveEnterpriseConfig,
    load_slots: frozenset[str],
) -> bool:
    for slot in load_slots:
        if slot in {s.value for s in MODEL_SLOT_KEYS} and result.models.get(slot):
            return True
        if slot == TemplateRefSlot.EMBEDDING_MODEL and result.embedding:
            return True
        if slot == TemplateRefSlot.SKILL_WHITELIST and result.skill_whitelist:
            return True
        if slot == TemplateRefSlot.EXTENSION_CONFIG and result.extension_config:
            return True
        if slot == TemplateRefSlot.SERVICE_CONFIG and result.service_config:
            return True
    return False


async def _fetch_slot_entities(
    slot: str,
    template_ids: list[str],
) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    for template_id in template_ids:
        entity = await gateway_db.fetch_template_by_slot(slot, template_id)
        if entity is None:
            logger.warning(
                "[enterprise_config] template not found: slot=%r template_id=%r",
                slot,
                template_id,
            )
            continue
        if slot == TemplateRefSlot.SERVICE_CONFIG:
            entity = _normalize_service_config_row(entity)
        entities.append(entity)
    return entities


async def _fetch_instance_agent_resource(resource_id: str) -> dict[str, Any] | None:
    rid = str(resource_id or "").strip()
    if not rid:
        return None
    rows = await gateway_db.list_records(
        "instance_agent_resource",
        filters={"resource_id": rid},
    )
    return rows[0] if rows else None


async def _fetch_agent_template_row(template_id: str) -> dict[str, Any] | None:
    tid = str(template_id or "").strip()
    if not tid:
        return None
    rows = await gateway_db.list_records(
        "agent_template",
        filters={"enabled": True, "template_id": tid},
    )
    return rows[0] if rows else None


def _literal_slot_template_id_map(
    refs: dict[str, list[str]],
) -> dict[str, list[str]]:
    """仅接受字面 ``template_id``；跳过 ``${...}`` / ``or`` 等映射表达式。"""
    slot_template_id_map: dict[str, list[str]] = {}
    for slot, raw_list in refs.items():
        resolved: list[str] = []
        seen: set[str] = set()
        for raw in raw_list:
            text = str(raw or "").strip()
            if not text or text.startswith("${") or " or " in text.lower():
                continue
            if text not in seen:
                seen.add(text)
                resolved.append(text)
        if resolved:
            slot_template_id_map[slot] = resolved
    return slot_template_id_map


async def load_effective_enterprise_config(
    request: AgentRequest | Any,
    slots: Collection[TemplateRefSlot],
) -> EffectiveEnterpriseConfig | None:
    """按 ``request.bot_id``（即 ``instance_agent_resource.resource_id``）加载 Agent 实例生效配置。

    读取实例 Agent 资源 → ``agent_template`` → 仅按字面 ``template_id`` 解析
    ``template_ref`` 并加载模型等模板实体。
    """
    if not is_enterprise():
        return None

    ctx = routing_context_from_request(request)
    if not slots:
        raise ValueError("slots must not be empty")

    rid = str(ctx.bot_id or "").strip()
    if not rid:
        logger.warning(
            "[enterprise_config] bot_id(resource_id) missing in request context %s",
            ctx.as_dict(),
        )
        return None

    load_slots = frozenset(slot.value for slot in slots)

    resource_row = await _fetch_instance_agent_resource(rid)
    if resource_row is None:
        logger.warning(
            "[enterprise_config] instance_agent_resource not found: resource_id=%r",
            rid,
        )
        return None

    ref_template_id = str(resource_row.get("ref_template_id") or "").strip()
    if not ref_template_id:
        logger.warning(
            "[enterprise_config] instance_agent_resource missing ref_template_id: resource_id=%r",
            rid,
        )
        return None

    agent_template_row = await _fetch_agent_template_row(ref_template_id)
    if agent_template_row is None:
        logger.warning(
            "[enterprise_config] agent_template not found or disabled: "
            "resource_id=%r ref_template_id=%r",
            rid,
            ref_template_id,
        )
        return None

    merged_refs = normalize_template_ref(agent_template_row.get("template_ref"))
    filtered_refs = {
        slot: refs
        for slot, refs in merged_refs.items()
        if slot in load_slots
    }

    if not filtered_refs:
        logger.warning(
            "[enterprise_config] agent_template has no template_ref for resource_id=%r slots=%s",
            rid,
            sorted(load_slots),
        )
        return None

    slot_template_id_map = _literal_slot_template_id_map(filtered_refs)
    if not slot_template_id_map:
        logger.warning(
            "[enterprise_config] agent template_ref has no literal template_id "
            "for resource_id=%r refs=%s",
            rid,
            filtered_refs,
        )
        return None

    template_data = agent_template_row.get("data")
    workspace_dir = None
    if isinstance(template_data, dict):
        raw_ws = template_data.get("workspace_dir")
        if isinstance(raw_ws, str) and raw_ws.strip():
            workspace_dir = raw_ws.strip()

    result = EffectiveEnterpriseConfig(
        routing=ctx,
        template_ref=slot_template_id_map,
        agent_id=rid,
        workspace_dir=workspace_dir,
        resource_id=rid,
        ref_template_id=ref_template_id,
        agent_template=agent_template_row,
        instance_agent_resource=resource_row,
        debug={
            "jiuwenclaw_id": gateway_db.resolve_jiuwenclaw_id(),
            "group_id": ctx.group_id,
            "bot_id": ctx.bot_id,
            "user_id": ctx.user_id,
            "load_slots": sorted(load_slots),
            "resource_id": rid,
            "ref_template_id": ref_template_id,
        },
    )

    for slot, template_ids in slot_template_id_map.items():
        entities = await _fetch_slot_entities(slot, template_ids)
        if entities:
            _apply_slot_entities(result, slot, entities)

    if not _any_requested_slot_loaded(result, load_slots):
        logger.warning(
            "[enterprise_config] no template entities loaded for resource_id=%r "
            "template_ref=%s ctx=%s slots=%s",
            rid,
            slot_template_id_map,
            ctx.as_dict(),
            sorted(load_slots),
        )
        return None

    logger.info(
        "[enterprise_config] loaded enterprise config by resource_id=%r slots=%s payload=%s",
        rid,
        sorted(load_slots),
        result.as_dict(),
    )
    return result


__all__ = (
    "DEFAULT_AGENT_LOAD_SLOTS",
    "EffectiveEnterpriseConfig",
    "TemplateRefSlot",
    "load_effective_enterprise_config",
    "routing_context_from_request",
)
