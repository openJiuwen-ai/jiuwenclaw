"""配置生效策略解析：Service → Agent → Global → model_template。"""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from typing import Any

from . import expressions
from .routing import RoutingContext
from .settings import enterprise_policy_enabled
from .store import get_store
from .template_ref import (
    SLOT_KEYS,
    read_extension_config_refs,
    read_template_ref_from_policy,
)
from jiuwenclaw.utils import logger

_LOG = "[enterprise_config]"


@dataclass
class EffectiveModelSlots:
    """四模型槽位 + 扩展配置解析结果。"""

    default: dict[str, Any] | None = None
    video: dict[str, Any] | None = None
    audio: dict[str, Any] | None = None
    vision: dict[str, Any] | None = None
    service_policy_id: int | None = None
    agent_policy_id: int | None = None
    global_policy_id: int | None = None
    extension_configs: list[dict[str, Any]] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)


_POLICY_CACHE: dict[str, tuple[float, EffectiveModelSlots]] = {}
_POLICY_CACHE_TTL = 60
_SLOT_KEYS = SLOT_KEYS


def _row_id(row: dict[str, Any] | None) -> int | None:
    if not row:
        return None
    try:
        return int(row["id"])
    except (KeyError, TypeError, ValueError):
        return None


def _normalize_client_provider(model_provider: Any) -> str:
    text = str(model_provider or "").strip()
    if not text:
        return ""
    if text.lower().startswith("llm_"):
        text = text[4:]
    if text.islower():
        return text[:1].upper() + text[1:]
    return text


def _merge_slot(current: str | None, override: str | None) -> str | None:
    if override is not None and str(override).strip():
        return str(override).strip()
    return current


def _cache_key(ctx: RoutingContext) -> str:
    return f"{ctx.jiuwenclaw_id}:{ctx.service_id}:{ctx.agent_id}:{ctx.user_id}"


def invalidate_policy_cache() -> None:
    _POLICY_CACHE.clear()


async def resolve_effective_model_slots(
    ctx: RoutingContext,
) -> EffectiveModelSlots | None:
    if not enterprise_policy_enabled():
        return None
    if not ctx.jiuwenclaw_id:
        logger.warning("%s JIUWENCLAW_PROVISIONED_INSTANCE_ID is not set", _LOG)
        return None

    store = get_store()
    if not await store.ensure_connected():
        logger.warning("%s policy database not available", _LOG)
        return None

    key = _cache_key(ctx)
    now = time.monotonic()
    cached = _POLICY_CACHE.get(key)
    if cached and now - cached[0] < _POLICY_CACHE_TTL:
        logger.debug("%s cache hit for %s", _LOG, key)
        return cached[1]

    result = await _resolve_slots(ctx, store)
    if result is not None:
        _POLICY_CACHE[key] = (now, result)
    return result


