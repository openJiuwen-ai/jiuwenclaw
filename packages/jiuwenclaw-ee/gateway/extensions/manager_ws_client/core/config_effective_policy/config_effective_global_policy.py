# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""全局兜底配置生效策略 WebSocket 同步：将 Claw Manager 下发的 config_effective_global_policies 写入 Gateway 本地库。"""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from ...infrastructure.db import ensure_db_handler
from ...infrastructure.utils import (
    apply_template_ref_to_updates,
    get_jiuwenclaw_id,
    normalize_template_ref,
    parse_iso_datetime,
    utc_now,
)
from ...models.config_effective_policy_models import (
    CONFIG_EFFECTIVE_GLOBAL_POLICY_TABLE_DEF,
)
from ...schemas.config_effective_policy_schemas import (
    ConfigEffectiveGlobalPolicyCreateRequest,
    ConfigEffectiveGlobalPolicyUpdateRequest,
)
from .config_record_ops import (
    apply_create_from_row_builder,
    apply_delete_by_id,
    apply_update_by_id,
    get_row_for_instance,
    sync_records_by_policy_id,
)

_TABLE = CONFIG_EFFECTIVE_GLOBAL_POLICY_TABLE_DEF.table_name
_SECTION = "config_effective_global_policies"
logger = logging.getLogger(__name__)


async def update_config_effective_global_policy_record(
    handler: DBHandler,
    policy_id: int,
    updates: dict[str, Any],
) -> dict[str, Any] | None:
    request = ConfigEffectiveGlobalPolicyUpdateRequest.model_validate(updates)
    existing = await get_row_for_instance(handler, _TABLE, policy_id)
    if existing is None:
        return None

    field_updates = request.model_dump(exclude_unset=True)

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
    req = ConfigEffectiveGlobalPolicyCreateRequest.model_validate(policy)
    return {
        "jiuwenclaw_id": jiuwenclaw_id,
        "policy_id": req.policy_id,
        "policy_name": req.policy_name,
        "policy_desc": req.policy_desc,
        "priority": req.priority,
        "template_ref": normalize_template_ref(req.template_ref),
        "enabled": req.enabled,
        "data": req.data,
        "created_at": parse_iso_datetime(policy.get("created_at")) or now,
        "updated_at": parse_iso_datetime(policy.get("updated_at")) or now,
    }


async def apply_config_effective_global_policy(
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """应用 Claw Manager 经 WebSocket 下发的 config_effective_global_policies 变更。"""
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
            record=payload.get("policy"),
            build_row=_build_row_from_sync_policy,
        )
    elif op == "update":
        await apply_update_by_id(
            handler,
            section=_SECTION,
            row_id=payload.get("id"),
            updates=payload.get("updates"),
            update_record=update_config_effective_global_policy_record,
            not_found_message=f"config effective global policy id={payload.get('id')} not found",
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
        policies = payload.get("policies")
        if not isinstance(policies, list):
            raise ValueError(f"{_SECTION}.sync requires policies array")
        result = await sync_records_by_policy_id(
            handler,
            _TABLE,
            policies,
            jiuwenclaw_id=jiuwenclaw_id,
            build_row=_build_row_from_sync_policy,
        )
    else:
        raise ValueError(f"unsupported {_SECTION}.op: {op!r}")

    logger.info(
        "[ManagerWsClient] config_effective_global_policies sync op=%s id=%s",
        op,
        (result or {}).get("id")
        or payload.get("id")
        or (payload.get("policy") or {}).get("id"),
    )
    return result
