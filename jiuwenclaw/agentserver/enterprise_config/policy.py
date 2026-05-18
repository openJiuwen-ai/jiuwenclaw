"""配置生效策略解析：Service → Agent → Global → 模板。"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from typing import Any

from . import expressions, gateway_db
from .routing import RoutingContext
from jiuwenclaw.utils import logger


@dataclass
class EffectiveModelSlots:
    """四模型槽位解析结果（值为 ``model_template`` 行字典）。"""

    default: dict[str, Any] | None = None
    video: dict[str, Any] | None = None
    audio: dict[str, Any] | None = None
    vision: dict[str, Any] | None = None
    service_policy_id: int | None = None
    agent_policy_id: int | None = None
    global_policy_id: int | None = None
    debug: dict[str, Any] = field(default_factory=dict)


def enterprise_policy_enabled() -> bool:
    flag = os.getenv("JIUWENCLAW_ENTERPRISE_MODEL_POLICY", "").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    if flag in ("1", "true", "yes", "on"):
        return True
    instance_id = os.getenv("JIUWENCLAW_PROVISIONED_INSTANCE_ID", "").strip()
    if not instance_id:
        return False
    return gateway_db.resolve_gateway_db_path() is not None


def _merge_slot(
    current: str | None,
    override: str | None,
) -> str | None:
    if override is not None and str(override).strip():
        return str(override).strip()
    return current


async def resolve_effective_model_slots(
    ctx: RoutingContext,
) -> EffectiveModelSlots | None:
    if not enterprise_policy_enabled():
        return None
    if not ctx.jiuwenclaw_id:
        logger.warning("[enterprise_config] JIUWENCLAW_PROVISIONED_INSTANCE_ID is not set")
        return None
    if not gateway_db.resolve_gateway_db_path():
        logger.warning("[enterprise_config] gateway agent_client.db not found")
        return None

    service_rules = await gateway_db.fetch_all(
        "config_effective_service_policy",
        jiuwenclaw_id=ctx.jiuwenclaw_id,
        extra_where="enabled = 1",
    )
    service_rules.sort(key=lambda r: int(r.get("priority") or 0), reverse=True)

    slots = {
        "default_model": None,
        "video_model": None,
        "audio_model": None,
        "vision_model": None,
    }
    matched_service: dict[str, Any] | None = None
    matched_agent: dict[str, Any] | None = None
    matched_global: dict[str, Any] | None = None

    for rule in service_rules:
        if expressions.service_rule_matches(rule, ctx):
            matched_service = rule
            for key in slots:
                resolved = await expressions.resolve_model_slot_ref(
                    rule.get(key), ctx, jiuwenclaw_id=ctx.jiuwenclaw_id
                )
                slots[key] = _merge_slot(slots[key], resolved)
            break

    if matched_service is not None:
        sp_id = int(matched_service["id"])
        agent_rules = await gateway_db.fetch_all(
            "config_effective_agent_policy",
            jiuwenclaw_id=ctx.jiuwenclaw_id,
            extra_where="enabled = 1 AND service_policy_id = ?",
            extra_params=(sp_id,),
        )
        agent_rules.sort(key=lambda r: int(r.get("priority") or 0), reverse=True)
        for rule in agent_rules:
            if expressions.agent_rule_matches(rule, ctx):
                matched_agent = rule
                for key in slots:
                    resolved = await expressions.resolve_model_slot_ref(
                        rule.get(key), ctx, jiuwenclaw_id=ctx.jiuwenclaw_id
                    )
                    slots[key] = _merge_slot(slots[key], resolved)
                break
    else:
        global_rows = await gateway_db.fetch_all(
            "config_effective_global_policy",
            jiuwenclaw_id=ctx.jiuwenclaw_id,
            extra_where="enabled = 1",
        )
        if global_rows:
            matched_global = global_rows[0]
            for key in slots:
                resolved = await expressions.resolve_model_slot_ref(
                    matched_global.get(key), ctx, jiuwenclaw_id=ctx.jiuwenclaw_id
                )
                slots[key] = _merge_slot(slots[key], resolved)

    result = EffectiveModelSlots(
        service_policy_id=int(matched_service["id"]) if matched_service else None,
        agent_policy_id=int(matched_agent["id"]) if matched_agent else None,
        global_policy_id=int(matched_global["id"]) if matched_global else None,
        debug={
            "service_id": ctx.service_id,
            "group_id": ctx.group_id,
            "bot_id": ctx.bot_id,
            "user_id": ctx.user_id,
            "slot_refs": dict(slots),
        },
    )

    for slot_name, ref_key in (
        ("default", "default_model"),
        ("video", "video_model"),
        ("audio", "audio_model"),
        ("vision", "vision_model"),
    ):
        ref = slots.get(ref_key)
        if not ref:
            continue
        template = await gateway_db.fetch_model_template(ctx.jiuwenclaw_id, ref)
        if template is None:
            logger.warning(
                "[enterprise_config] model_template not found: ref=%r jiuwenclaw_id=%s",
                ref,
                ctx.jiuwenclaw_id,
            )
            continue
        setattr(result, slot_name, template)

    if not any((result.default, result.video, result.audio, result.vision)):
        logger.warning(
            "[enterprise_config] no model_template resolved for context %s",
            ctx.as_dict(),
        )
        return None

    logger.info(
        "[enterprise_config] resolved models: default=%s video=%s audio=%s vision=%s "
        "service_policy=%s agent_policy=%s global_policy=%s ctx=%s",
        (result.default or {}).get("id"),
        (result.video or {}).get("id"),
        (result.audio or {}).get("id"),
        (result.vision or {}).get("id"),
        result.service_policy_id,
        result.agent_policy_id,
        result.global_policy_id,
        ctx.as_dict(),
    )
    return result


def _template_to_model_section(template: dict[str, Any]) -> dict[str, Any]:
    parameters = template.get("parameters")
    if not isinstance(parameters, dict):
        parameters = {}
    temperature = parameters.get("temperature", 0.95)
    model_name = str(template.get("model_id") or "").strip()
    display_name = str(template.get("display_name") or "").strip()
    template_id = template.get("id")
    return {
        "display_name": display_name,
        "template_id": str(template_id) if template_id is not None else "",
        "model_client_config": {
            "api_base": template.get("api_base", ""),
            "api_key": template.get("api_key", ""),
            "model_name": model_name,
            "client_provider": template.get("model_provider", ""),
            "timeout": int(template.get("timeout") or 60),
            "verify_ssl": bool(template.get("verify_ssl", True)),
        },
        "model_config_obj": {"temperature": temperature},
    }


def apply_effective_models_to_config(
    config_base: dict[str, Any],
    effective: EffectiveModelSlots,
) -> dict[str, Any]:
    """将解析结果写入 ``config_base['models']`` 段（用于 ``_create_model``）。"""
    merged = copy.deepcopy(config_base)
    models = dict(merged.get("models") or {})

    if effective.default:
        models["default"] = _template_to_model_section(effective.default)
    if effective.video:
        models["video"] = _template_to_model_section(effective.video)
    if effective.audio:
        models["audio"] = _template_to_model_section(effective.audio)
    if effective.vision:
        models["vision"] = _template_to_model_section(effective.vision)

    defaults_list = []
    for key in ("default", "video", "audio", "vision"):
        section = models.get(key)
        if isinstance(section, dict):
            defaults_list.append(section)
    if defaults_list:
        models["defaults"] = defaults_list

    merged["models"] = models
    return merged
