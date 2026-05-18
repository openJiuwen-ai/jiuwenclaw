"""从 Gateway DB 加载企业级生效配置（Service → Agent → Global 三级匹配）。"""

from __future__ import annotations

import os
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.common.utils import logger

from . import expressions, gateway_db
from .schemas import (
    DEFAULT_AGENT_LOAD_SLOTS,
    MODEL_SLOT_KEYS,
    EffectiveEnterpriseConfig,
    RoutingContext,
    TemplateRefSlot,
    normalize_template_ref,
)


def merge_template_ref(*layers: dict[str, list[str]]) -> dict[str, list[str]]:
    """按参数顺序从左到右合并 ``template_ref``；后出现的槽位整组覆盖先前的同名槽位。"""
    merged: dict[str, list[str]] = {}
    for layer in layers:
        merged.update(layer)
    return merged


def fill_missing_template_ref_slots(
    merged: dict[str, list[str]],
    fallback: dict[str, list[str]],
) -> dict[str, list[str]]:
    """将 ``fallback`` 中尚未出现在 ``merged`` 的槽位补入（用于全局兜底按槽位回填）。"""
    if not fallback:
        return merged
    out = dict(merged)
    for slot, refs in fallback.items():
        if slot not in out:
            out[slot] = refs
    return out


async def _fetch_global_policy_refs() -> tuple[dict[str, Any] | None, dict[str, list[str]]]:
    filters: dict[str, Any] = {"enabled": True}
    global_rows = await gateway_db.list_records(
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
    service_rules = await gateway_db.list_records(
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
        agent_rules = await gateway_db.list_records(
            "config_effective_agent_policy",
            filters={"enabled": True, "service_policy_id": sp_id},
            order_by="priority DESC",
        )
        for rule in agent_rules:
            if expressions.agent_rule_matches(rule, ctx):
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


async def load_effective_enterprise_config(
    request: AgentRequest | Any,
    slots: Collection[TemplateRefSlot],
) -> EffectiveEnterpriseConfig | None:
    """按 Service → Agent → Global 三级匹配加载企业配置。

    ``slots`` 指定要解析并加载的 ``template_ref`` 槽位。
    """
    # 企业版特性：仅 AGENT_RUNTIME 开启时生效
    if not os.getenv("AGENT_RUNTIME", "").strip():
        return None

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
        service_policy=match.matched_service,
        agent_policy=match.matched_agent,
        global_policy=match.matched_global,
        debug={
            "raw_template_ref": match.merged_refs,
            "jiuwenclaw_id": gateway_db.resolve_jiuwenclaw_id(),
            "group_id": ctx.group_id,
            "bot_id": ctx.bot_id,
            "user_id": ctx.user_id,
            "load_slots": sorted(load_slots),
        },
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
    "DEFAULT_AGENT_LOAD_SLOTS",
    "EffectiveEnterpriseConfig",
    "TemplateRefSlot",
    "fill_missing_template_ref_slots",
    "load_effective_enterprise_config",
    "merge_template_ref",
    "routing_context_from_request",
)
