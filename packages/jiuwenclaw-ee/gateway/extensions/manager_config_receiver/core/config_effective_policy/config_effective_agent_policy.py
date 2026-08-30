# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Agent 层级配置生效策略：将 Claw Manager 下发的 config_effective_agent_policies 写入 Gateway 本地库。"""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenswarm.gateway.config.enterprise.repository import EnterpriseRecordRepository

from ...infrastructure.repository_access import require_enterprise_repository
from ...infrastructure.utils import (
    apply_template_ref_to_updates,
    normalize_template_ref,
    parse_iso_datetime,
    utc_now,
)
from jiuwenswarm.gateway.config.enterprise.tables.config_effective_policy_models import (
    CONFIG_EFFECTIVE_AGENT_POLICY_TABLE_DEF,
    CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF,
)
from ...schemas.config_effective_policy_schemas import (
    ConfigEffectiveAgentPolicyCreateRequest,
    ConfigEffectiveAgentPolicyUpdateRequest,
)
from ..enterprise_config.expressions import validate_match_expr
from .config_record_ops import (
    apply_create_from_row_builder,
    apply_delete_by_id,
    apply_update_by_id,
    get_row_for_instance,
)

_AGENT_TABLE = CONFIG_EFFECTIVE_AGENT_POLICY_TABLE_DEF.table_name
_SERVICE_TABLE = CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF.table_name
_SECTION = "config_effective_agent_policies"
logger = logging.getLogger(__name__)


async def _validate_service_policy_ref(
    service_repo: EnterpriseRecordRepository,
    *,
    service_policy_id: str,
) -> None:
    normalized_id = str(service_policy_id).strip()
    if not normalized_id:
        raise ValueError("service_policy_id is required")
    rows = await service_repo.list(
        filters={"policy_id": normalized_id},
        limit=1,
    )
    if not rows:
        raise ValueError(f"unknown service_policy_id={normalized_id!r}")


async def _before_agent_policy_create(
    repo: EnterpriseRecordRepository,
    jiuwenclaw_id: str,
    policy: dict[str, Any],
) -> None:
    req = ConfigEffectiveAgentPolicyCreateRequest.model_validate(policy)
    service_repo = require_enterprise_repository(_SERVICE_TABLE)
    await _validate_service_policy_ref(
        service_repo,
        service_policy_id=req.service_policy_id,
    )


async def update_config_effective_agent_policy_record(
    repo: EnterpriseRecordRepository,
    policy_id: int,
    updates: dict[str, Any],
    jiuwenclaw_id: str,
) -> dict[str, Any] | None:
    request = ConfigEffectiveAgentPolicyUpdateRequest.model_validate(updates)
    existing = await get_row_for_instance(repo, policy_id)
    if existing is None:
        return None

    field_updates = request.model_dump(exclude_unset=True)
    if "agent_id" in field_updates and field_updates["agent_id"] is not None:
        field_updates["agent_id"] = field_updates["agent_id"].strip()
        if not field_updates["agent_id"]:
            raise ValueError("agent_id cannot be empty")

    if "workspace_dir" in field_updates:
        raw_ws = field_updates["workspace_dir"]
        field_updates["workspace_dir"] = (
            raw_ws.strip() if isinstance(raw_ws, str) and raw_ws.strip() else None
        )

    if "service_policy_id" in field_updates and field_updates["service_policy_id"] is not None:
        field_updates["service_policy_id"] = str(field_updates["service_policy_id"]).strip()
        if not field_updates["service_policy_id"]:
            raise ValueError("service_policy_id cannot be empty")

    if "match_expr" in field_updates:
        validate_match_expr(field_updates["match_expr"])

    next_service_policy_id = field_updates.get(
        "service_policy_id", existing.get("service_policy_id")
    )
    if "service_policy_id" in field_updates:
        service_repo = require_enterprise_repository(_SERVICE_TABLE)
        await _validate_service_policy_ref(
            service_repo,
            service_policy_id=str(next_service_policy_id),
        )

    if not field_updates:
        raise ValueError("请求未包含任何可更新的业务字段")

    field_updates = apply_template_ref_to_updates(field_updates, existing_row=existing)
    field_updates["updated_at"] = utc_now()
    updated = await repo.update_by_row_id(policy_id, field_updates)
    if updated is None:
        return None
    return {"id": updated.get("id")}


def _build_row_from_sync_policy(
    policy: dict[str, Any],
    jiuwenclaw_id: str,
    now: Any,
) -> dict[str, Any]:
    req = ConfigEffectiveAgentPolicyCreateRequest.model_validate(policy)
    validate_match_expr(req.match_expr)
    return {
        "jiuwenclaw_id": jiuwenclaw_id,
        "policy_id": req.policy_id,
        "policy_name": req.policy_name,
        "policy_desc": req.policy_desc,
        "agent_id": req.agent_id,
        "workspace_dir": req.workspace_dir,
        "service_policy_id": req.service_policy_id,
        "priority": req.priority,
        "match_expr": req.match_expr,
        "template_ref": normalize_template_ref(req.template_ref),
        "send_file_allowed": req.send_file_allowed,
        "enabled": req.enabled,
        "data": req.data,
        "created_at": parse_iso_datetime(policy.get("created_at")) or now,
        "updated_at": parse_iso_datetime(policy.get("updated_at")) or now,
    }


class ConfigEffectiveAgentPolicyService:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def create(
        self,
        jiuwenclaw_id: str,
        policy: dict[str, Any],
    ) -> dict[str, Any]:
        repo = require_enterprise_repository(_AGENT_TABLE)
        result = await apply_create_from_row_builder(
            repo,
            section=_SECTION,
            jiuwenclaw_id=jiuwenclaw_id,
            record=policy,
            build_row=_build_row_from_sync_policy,
            before_create=_before_agent_policy_create,
        )
        logger.info(
            "[ManagerConfigReceiver] config_effective_agent_policies create id=%s",
            result.get("id"),
        )
        return result

    async def update(
        self,
        jiuwenclaw_id: str,
        row_id: Any,
        updates: dict[str, Any],
    ) -> None:
        repo = require_enterprise_repository(_AGENT_TABLE)
        await apply_update_by_id(
            repo,
            section=_SECTION,
            jiuwenclaw_id=jiuwenclaw_id,
            row_id=row_id,
            updates=updates,
            update_record=update_config_effective_agent_policy_record,
            not_found_message=f"config effective agent policy id={row_id} not found",
        )
        logger.info(
            "[ManagerConfigReceiver] config_effective_agent_policies update id=%s",
            row_id,
        )

    async def delete(self, jiuwenclaw_id: str, row_id: Any) -> None:
        _ = jiuwenclaw_id
        repo = require_enterprise_repository(_AGENT_TABLE)
        await apply_delete_by_id(
            repo,
            section=_SECTION,
            row_id=row_id,
        )
        logger.info(
            "[ManagerConfigReceiver] config_effective_agent_policies delete id=%s",
            row_id,
        )
