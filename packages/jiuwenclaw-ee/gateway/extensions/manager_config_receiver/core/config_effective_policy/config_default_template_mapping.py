# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""默认模板映射：将 Claw Manager 下发的 config_default_template_mappings 写入 Gateway 本地库。"""

from __future__ import annotations

import logging
from typing import Any

from jiuwenswarm.gateway.config.enterprise.repository import EnterpriseRecordRepository

from ...infrastructure.repository_access import require_enterprise_repository
from ...infrastructure.utils import parse_iso_datetime, utc_now
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
    repo: EnterpriseRecordRepository,
    mapping_id: int,
    updates: dict[str, Any],
    jiuwenclaw_id: str,
) -> dict[str, Any] | None:
    request = ConfigDefaultTemplateMappingUpdateRequest.model_validate(updates)
    existing = await get_row_for_instance(repo, mapping_id)
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

    merged_type = field_updates.get("scope_type", existing.get("scope_type"))
    merged_id = field_updates.get("scope_id", existing.get("scope_id"))
    _validate_scope(str(merged_type), str(merged_id))

    if not field_updates:
        raise ValueError("请求未包含任何可更新的业务字段")

    field_updates["updated_at"] = utc_now()
    updated = await repo.update_by_row_id(mapping_id, field_updates)
    if updated is None:
        return None
    return {"id": updated.get("id")}


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


class ConfigDefaultTemplateMappingService:

    async def create(
        self,
        jiuwenclaw_id: str,
        mapping: dict[str, Any],
    ) -> dict[str, Any]:
        repo = require_enterprise_repository(_TABLE)
        result = await apply_create_from_row_builder(
            repo,
            section=_SECTION,
            jiuwenclaw_id=jiuwenclaw_id,
            record=mapping,
            build_row=_build_row_from_sync_mapping,
            record_label="mapping",
            entity="mapping",
        )
        logger.info(
            "[ManagerConfigReceiver] config_default_template_mappings create id=%s",
            result.get("id"),
        )
        return result

    async def update(
        self,
        jiuwenclaw_id: str,
        row_id: Any,
        updates: dict[str, Any],
    ) -> None:
        repo = require_enterprise_repository(_TABLE)
        await apply_update_by_id(
            repo,
            section=_SECTION,
            jiuwenclaw_id=jiuwenclaw_id,
            row_id=row_id,
            updates=updates,
            update_record=update_config_default_template_mapping_record,
            not_found_message=f"config default template mapping id={row_id} not found",
        )
        logger.info(
            "[ManagerConfigReceiver] config_default_template_mappings update id=%s",
            row_id,
        )

    async def delete(self, jiuwenclaw_id: str, row_id: Any) -> None:
        _ = jiuwenclaw_id
        repo = require_enterprise_repository(_TABLE)
        await apply_delete_by_id(
            repo,
            section=_SECTION,
            row_id=row_id,
        )
        logger.info(
            "[ManagerConfigReceiver] config_default_template_mappings delete id=%s",
            row_id,
        )
