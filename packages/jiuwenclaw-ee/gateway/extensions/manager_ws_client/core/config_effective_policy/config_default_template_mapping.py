# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""默认模板映射 WebSocket 同步：将 Claw Manager 下发的 config_default_template_mappings 写入 Gateway 本地库。"""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from ...infrastructure.db import ensure_db_handler
from ...infrastructure.utils import get_jiuwenclaw_id, parse_iso_datetime, utc_now
from ...models.config_effective_policy_models import (
    CONFIG_DEFAULT_TEMPLATE_MAPPING_TABLE_DEF,
)
from ...schemas.config_effective_policy_schemas import (
    ConfigDefaultTemplateMappingCreateRequest,
    ConfigDefaultTemplateMappingUpdateRequest,
)
from .config_record_ops import (
    apply_create_from_row_builder,
    apply_delete_by_id,
    apply_update_by_id,
    get_row_for_instance,
    sync_records_by_policy_id,
)
from ..enterprise_config.schemas import MAPPING_SCOPE_TYPES

_TABLE = CONFIG_DEFAULT_TEMPLATE_MAPPING_TABLE_DEF.table_name
_SECTION = "config_default_template_mappings"
logger = logging.getLogger(__name__)

_ALLOWED_TEMPLATE_TYPES = frozenset({
    "default_model",
    "video_model",
    "audio_model",
    "vision_model",
    "embedding_model",
    "skill_whitelist",
    "extension_config",
    "service_config",
})


def _validate_template_type(template_type: str) -> str:
    normalized = template_type.strip()
    if normalized not in _ALLOWED_TEMPLATE_TYPES:
        raise ValueError(
            f"template_type must be one of {sorted(_ALLOWED_TEMPLATE_TYPES)}, got {template_type!r}"
        )
    return normalized


def _validate_scope(scope_type: str, scope_id: str) -> tuple[str, str]:
    st = str(scope_type or "").strip().lower()
    if st not in MAPPING_SCOPE_TYPES:
        raise ValueError(
            f"scope_type must be one of {sorted(MAPPING_SCOPE_TYPES)}, got {scope_type!r}"
        )
    sid = str(scope_id or "").strip()
    if not sid:
        raise ValueError("scope_id is required")
    return st, sid


async def update_config_default_template_mapping_record(
    handler: DBHandler,
    mapping_id: int,
    updates: dict[str, Any],
) -> dict[str, Any] | None:
    request = ConfigDefaultTemplateMappingUpdateRequest.model_validate(updates)
    existing = await get_row_for_instance(handler, _TABLE, mapping_id)
    if existing is None:
        return None

    field_updates = request.model_dump(exclude_unset=True)
    if "template_type" in field_updates and field_updates["template_type"] is not None:
        field_updates["template_type"] = _validate_template_type(field_updates["template_type"])
    if "template_id" in field_updates and field_updates["template_id"] is not None:
        field_updates["template_id"] = field_updates["template_id"].strip()
        if not field_updates["template_id"]:
            raise ValueError("template_id cannot be empty")
    if "scope_type" in field_updates and field_updates["scope_type"] is not None:
        field_updates["scope_type"] = str(field_updates["scope_type"]).strip().lower()
    if "scope_id" in field_updates and field_updates["scope_id"] is not None:
        field_updates["scope_id"] = str(field_updates["scope_id"]).strip()

    merged_type = field_updates.get("scope_type", getattr(existing, "scope_type", None))
    merged_id = field_updates.get("scope_id", getattr(existing, "scope_id", None))
    _validate_scope(str(merged_type), str(merged_id))

    if not field_updates:
        raise ValueError("请求未包含任何可更新的业务字段")

    field_updates["updated_at"] = utc_now()
    updated = await handler.update(_TABLE, {"id": mapping_id}, field_updates)
    if updated is None:
        return None
    return {"id": getattr(updated, "id")}


def _build_row_from_sync_mapping(
    mapping: dict[str, Any],
    jiuwenclaw_id: str,
    now: Any,
) -> dict[str, Any]:
    req = ConfigDefaultTemplateMappingCreateRequest.model_validate(mapping)
    scope_type, scope_id = _validate_scope(req.scope_type, req.scope_id)
    template_type = _validate_template_type(req.template_type)
    return {
        "jiuwenclaw_id": jiuwenclaw_id,
        "policy_id": req.policy_id,
        "policy_name": req.policy_name,
        "policy_desc": req.policy_desc,
        "scope_type": scope_type,
        "scope_id": scope_id,
        "priority": req.priority,
        "template_id": req.template_id,
        "template_type": template_type,
        "enabled": req.enabled,
        "data": req.data,
        "created_at": parse_iso_datetime(mapping.get("created_at")) or now,
        "updated_at": parse_iso_datetime(mapping.get("updated_at")) or now,
    }


async def apply_config_default_template_mapping(
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """应用 Claw Manager 经 WebSocket 下发的 config_default_template_mappings 变更。"""
    op = str(payload.get("op") or "").strip()
    if not op:
        raise ValueError(f"{_SECTION}.op is required")

    jiuwenclaw_id = get_jiuwenclaw_id()
    if not jiuwenclaw_id:
        raise ValueError("jiuwenclaw_id is not set")
    handler = await ensure_db_handler()

    if op == "create":
        result = await apply_create_from_row_builder(
            handler,
            _TABLE,
            section=_SECTION,
            jiuwenclaw_id=jiuwenclaw_id,
            record=payload.get("mapping"),
            build_row=_build_row_from_sync_mapping,
            record_label="mapping",
            entity="mapping",
        )
    elif op == "update":
        await apply_update_by_id(
            handler,
            section=_SECTION,
            row_id=payload.get("id"),
            updates=payload.get("updates"),
            update_record=update_config_default_template_mapping_record,
            not_found_message=f"config default template mapping id={payload.get('id')} not found",
        )
        result = None
    elif op == "delete":
        await apply_delete_by_id(
            handler,
            section=_SECTION,
            table=_TABLE,
            row_id=payload.get("id"),
        )
        result = None
    elif op == "sync":
        mappings = payload.get("mappings")
        if not isinstance(mappings, list):
            raise ValueError(f"{_SECTION}.sync requires mappings array")
        result = await sync_records_by_policy_id(
            handler,
            _TABLE,
            mappings,
            jiuwenclaw_id=jiuwenclaw_id,
            build_row=_build_row_from_sync_mapping,
        )
    else:
        raise ValueError(f"unsupported {_SECTION}.op: {op!r}")

    logger.info(
        "[ManagerWsClient] config_default_template_mappings sync op=%s id=%s",
        op,
        (result or {}).get("id")
        or payload.get("id")
        or (payload.get("mapping") or {}).get("id"),
    )
    return result
