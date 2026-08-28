# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""从 Gateway DB 加载企业级生效配置（Service → Agent → Global 三级匹配）。"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from openjiuwen_runtime.foundation.log import get_logger

from ...infrastructure.utils import (
    fill_missing_template_ref_slots,
    merge_template_ref,
    normalize_template_ref,
)
from . import expressions
from .gateway_db import GatewayDb
from .schemas import (
    MODEL_SLOT_KEYS,
    EffectiveEnterpriseConfig,
    RoutingContext,
    TemplateRefSlot,
)

logger = get_logger(__name__)

# 策略/映射选路排序：priority 降序；同 priority 时 updated_at 越新越优先。
# SQLAlchemyHandler.list_records 的 str 形式 order_by 仅支持单列；多列须用 list[tuple[field, is_desc]]。
POLICY_MATCH_ORDER_BY: list[tuple[str, bool]] = [
    ("priority", True),
    ("updated_at", True),
]


async def _fetch_global_policy_refs() -> tuple[dict[str, Any] | None, dict[str, list[str]]]:
    filters: dict[str, Any] = {"enabled": True}
    global_rows = await GatewayDb.current().list_records(
        "config_effective_global_policy",
        filters=filters,
        order_by=POLICY_MATCH_ORDER_BY,
    )
    if not global_rows:
        return None, {}
    matched_global = global_rows[0]
    return matched_global, normalize_template_ref(matched_global.get("template_ref"))


@dataclass
class _PolicyMatchResult:
    merged_refs: dict[str, list[str]]
    matched_service: dict[str, Any] | None
    matched_agent: dict[str, Any] | None
    matched_global: dict[str, Any] | None


async def _resolve_policy_match(ctx: RoutingContext) -> _PolicyMatchResult:
    service_rules = await GatewayDb.current().list_records(
        "config_effective_service_policy",
        filters={"enabled": True},
        order_by=POLICY_MATCH_ORDER_BY,
    )

    matched_service: dict[str, Any] | None = None
    matched_agent: dict[str, Any] | None = None
    matched_global, global_refs = await _fetch_global_policy_refs()
    merged_refs: dict[str, list[str]] = {}

    for rule in service_rules:
        if expressions.evaluate_match_expr(rule.get("match_expr"), ctx):
            matched_service = rule
            merged_refs = normalize_template_ref(rule.get("template_ref"))
            break

    if matched_service is not None:
        sp_policy_id = str(matched_service["policy_id"])
        agent_rules = await GatewayDb.current().list_records(
            "config_effective_agent_policy",
            filters={"enabled": True, "service_policy_id": sp_policy_id},
            order_by=POLICY_MATCH_ORDER_BY,
        )
        for rule in agent_rules:
            if expressions.evaluate_match_expr(rule.get("match_expr"), ctx):
                matched_agent = rule
                merged_refs = merge_template_ref(
                    merged_refs,
                    normalize_template_ref(rule.get("template_ref")),
                )
                break
        merged_refs = fill_missing_template_ref_slots(merged_refs, global_refs)
    else:
        merged_refs = global_refs

    return _PolicyMatchResult(
        merged_refs=merged_refs,
        matched_service=matched_service,
        matched_agent=matched_agent,
        matched_global=matched_global,
    )


def _coerce_routing_field(value: Any) -> str:
    """将路由字段规范为字符串（兼容 WebChannel ``parse_qs`` 的列表值）。"""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if value is None:
        return ""
    return str(value).strip()


def _routing_field_sources(request: Any) -> list[dict[str, Any]]:
    """按优先级收集路由字段来源：``params`` → ``metadata`` → ``metadata.query``。"""
    sources: list[dict[str, Any]] = []
    params = getattr(request, "params", None)
    if isinstance(params, dict):
        sources.append(params)
    metadata = getattr(request, "metadata", None)
    if isinstance(metadata, dict):
        sources.append(metadata)
        query = metadata.get("query")
        if isinstance(query, dict):
            sources.append(query)
    return sources


def _resolve_routing_field(request: Any, field: str) -> str:
    for source in _routing_field_sources(request):
        if field not in source:
            continue
        coerced = _coerce_routing_field(source[field])
        if coerced:
            return coerced
    if field == "group_id":
        return _coerce_routing_field(getattr(request, "chat_id", None))
    return ""


