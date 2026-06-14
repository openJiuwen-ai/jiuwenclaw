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
    ConfigDefaultTemplateMappingUpdateRequest,
)

_TABLE = CONFIG_DEFAULT_TEMPLATE_MAPPING_TABLE_DEF.table_name
logger = logging.getLogger(__name__)

_ALLOWED_TEMPLATE_TYPES = frozenset({
    "default_model",
    "video_model",
    "audio_model",
    "vision_model",
    "skill_whitelist",
    "extension_config",
})


def _optional_key(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _validate_template_type(template_type: str) -> str:
    normalized = template_type.strip()
    if normalized not in _ALLOWED_TEMPLATE_TYPES:
        raise ValueError(
            f"template_type must be one of {sorted(_ALLOWED_TEMPLATE_TYPES)}, got {template_type!r}"
        )
    return normalized


def _validate_dimension_keys(
    user_id: str | None, group_id: str | None
) -> tuple[str | None, str | None]:
    uid = _optional_key(user_id)
    gid = _optional_key(group_id)
    if uid is None and gid is None:
        raise ValueError("at least one of user_id or group_id is required")
    return uid, gid


async def _get_row_for_instance(
    handler: DBHandler,
    mapping_id: int,
    jiuwenclaw_id: str,
) -> Any | None:
    row = await handler.get(_TABLE, {"id": mapping_id})
    if row is None:
        return None
    if getattr(row, "jiuwenclaw_id", None) != jiuwenclaw_id:
        return None
    return row


async def update_config_default_template_mapping_record(
    handler: DBHandler,
    mapping_id: int,
    request: ConfigDefaultTemplateMappingUpdateRequest,
) -> dict[str, Any] | None:
    jiuwenclaw_id = get_jiuwenclaw_id()
    existing = await _get_row_for_instance(handler, mapping_id, jiuwenclaw_id)
    if existing is None:
        return None

    updates = request.model_dump(exclude_unset=True)
    if "template_type" in updates and updates["template_type"] is not None:
        updates["template_type"] = _validate_template_type(updates["template_type"])
    if "template_id" in updates and updates["template_id"] is not None:
        updates["template_id"] = updates["template_id"].strip()
        if not updates["template_id"]:
            raise ValueError("template_id cannot be empty")
    if "user_id" in updates:
        updates["user_id"] = _optional_key(updates["user_id"])
    if "group_id" in updates:
        updates["group_id"] = _optional_key(updates["group_id"])

    merged_user = updates.get("user_id", getattr(existing, "user_id", None))
    merged_group = updates.get("group_id", getattr(existing, "group_id", None))
    _validate_dimension_keys(merged_user, merged_group)

    if not updates:
        raise ValueError("请求未包含任何可更新的业务字段")

    updates["updated_at"] = utc_now()
    updated = await handler.update(_TABLE, {"id": mapping_id}, updates)
    if updated is None:
        return None
    return {"id": getattr(updated, "id")}


async def delete_config_default_template_mapping_record(
    handler: DBHandler,
    mapping_id: int,
) -> bool:
    jiuwenclaw_id = get_jiuwenclaw_id()
    existing = await _get_row_for_instance(handler, mapping_id, jiuwenclaw_id)
    if existing is None:
        return False
    return await handler.delete(_TABLE, {"id": mapping_id})


async def apply_config_default_template_mapping(
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """应用 Claw Manager 经 WebSocket 下发的 config_default_template_mappings 变更。"""
    op = str(payload.get("op") or "").strip()
    if not op:
        raise ValueError("config_default_template_mappings.op is required")

    jiuwenclaw_id = get_jiuwenclaw_id()
    if not jiuwenclaw_id:
        raise ValueError("jiuwenclaw_id is not set")
    handler = await ensure_db_handler()

    if op == "create":
        mapping = payload.get("mapping")
        if not isinstance(mapping, dict):
            raise ValueError(
                "config_default_template_mappings.create requires mapping object"
            )
        policy_name = str(mapping.get("policy_name") or "").strip()
        if not policy_name:
            raise ValueError("policy_name is required")
        user_id, group_id = _validate_dimension_keys(
            mapping.get("user_id"), mapping.get("group_id")
        )
        template_type = _validate_template_type(str(mapping["template_type"]))
        template_id = str(mapping["template_id"]).strip()
        if not template_id:
            raise ValueError("template_id is required")

        priority = int(mapping.get("priority", 0))

        now = utc_now()
        row_data: dict[str, Any] = {
            "jiuwenclaw_id": jiuwenclaw_id,
            "policy_id": mapping["policy_id"],
            "policy_name": policy_name,
            "policy_desc": mapping.get("policy_desc"),
            "user_id": user_id,
            "group_id": group_id,
            "priority": priority,
            "template_id": template_id,
            "template_type": template_type,
            "enabled": bool(mapping.get("enabled", True)),
            "data": mapping.get("data"),
            "created_at": parse_iso_datetime(mapping.get("created_at")) or now,
            "updated_at": parse_iso_datetime(mapping.get("updated_at")) or now,
        }
        created = await handler.create(_TABLE, row_data)
        new_id = int(getattr(created, "id", 0) or 0)
        if new_id < 1:
            raise ValueError(
                "config_default_template_mappings.create: database did not return mapping id"
            )
        result: dict[str, Any] | None = {"id": new_id}

    elif op == "update":
        row_id = payload.get("id")
        updates = payload.get("updates")
        if row_id is None:
            raise ValueError(
                "config_default_template_mappings.update requires id"
            )
        if not isinstance(updates, dict) or not updates:
            raise ValueError(
                "config_default_template_mappings.update requires non-empty updates"
            )
        req = ConfigDefaultTemplateMappingUpdateRequest.model_validate(updates)
        row = await update_config_default_template_mapping_record(
            handler, int(row_id), req
        )
        if row is None:
            raise ValueError(f"config default template mapping id={row_id} not found")
        result = None

    elif op == "delete":
        row_id = payload.get("id")
        if row_id is None:
            raise ValueError(
                "config_default_template_mappings.delete requires id"
            )
        deleted = await delete_config_default_template_mapping_record(
            handler, int(row_id)
        )
        if not deleted:
            raise ValueError(f"config default template mapping id={row_id} not found")
        result = None

    else:
        raise ValueError(f"unsupported config_default_template_mappings.op: {op!r}")

    logger.info(
        "[ManagerWsClient] config_default_template_mappings sync op=%s id=%s",
        op,
        (result or {}).get("id")
        or payload.get("id")
        or (payload.get("mapping") or {}).get("id"),
    )
    return result
