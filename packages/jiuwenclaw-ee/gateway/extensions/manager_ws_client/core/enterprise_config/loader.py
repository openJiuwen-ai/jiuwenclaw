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


async def _fetch_global_policy_refs() -> tuple[dict[str, Any] | None, dict[str, list[str]]]:
    filters: dict[str, Any] = {"enabled": True}
    global_rows = await GatewayDb.current().list_records(
        "config_effective_global_policy",
        filters=filters,
        order_by="priority DESC",
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
        order_by="priority DESC",
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
        sp_id = int(matched_service["id"])
        agent_rules = await GatewayDb.current().list_records(
            "config_effective_agent_policy",
            filters={"enabled": True, "service_policy_id": sp_id},
            order_by="priority DESC",
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


def routing_context_from_request(request: Any) -> RoutingContext:
    """从 ``request.params`` 解析路由上下文（调用方保证字段格式正确）。"""
    p = getattr(request, "params", None) or {}
    if not isinstance(p, dict):
        p = {}
    return RoutingContext(
        group_id=p.get("group_id", ""),
        bot_id=p.get("bot_id", ""),
        user_id=p.get("user_id", ""),
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


def _resolve_policy_field(
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
        return resolved or None
    return raw


async def load_effective_enterprise_config(
    request: Any,
    slots: Collection[TemplateRefSlot],
) -> EffectiveEnterpriseConfig | None:
    """按 Service → Agent → Global 三级匹配加载企业配置。

    ``slots`` 指定要解析并加载的 ``template_ref`` 槽位，例如模型槽位、
    ``TemplateRefSlot.SKILL_WHITELIST``、``TemplateRefSlot.EXTENSION_CONFIG``、
    ``TemplateRefSlot.SERVICE_CONFIG`` 等。
    """
    ctx = routing_context_from_request(request)
    if not slots:
        raise ValueError("slots must not be empty")
    load_slots = frozenset(slot.value for slot in slots)
    match = await _resolve_policy_match(ctx)
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
        return None

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
        return None

    resolved_service_id: str | None = None
    resolved_agent_id: str | None = None
    if TemplateRefSlot.SERVICE_CONFIG in load_slots:
        resolved_service_id = _resolve_policy_field(
            match.matched_service,
            "service_id",
            ctx,
        )
        resolved_agent_id = _resolve_policy_field(
            match.matched_agent,
            "agent_id",
            ctx,
        )

    result = EffectiveEnterpriseConfig(
        routing=ctx,
        template_ref=slot_template_id_map,
        service_policy_id=(
            int(match.matched_service["id"]) if match.matched_service else None
        ),
        agent_policy_id=(
            int(match.matched_agent["id"]) if match.matched_agent else None
        ),
        global_policy_id=(
            int(match.matched_global["id"]) if match.matched_global else None
        ),
        service_id=resolved_service_id,
        agent_id=resolved_agent_id,
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
        return None

    logger.info(
        "[enterprise_config] loaded enterprise config: slots=%s payload=%s",
        sorted(load_slots),
        result.as_dict(),
    )
    return result


__all__ = (
    "load_effective_enterprise_config",
    "routing_context_from_request",
)