def routing_context_from_request(request: Any) -> RoutingContext:
    """从 AgentRequest 解析企业策略路由上下文。

    各 Channel 入参形态不一，统一在此合并：
    - JSON ``params`` 中的 ``group_id`` / ``bot_id`` / ``user_id``（如联调脚本）；
    - E2A ``metadata`` 扁平字段（如 IM 通道 ``chat_id`` → ``group_id``）；
    - WebChannel URL query（``metadata.query``，``parse_qs`` 列表值）；
    - ``request.chat_id`` 作为 ``group_id`` 兜底。
    """
    return RoutingContext(
        group_id=_resolve_routing_field(request, "group_id"),
        bot_id=_resolve_routing_field(request, "bot_id"),
        user_id=_resolve_routing_field(request, "user_id"),
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
        entity = await GatewayDb.current().fetch_template_by_slot(slot, template_id)
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


def resolve_policy_field(
    policy: dict[str, Any] | None,
    field: str,
    ctx: RoutingContext,
) -> str | None:
    if not policy:
        return None
    raw = str(policy.get(field) or "").strip()
    if not raw:
        return None
    if "${" in raw:
        resolved = expressions.substitute_template(raw, ctx)
        return resolved if resolved else raw
    return raw


async def load_effective_enterprise_config_old(
    request: Any,
    slots: Collection[TemplateRefSlot],
) -> EffectiveEnterpriseConfig | None:
    """按 Service → Agent → Global 三级匹配加载企业配置（旧路径，保留供对照）。

    ``slots`` 指定要解析并加载的 ``template_ref`` 槽位，例如模型槽位、
    ``TemplateRefSlot.EMBEDDING_MODEL``、``TemplateRefSlot.SKILL_WHITELIST``、
    ``TemplateRefSlot.EXTENSION_CONFIG``、``TemplateRefSlot.SERVICE_CONFIG`` 等。
    """
    ctx = routing_context_from_request(request)
    if not slots:
        raise ValueError("slots must not be empty")
    load_slots = frozenset(slot.value for slot in slots)
    match = await _resolve_policy_match(ctx)

    resolved_service_id: str | None = None
    resolved_agent_id: str | None = None
    resolved_workspace_dir: str | None = None
    if TemplateRefSlot.SERVICE_CONFIG in load_slots:
        resolved_service_id = resolve_policy_field(
            match.matched_service,
            "service_id",
            ctx,
        )
        resolved_agent_id = resolve_policy_field(
            match.matched_agent,
            "agent_id",
            ctx,
        )
        resolved_workspace_dir = resolve_policy_field(
            match.matched_agent,
            "workspace_dir",
            ctx,
        )

    send_file_allowed = bool((match.matched_agent or {}).get("send_file_allowed", True))
    has_policy_outcome = bool(
        resolved_service_id
        or resolved_agent_id
        or resolved_workspace_dir
        or send_file_allowed
    )

    filtered_refs = {
        slot: refs
        for slot, refs in match.merged_refs.items()
        if slot in load_slots
    }

    if not filtered_refs:
        logger.warning(
            "[enterprise_config] no template_ref resolved for context %s slots=%s",
            ctx.as_dict(),
            sorted(load_slots),
        )
        if not has_policy_outcome:
            return None
        slot_template_id_map: dict[str, list[str]] = {}
    else:
        slot_template_id_map = await expressions.resolve_slot_template_id_map(
            filtered_refs,
            ctx,
        )
        if not slot_template_id_map:
            logger.warning(
                "[enterprise_config] template_ref slots unresolved for context %s refs=%s",
                ctx.as_dict(),
                filtered_refs,
            )
            if not has_policy_outcome:
                return None

    result = EffectiveEnterpriseConfig(
        routing=ctx,
        template_ref=slot_template_id_map,
        service_policy_id=(
            str(match.matched_service["policy_id"]) if match.matched_service else None
        ),
        agent_policy_id=(
            int(match.matched_agent["id"]) if match.matched_agent else None
        ),
        global_policy_id=(
            int(match.matched_global["id"]) if match.matched_global else None
        ),
        service_id=resolved_service_id,
        agent_id=resolved_agent_id,
        workspace_dir=resolved_workspace_dir,
        send_file_allowed=send_file_allowed,
        service_policy=match.matched_service,
        agent_policy=match.matched_agent,
        global_policy=match.matched_global,
    )

    for slot, template_ids in slot_template_id_map.items():
        entities = await _fetch_slot_entities(slot, template_ids)
        if entities:
            _apply_slot_entities(result, slot, entities)

    if not _any_requested_slot_loaded(result, load_slots):
        logger.warning(
            "[enterprise_config] no template entities loaded "
            "template_ref=%s ctx=%s slots=%s",
            slot_template_id_map,
            ctx.as_dict(),
            sorted(load_slots),
        )
        if not has_policy_outcome:
            return None

    logger.info(
        "[enterprise_config] loaded enterprise config (old): slots=%s payload=%s",
        sorted(load_slots),
        result.as_dict(),
    )
    return result


async def _fetch_instance_agent_resource(resource_id: str) -> dict[str, Any] | None:
    rid = str(resource_id or "").strip()
    if not rid:
        return None
    rows = await GatewayDb.current().list_records(
        "instance_agent_resource",
        filters={"resource_id": rid},
    )
    return rows[0] if rows else None


async def _fetch_agent_template_row(template_id: str) -> dict[str, Any] | None:
    tid = str(template_id or "").strip()
    if not tid:
        return None
    rows = await GatewayDb.current().list_records(
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
    request: Any,
    slots: Collection[TemplateRefSlot],
) -> EffectiveEnterpriseConfig | None:
    """按 ``request.bot_id``（即 ``instance_agent_resource.resource_id``）加载 Agent 实例生效配置。

    读取实例 Agent 资源 → ``agent_template`` → 仅按字面 ``template_id`` 解析
    ``template_ref`` 并加载模型等模板实体。不支持 ``${group::...}`` 等映射表达式。
    """
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
    "POLICY_MATCH_ORDER_BY",
    "load_effective_enterprise_config",
    "load_effective_enterprise_config_old",
    "routing_context_from_request",
)