async def _resolve_slots(
    ctx: RoutingContext,
    store: Any,
) -> EffectiveModelSlots | None:
    slots = dict.fromkeys(_SLOT_KEYS)
    matched_service: dict[str, Any] | None = None
    matched_agent: dict[str, Any] | None = None
    matched_global: dict[str, Any] | None = None
    extension_refs: list[str] = []

    for rule in await store.list_enabled_service_policies(ctx.jiuwenclaw_id):
        if expressions.service_rule_matches(rule, ctx):
            matched_service = rule
            await _apply_slot_refs(rule, slots, ctx, store)
            extension_refs.extend(read_extension_config_refs(rule))
            break

    if matched_service is not None:
        sp_id = _row_id(matched_service)
        if sp_id is None:
            logger.warning("%s service policy missing id: %s", _LOG, matched_service)
        else:
            for rule in await store.list_enabled_agent_policies(
                ctx.jiuwenclaw_id, sp_id
            ):
                if expressions.agent_rule_matches(rule, ctx):
                    matched_agent = rule
                    await _apply_slot_refs(rule, slots, ctx, store)
                    extension_refs.extend(read_extension_config_refs(rule))
                    break

    global_policy = await store.get_enabled_global_policy(ctx.jiuwenclaw_id)
    if global_policy:
        matched_global = global_policy
        global_refs = read_template_ref_from_policy(global_policy)
        for key in _SLOT_KEYS:
            if slots[key] is None:
                resolved = await expressions.resolve_model_slot_ref(
                    global_refs.get(key),
                    ctx,
                    store=store,
                )
                slots[key] = _merge_slot(slots[key], resolved)
        extension_refs.extend(read_extension_config_refs(global_policy))

    result = EffectiveModelSlots(
        service_policy_id=_row_id(matched_service),
        agent_policy_id=_row_id(matched_agent),
        global_policy_id=_row_id(matched_global),
        debug={
            "service_id": ctx.service_id,
            "group_id": ctx.group_id,
            "bot_id": ctx.bot_id,
            "user_id": ctx.user_id,
            "slot_refs": dict(slots),
            "extension_config_refs": extension_refs,
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
        template = await store.get_model_template(ctx.jiuwenclaw_id, ref)
        if template is None:
            logger.warning(
                "%s model_template not found: ref=%r jiuwenclaw_id=%s",
                _LOG,
                ref,
                ctx.jiuwenclaw_id,
            )
            continue
        setattr(result, slot_name, template)

    if not any((result.default, result.video, result.audio, result.vision)):
        logger.warning("%s no model_template resolved for %s", _LOG, ctx.as_dict())
        return None

    # 按三层策略合并后的 extension_config_refs 查询扩展配置模板
    result.extension_configs = await _resolve_extension_configs(
        ctx.jiuwenclaw_id, extension_refs, store
    )

    logger.info(
        "%s resolved default=%s video=%s audio=%s vision=%s "
        "service_policy=%s agent_policy=%s global_policy=%s extension_configs=%d ctx=%s",
        _LOG,
        (result.default or {}).get("id"),
        (result.video or {}).get("id"),
        (result.audio or {}).get("id"),
        (result.vision or {}).get("id"),
        result.service_policy_id,
        result.agent_policy_id,
        result.global_policy_id,
        len(result.extension_configs),
        ctx.as_dict(),
    )
    return result


async def _apply_slot_refs(
    rule: dict[str, Any],
    slots: dict[str, str | None],
    ctx: RoutingContext,
    store: Any,
) -> None:
    refs = read_template_ref_from_policy(rule)
    for key in _SLOT_KEYS:
        resolved = await expressions.resolve_model_slot_ref(
            refs.get(key), ctx, store=store
        )
        slots[key] = _merge_slot(slots[key], resolved)


async def _resolve_extension_configs(
    jiuwenclaw_id: str,
    refs: list[str],
    store: Any,
) -> list[dict[str, Any]]:
    """根据合并去重后的 extension_config_refs 查询扩展配置模板。"""
    seen: set[str] = set()
    unique_refs: list[str] = []
    for ref in refs:
        if ref not in seen:
            seen.add(ref)
            unique_refs.append(ref)

    configs: list[dict[str, Any]] = []
    for ref in unique_refs:
        template = await store.get_extension_config_template(jiuwenclaw_id, ref)
        if template is None:
            logger.warning(
                "%s extension_config_template not found: ref=%r jiuwenclaw_id=%s",
                _LOG,
                ref,
                jiuwenclaw_id,
            )
            continue
        configs.append(template)
    return configs


def _template_to_model_section(template: dict[str, Any]) -> dict[str, Any]:
    parameters = template.get("parameters")
    if not isinstance(parameters, dict):
        parameters = {}
    model_name = str(template.get("model_id") or "").strip()
    template_id = template.get("id")
    return {
        "display_name": str(template.get("display_name") or "").strip(),
        "template_id": str(template_id) if template_id is not None else "",
        "model_client_config": {
            "client_id": f"enterprise-template-{template_id or model_name}",
            "api_base": template.get("api_base", ""),
            "api_key": template.get("api_key", ""),
            "model_name": model_name,
            "client_provider": _normalize_client_provider(
                template.get("model_provider")
            ),
            "timeout": int(template.get("timeout") or 60),
            "verify_ssl": bool(template.get("verify_ssl", True)),
        },
        "model_config_obj": {"temperature": parameters.get("temperature", 0.95)},
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
