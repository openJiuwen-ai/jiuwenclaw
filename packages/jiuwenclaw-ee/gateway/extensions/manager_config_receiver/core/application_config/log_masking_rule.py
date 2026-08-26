# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""日志脱敏规则：将 Claw Manager 下发的 log_masking_rule 写入 Gateway 本地库。"""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenswarm.infrastructure.log_masking.engine import LogMaskingEngine

from ...infrastructure.utils import assert_jiuwenclaw_id_matches, format_ts, utc_now
from ...models.application_config_models import LOG_MASKING_RULE_TABLE_DEF
from ...schemas.application_config_schemas import (
    LogMaskingRuleCreateRequest,
    LogMaskingRuleUpdateRequest,
)

_TABLE = LOG_MASKING_RULE_TABLE_DEF.table_name
logger = logging.getLogger(__name__)


def _rule_row_to_dict(obj: Any) -> dict[str, Any]:
    return {
        "id": getattr(obj, "id", None),
        "jiuwenclaw_id": getattr(obj, "jiuwenclaw_id", None),
        "rule_id": getattr(obj, "rule_id", None),
        "rule_name": getattr(obj, "rule_name", None),
        "description": getattr(obj, "description", None),
        "pattern": getattr(obj, "pattern", None),
        "replacement": getattr(obj, "replacement", None),
        "priority": getattr(obj, "priority", 0),
        "source": getattr(obj, "source", None),
        "enabled": bool(getattr(obj, "enabled", True)),
        "data": getattr(obj, "data", None),
        "created_at": format_ts(getattr(obj, "created_at", None)),
        "updated_at": format_ts(getattr(obj, "updated_at", None)),
    }


def _instance_filters(jiuwenclaw_id: str, rule_id: str | None = None) -> dict[str, Any]:
    filters: dict[str, Any] = {"jiuwenclaw_id": jiuwenclaw_id}
    if rule_id is not None:
        filters["rule_id"] = rule_id
    return filters


async def _create_log_masking_rule_record(
    handler: DBHandler,
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
    dup = await handler.get(_TABLE, _instance_filters(jiuwenclaw_id, rule_id))
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
    record = await handler.create(_TABLE, row_data)
    return _rule_row_to_dict(record)


async def _update_log_masking_rule_record(
    handler: DBHandler,
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

    rid = normalize_rule_id(rule_id)
    existing = await handler.get(_TABLE, _instance_filters(jiuwenclaw_id, rid))
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
    updated = await handler.update(_TABLE, _instance_filters(jiuwenclaw_id, rid), updates)
    if updated is None:
        return None
    return _rule_row_to_dict(updated)


async def _delete_log_masking_rule_record(
    handler: DBHandler,
    rule_id: str,
    *,
    jiuwenclaw_id: str,
) -> bool:
    from jiuwenswarm.infrastructure.log_masking.engine import normalize_rule_id

    rid = normalize_rule_id(rule_id)
    return await handler.delete(_TABLE, _instance_filters(jiuwenclaw_id, rid))


async def _upsert_log_masking_rule_record(
    handler: DBHandler,
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

    rid = normalize_rule_id(request.rule_id)
    source = normalize_source(request.source)
    row_data: dict[str, Any] = {
        "jiuwenclaw_id": jiuwenclaw_id,
        "rule_id": rid,
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
    }
    existing = await handler.get(_TABLE, _instance_filters(jiuwenclaw_id, rid))
    if existing is None:
        now = utc_now()
        row_data["created_at"] = now
        row_data["updated_at"] = now
        record = await handler.create(_TABLE, row_data)
        if record is None:
            raise ValueError(f"failed to upsert log masking rule: {rid!r}")
        return _rule_row_to_dict(record)

    row_data["updated_at"] = utc_now()
    updated = await handler.update(
        _TABLE,
        _instance_filters(jiuwenclaw_id, rid),
        row_data,
    )
    if updated is None:
        raise ValueError(f"failed to upsert log masking rule: {rid!r}")
    return _rule_row_to_dict(updated)


async def _sync_log_masking_rules_records(
    handler: DBHandler,
    rules: list[dict[str, Any]],
    *,
    jiuwenclaw_id: str,
) -> dict[str, Any]:
    incoming_rule_ids: set[str] = set()
    synced = 0
    for raw in rules:
        if not isinstance(raw, dict):
            raise ValueError("log_masking_rule.sync rules must be objects")
        req = LogMaskingRuleCreateRequest.model_validate(raw)
        jid = str(req.jiuwenclaw_id or jiuwenclaw_id).strip()
        assert_jiuwenclaw_id_matches(jid)
        from jiuwenswarm.infrastructure.log_masking.engine import normalize_rule_id

        rid = normalize_rule_id(req.rule_id)
        incoming_rule_ids.add(rid)
        await _upsert_log_masking_rule_record(handler, req, jiuwenclaw_id=jid)
        synced += 1

    deleted = 0
    existing_rows = await handler.list_records(_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})
    for row in existing_rows:
        rid = str(getattr(row, "rule_id", "") or "")
        if rid and rid not in incoming_rule_ids:
            if await _delete_log_masking_rule_record(
                handler, rid, jiuwenclaw_id=jiuwenclaw_id
            ):
                deleted += 1

    return {"synced_count": synced, "deleted_count": deleted}


class LogMaskingRuleService:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def create(
        self,
        jiuwenclaw_id: str,
        rule: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(rule, dict):
            raise ValueError("log_masking_rule.create requires rule object")
        req = LogMaskingRuleCreateRequest.model_validate(rule)
        row = await _create_log_masking_rule_record(
            self._handler, req, jiuwenclaw_id=jiuwenclaw_id
        )
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
        row = await _update_log_masking_rule_record(
            self._handler, rid, req, jiuwenclaw_id=jiuwenclaw_id
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
        deleted = await _delete_log_masking_rule_record(
            self._handler, rid, jiuwenclaw_id=jiuwenclaw_id
        )
        if not deleted:
            raise ValueError(f"log masking rule id={rid!r} not found")
        await LogMaskingEngine.reload_log_masking_rule(db_authoritative=True)
        logger.info(
            "[ManagerConfigReceiver] log_masking_rule delete rule_id=%s",
            rid,
        )
