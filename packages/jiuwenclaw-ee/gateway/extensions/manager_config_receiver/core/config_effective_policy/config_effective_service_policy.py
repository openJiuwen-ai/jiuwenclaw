# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Service 层级配置生效策略：将 Claw Manager 下发的 config_effective_service_policies 写入 Gateway 本地库。"""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from ...infrastructure.utils import (
    apply_template_ref_to_updates,
    normalize_template_ref,
    parse_iso_datetime,
    utc_now,
)
from ...models.config_effective_policy_models import (
    CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF,
)
from ...schemas.config_effective_policy_schemas import (
    ConfigEffectiveServicePolicyCreateRequest,
    ConfigEffectiveServicePolicyUpdateRequest,
)
from ..enterprise_config.expressions import validate_match_expr
from .config_record_ops import (
    apply_create_from_row_builder,
    apply_delete_by_id,
    apply_update_by_id,
    get_row_for_instance,
)

_TABLE = CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF.table_name
_SECTION = "config_effective_service_policies"
logger = logging.getLogger(__name__)


async def update_config_effective_service_policy_record(
    handler: DBHandler,
    policy_id: int,
    updates: dict[str, Any],
    jiuwenclaw_id: str,
) -> dict[str, Any] | None:
    request = ConfigEffectiveServicePolicyUpdateRequest.model_validate(updates)
    existing = await get_row_for_instance(handler, _TABLE, policy_id, jiuwenclaw_id)
    if existing is None:
        return None

    field_updates = request.model_dump(exclude_unset=True)
    if "service_id" in field_updates and field_updates["service_id"] is not None:
        field_updates["service_id"] = field_updates["service_id"].strip()
        if not field_updates["service_id"]:
            raise ValueError("service_id cannot be empty")
    if "match_expr" in field_updates:
        validate_match_expr(field_updates["match_expr"])

    if not field_updates:
        raise ValueError("请求未包含任何可更新的业务字段")

    field_updates = apply_template_ref_to_updates(field_updates, existing_row=existing)
    field_updates["updated_at"] = utc_now()
    updated = await handler.update(_TABLE, {"id": policy_id}, field_updates)
    if updated is None:
        return None
    return {"id": getattr(updated, "id")}


def _build_row_from_sync_policy(
    policy: dict[str, Any],
    jiuwenclaw_id: str,
    now: Any,
) -> dict[str, Any]:
    req = ConfigEffectiveServicePolicyCreateRequest.model_validate(policy)
    validate_match_expr(req.match_expr)
    return {
        "jiuwenclaw_id": jiuwenclaw_id,
        "policy_id": req.policy_id,
        "policy_name": req.policy_name,
        "policy_desc": req.policy_desc,
        "service_id": req.service_id,
        "priority": req.priority,
        "match_expr": req.match_expr,
        "template_ref": normalize_template_ref(req.template_ref),
        "enabled": req.enabled,
        "data": req.data,
        "created_at": parse_iso_datetime(policy.get("created_at")) or now,
        "updated_at": parse_iso_datetime(policy.get("updated_at")) or now,
    }


class ConfigEffectiveServicePolicyService:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def create(
        self,
        jiuwenclaw_id: str,
        policy: dict[str, Any],
    ) -> dict[str, Any]:
        result = await apply_create_from_row_builder(
            self._handler,
            _TABLE,
            section=_SECTION,
            jiuwenclaw_id=jiuwenclaw_id,
            record=policy,
            build_row=_build_row_from_sync_policy,
        )
        logger.info(
            "[ManagerConfigReceiver] config_effective_service_policies create id=%s",
            result.get("id"),
        )
        return result

    async def update(
        self,
        jiuwenclaw_id: str,
        row_id: Any,
        updates: dict[str, Any],
    ) -> None:
        await apply_update_by_id(
            self._handler,
            section=_SECTION,
            jiuwenclaw_id=jiuwenclaw_id,
            row_id=row_id,
            updates=updates,
            update_record=update_config_effective_service_policy_record,
            not_found_message=f"config effective service policy id={row_id} not found",
        )
        logger.info(
            "[ManagerConfigReceiver] config_effective_service_policies update id=%s",
            row_id,
        )

    async def delete(self, jiuwenclaw_id: str, row_id: Any) -> None:
        await apply_delete_by_id(
            self._handler,
            section=_SECTION,
            table=_TABLE,
            jiuwenclaw_id=jiuwenclaw_id,
            row_id=row_id,
        )
        logger.info(
            "[ManagerConfigReceiver] config_effective_service_policies delete id=%s",
            row_id,
        )
