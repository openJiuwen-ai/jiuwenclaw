# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""日志脱敏规则：将 Claw Manager 下发的 log_masking_rule 写入 Gateway 本地库。"""

from __future__ import annotations

import logging
from typing import Any

from jiuwenswarm.gateway.config.enterprise.repository import EnterpriseRecordRepository
from jiuwenswarm.infrastructure.log_masking.engine import LogMaskingEngine

from ...infrastructure.repository_access import require_enterprise_repository
from ...infrastructure.utils import assert_jiuwenclaw_id_matches, format_ts, utc_now
from jiuwenswarm.gateway.config.enterprise.tables.application_config_models import LOG_MASKING_RULE_TABLE_DEF
from ...schemas.application_config_schemas import (
    LogMaskingRuleCreateRequest,
    LogMaskingRuleUpdateRequest,
)

_TABLE = LOG_MASKING_RULE_TABLE_DEF.table_name
logger = logging.getLogger(__name__)


def _rule_row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "jiuwenclaw_id": row.get("jiuwenclaw_id"),
        "rule_id": row.get("rule_id"),
        "rule_name": row.get("rule_name"),
        "description": row.get("description"),
        "pattern": row.get("pattern"),
        "replacement": row.get("replacement"),
        "priority": row.get("priority", 0),
        "source": row.get("source"),
        "enabled": bool(row.get("enabled", True)),
        "data": row.get("data"),
        "created_at": format_ts(row.get("created_at")),
        "updated_at": format_ts(row.get("updated_at")),
    }


async def _create_log_masking_rule_record(
    repo: EnterpriseRecordRepository,
    request: LogMaskingRuleCreateRequest,
    *,
    jiuwenclaw_id: str,
) -> dict[str, Any]:
    from jiuwenswarm.infrastructure.log_masking.engine import (
        normalize_replacement,
        normalize_rule_id,
        normalize_source,
        validate_pattern,
    )

    rule_id = normalize_rule_id(request.rule_id)
    dup = await repo.get(rule_id=rule_id)
    if dup is not None:
        raise ValueError(f"rule_id already exists: {rule_id!r}")

    source = normalize_source(request.source)
    now = utc_now()
    row_data: dict[str, Any] = {
        "jiuwenclaw_id": jiuwenclaw_id,
        "rule_id": rule_id,
        "rule_name": request.rule_name,
        "description": request.description,
        "pattern": validate_pattern(
            request.pattern,
            check_structure=False,
            check_performance=False,
        ),
        "replacement": normalize_replacement(request.replacement),
        "priority": int(request.priority),
        "source": source,
        "enabled": bool(request.enabled),
        "data": request.data,
        "created_at": now,
        "updated_at": now,
    }
    record = await repo.create(row_data)
    return _rule_row_to_dict(record)


async def _update_log_masking_rule_record(
    repo: EnterpriseRecordRepository,
    rule_id: str,
    request: LogMaskingRuleUpdateRequest,
    *,
    jiuwenclaw_id: str,
) -> dict[str, Any] | None:
    from jiuwenswarm.infrastructure.log_masking.engine import (
        normalize_replacement,
        normalize_rule_id,
        normalize_source,
        validate_pattern,
    )

    _ = jiuwenclaw_id
    rid = normalize_rule_id(rule_id)
    existing = await repo.get(rule_id=rid)
    if existing is None:
        return None

    updates = request.model_dump(exclude_unset=True)
    if not updates:
        raise ValueError("updates must not be empty")

    if "pattern" in updates and updates["pattern"] is not None:
        updates["pattern"] = validate_pattern(
            updates["pattern"],
            check_structure=False,
            check_performance=False,
        )
    if "replacement" in updates:
        updates["replacement"] = normalize_replacement(updates.get("replacement"))
    if "source" in updates and updates["source"] is not None:
        updates["source"] = normalize_source(updates["source"])
    if "priority" in updates and updates["priority"] is not None:
        updates["priority"] = int(updates["priority"])

    updates["updated_at"] = utc_now()
    updated = await repo.update({"rule_id": rid}, updates)
    if updated is None:
        return None
    return _rule_row_to_dict(updated)


async def _delete_log_masking_rule_record(
    repo: EnterpriseRecordRepository,
    rule_id: str,
    *,
    jiuwenclaw_id: str,
) -> bool:
    from jiuwenswarm.infrastructure.log_masking.engine import normalize_rule_id

    _ = jiuwenclaw_id
    rid = normalize_rule_id(rule_id)
    return await repo.delete(rule_id=rid)


class LogMaskingRuleService:

    async def create(
        self,
        jiuwenclaw_id: str,
        rule: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(rule, dict):
            raise ValueError("log_masking_rule.create requires rule object")
        req = LogMaskingRuleCreateRequest.model_validate(rule)
        jid = str(req.jiuwenclaw_id or jiuwenclaw_id).strip()
        assert_jiuwenclaw_id_matches(jid)
        repo = require_enterprise_repository(_TABLE)
        row = await _create_log_masking_rule_record(repo, req, jiuwenclaw_id=jid)
        await LogMaskingEngine.reload_log_masking_rule(db_authoritative=True)
        result = {"rule_id": row["rule_id"]}
        logger.info(
            "[ManagerConfigReceiver] log_masking_rule create rule_id=%s",
            result["rule_id"],
        )
        return result

    async def update(
        self,
        jiuwenclaw_id: str,
        rule_id: str,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        rid = str(rule_id or "").strip()
        if not rid:
            raise ValueError("log_masking_rule.update requires rule_id")
        if not isinstance(updates, dict) or not updates:
            raise ValueError("log_masking_rule.update requires non-empty updates")
        req = LogMaskingRuleUpdateRequest.model_validate(updates)
        repo = require_enterprise_repository(_TABLE)
        row = await _update_log_masking_rule_record(
            repo, rid, req, jiuwenclaw_id=jiuwenclaw_id
        )
        if row is None:
            raise ValueError(f"log masking rule id={rid!r} not found")
        await LogMaskingEngine.reload_log_masking_rule(db_authoritative=True)
        result = {"rule_id": row["rule_id"]}
        logger.info(
            "[ManagerConfigReceiver] log_masking_rule update rule_id=%s",
            result["rule_id"],
        )
        return result

    async def delete(self, jiuwenclaw_id: str, rule_id: str) -> None:
        rid = str(rule_id or "").strip()
        if not rid:
            raise ValueError("log_masking_rule.delete requires rule_id")
        repo = require_enterprise_repository(_TABLE)
        deleted = await _delete_log_masking_rule_record(
            repo, rid, jiuwenclaw_id=jiuwenclaw_id
        )
        if not deleted:
            raise ValueError(f"log masking rule id={rid!r} not found")
        await LogMaskingEngine.reload_log_masking_rule(db_authoritative=True)
        logger.info(
            "[ManagerConfigReceiver] log_masking_rule delete rule_id=%s",
            rid,
        )
