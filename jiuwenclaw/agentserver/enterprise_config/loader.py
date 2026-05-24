"""从 Gateway DB 加载企业级生效配置（不应用模型/钩子，仅解析与物化模板）。"""

from __future__ import annotations

from typing import Any

from jiuwenclaw.schema.agent import AgentRequest
from jiuwenclaw.utils import logger

from . import expressions, gateway_db
from .schemas import (
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


def routing_context_from_request(request: AgentRequest) -> RoutingContext:
    """从 ``request.params`` 解析路由上下文（调用方保证字段格式正确）。"""
    p = request.params
    return RoutingContext(
        group_id=p.get("group_id", ""),
        bot_id=p.get("bot_id", ""),
        user_id=p.get("user_id", ""),
    )


async def load_effective_enterprise_config(
    request: AgentRequest,
) -> EffectiveEnterpriseConfig | None:
    """从 ``AgentRequest`` 解析路由上下文，按 Service → Agent → Global 加载企业配置。"""
    ctx = routing_context_from_request(request)

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

    if not merged_refs:
        logger.warning(
            "[enterprise_config] no template_ref resolved for context %s",
            ctx.as_dict(),
        )
        return None

    slot_template_id_map = await expressions.resolve_slot_template_id_map(
        merged_refs, ctx
    )
    if not slot_template_id_map:
        logger.warning(
            "[enterprise_config] template_ref slots unresolved for context %s refs=%s",
            ctx.as_dict(),
            merged_refs,
        )
        return None

    result = EffectiveEnterpriseConfig(
        routing=ctx,
        template_ref=slot_template_id_map,
        service_policy_id=int(matched_service["id"]) if matched_service else None,
        agent_policy_id=int(matched_agent["id"]) if matched_agent else None,
        global_policy_id=int(matched_global["id"]) if matched_global else None,
        service_policy=matched_service,
        agent_policy=matched_agent,
        global_policy=matched_global,
        debug={
            "raw_template_ref": merged_refs,
            "jiuwenclaw_id": gateway_db.resolve_jiuwenclaw_id(),
            "group_id": ctx.group_id,
            "bot_id": ctx.bot_id,
            "user_id": ctx.user_id,
        },
    )

    for slot, template_ids in slot_template_id_map.items():
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
            entities.append(entity)
        if not entities:
            continue
        if slot in MODEL_SLOT_KEYS:
            result.models[slot] = entities
        elif slot == TemplateRefSlot.SKILL_WHITELIST:
            result.skill_whitelist = entities
        elif slot == TemplateRefSlot.EXTENSION_CONFIG:
            result.extension_config = entities

    if (
        not result.models
        and result.skill_whitelist is None
        and result.extension_config is None
    ):
        logger.warning(
            "[enterprise_config] no template entities loaded "
            "template_ref=%s ctx=%s",
            slot_template_id_map,
            ctx.as_dict(),
        )
        return None

    logger.info(
        "[enterprise_config] loaded enterprise config: %s",
        result.as_dict(),
    )
    return result
