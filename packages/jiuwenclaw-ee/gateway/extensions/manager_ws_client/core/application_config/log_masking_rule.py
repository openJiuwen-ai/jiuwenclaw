# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""日志脱敏规则 WebSocket 同步：将 Claw Manager 下发的 log_masking_rule 写入 Gateway 本地库。"""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw.infrastructure.log_masking.engine import LogMaskingEngine

from ...infrastructure.db import ensure_db_handler
from ...infrastructure.utils import assert_jiuwenclaw_id_matches, format_ts, get_jiuwenclaw_id, utc_now
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
    from jiuwenclaw.infrastructure.log_masking.engine import (
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
    from jiuwenclaw.infrastructure.log_masking.engine import (
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
    from jiuwenclaw.infrastructure.log_masking.engine import normalize_rule_id

    rid = normalize_rule_id(rule_id)
    return await handler.delete(_TABLE, _instance_filters(jiuwenclaw_id, rid))


async def _upsert_log_masking_rule_record(
    handler: DBHandler,
    request: LogMaskingRuleCreateRequest,
    *,
    jiuwenclaw_id: str,
) -> dict[str, Any]:
    from jiuwenclaw.infrastructure.log_masking.engine import (
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
        from jiuwenclaw.infrastructure.log_masking.engine import normalize_rule_id

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


async def apply_log_masking_rule(payload: dict[str, Any]) -> dict[str, Any] | None:
    """应用 Claw Manager 经 WebSocket 下发的 log_masking_rule 变更。"""
    op = str(payload.get("op") or "").strip()
    if not op:
        raise ValueError("log_masking_rule.op is required")

    handler = await ensure_db_handler()
    registered = get_jiuwenclaw_id()
    if not registered:
        raise ValueError("jiuwenclaw_id is not set; manager ws register required")

    result: dict[str, Any] | None = None

    if op == "create":
        rule = payload.get("rule")
        if not isinstance(rule, dict):
            raise ValueError("log_masking_rule.create requires rule object")
        req = LogMaskingRuleCreateRequest.model_validate(rule)
        jid = str(req.jiuwenclaw_id or registered).strip()
        assert_jiuwenclaw_id_matches(jid)
        row = await _create_log_masking_rule_record(handler, req, jiuwenclaw_id=jid)
        result = {"rule_id": row["rule_id"]}

    elif op == "update":
        rule_id = str(payload.get("rule_id") or "").strip()
        updates = payload.get("updates")
        if not rule_id:
            raise ValueError("log_masking_rule.update requires rule_id")
        if not isinstance(updates, dict) or not updates:
            raise ValueError("log_masking_rule.update requires non-empty updates")
        req = LogMaskingRuleUpdateRequest.model_validate(updates)
        row = await _update_log_masking_rule_record(
            handler, rule_id, req, jiuwenclaw_id=registered
        )
        if row is None:
            raise ValueError(f"log masking rule id={rule_id!r} not found")
        result = {"rule_id": row["rule_id"]}

    elif op == "delete":
        rule_id = str(payload.get("rule_id") or "").strip()
        if not rule_id:
            raise ValueError("log_masking_rule.delete requires rule_id")
        deleted = await _delete_log_masking_rule_record(
            handler, rule_id, jiuwenclaw_id=registered
        )
        if not deleted:
            raise ValueError(f"log masking rule id={rule_id!r} not found")

    elif op == "sync":
        raw_rules = payload.get("rules")
        if not isinstance(raw_rules, list):
            raise ValueError("log_masking_rule.sync requires rules array")
        result = await _sync_log_masking_rules_records(
            handler,
            raw_rules,
            jiuwenclaw_id=registered,
        )

    else:
        raise ValueError(f"unsupported log_masking_rule.op: {op!r}")

    await LogMaskingEngine.reload_log_masking_rule(db_authoritative=True)

    logger.info(
        "[ManagerWsClient] log_masking_rule sync op=%s rule_id=%s",
        op,
        (result or {}).get("rule_id") or payload.get("rule_id"),
    )
    return result
