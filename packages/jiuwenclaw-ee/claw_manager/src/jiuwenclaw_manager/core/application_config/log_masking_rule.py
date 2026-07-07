# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved
"""日志脱敏规则：Manager DB + Gateway WS 推送。"""

from __future__ import annotations

from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.infrastructure.common import resolve_order_by
from jiuwenclaw_manager.infrastructure.utils import format_ts, new_uuid4, utc_now
from jiuwenclaw_manager.manager_ws_server.server import push_config_op
from jiuwenclaw_manager.models.application_config_models import LOG_MASKING_RULE_TABLE_DEF
from jiuwenclaw_manager.schemas.application_config_schemas import (
    LogMaskingRuleCreateBody,
    LogMaskingRuleListQuery,
    LogMaskingRuleOut,
    LogMaskingRuleUpdateBody,
)
from jiuwenclaw.infrastructure.log_masking.engine import (
    DEFAULT_REPLACEMENT,
    LogMaskingEngine,
    normalize_replacement,
    normalize_rule_id,
    validate_pattern,
)


def _builtin_seed_rows(jiuwenclaw_id: str) -> list[dict]:
    return [
        {
            "jiuwenclaw_id": jiuwenclaw_id,
            "rule_id": rule.rule_id,
            "rule_name": rule.name,
            "description": f"builtin seed: {rule.rule_id}",
            "pattern": rule.pattern.pattern,
            "replacement": rule.replacement,
            "priority": rule.priority,
            "source": "builtin",
            "enabled": True,
            "data": None,
        }
        for rule in LogMaskingEngine.compiled_default_rules()
    ]


_REST_LOG_MASKING_SOURCE = "custom"

_TABLE = LOG_MASKING_RULE_TABLE_DEF.table_name
_LIST_ALL_CAP = 10_000
_ALLOWED_SORT_FIELDS = frozenset({
    "rule_name",
    "description",
    "pattern",
    "replacement",
    "priority",
    "updated_at",
})
_DEFAULT_LOG_MASKING_ORDER_BY: list[tuple[str, bool]] = [
    ("priority", True),
    ("id", False),
]


def _matches_search(row: Any, query: str) -> bool:
    needle = query.strip().lower()
    if not needle:
        return True
    fields = [
        str(getattr(row, "rule_id", "") or ""),
        str(getattr(row, "rule_name", "") or ""),
        str(getattr(row, "description", "") or ""),
        str(getattr(row, "pattern", "") or ""),
        str(getattr(row, "replacement", "") or ""),
        str(getattr(row, "priority", "") or ""),
        str(getattr(row, "source", "") or ""),
    ]
    return any(needle in field.lower() for field in fields)


async def seed_builtin_log_masking_rules(
    handler: DBHandler,
    jiuwenclaw_id: str,
) -> int:
    """为实例写入内置 ``log_masking_rule`` 种子（仅 Manager DB，不 push Gateway）。"""
    jid = str(jiuwenclaw_id or "").strip()
    if not jid:
        return 0

    existing_rows = await handler.list_records(_TABLE, {"jiuwenclaw_id": jid})
    existing_rule_ids = {
        str(getattr(row, "rule_id", "") or "") for row in existing_rows
    }

    now = utc_now()
    created = 0
    for row in _builtin_seed_rows(jid):
        rule_id = str(row.get("rule_id") or "")
        if not rule_id or rule_id in existing_rule_ids:
            continue
        payload = {
            **row,
            "replacement": str(row.get("replacement") or DEFAULT_REPLACEMENT),
            "created_at": now,
            "updated_at": now,
        }
        if await handler.create(_TABLE, payload) is not None:
            created += 1
            existing_rule_ids.add(rule_id)
    return created


def _row_to_out(obj: Any) -> LogMaskingRuleOut:
    return LogMaskingRuleOut(
        id=int(getattr(obj, "id", 0) or 0),
        jiuwenclaw_id=str(getattr(obj, "jiuwenclaw_id", "")),
        rule_id=str(getattr(obj, "rule_id", "")),
        rule_name=str(getattr(obj, "rule_name", "")),
        description=getattr(obj, "description", None),
        pattern=str(getattr(obj, "pattern", "")),
        replacement=str(getattr(obj, "replacement", "") or DEFAULT_REPLACEMENT),
        priority=int(getattr(obj, "priority", 0) or 0),
        source=str(getattr(obj, "source", "custom")),
        enabled=bool(getattr(obj, "enabled", True)),
        data=getattr(obj, "data", None),
        created_at=format_ts(getattr(obj, "created_at", None)) or None,
        updated_at=format_ts(getattr(obj, "updated_at", None)) or None,
    )


def _row_to_sync_payload(obj: Any) -> dict[str, Any]:
    """WS ``sync`` 用：不含 ``id`` / 时间戳，Gateway 按 ``rule_id`` upsert。"""
    out = _row_to_out(obj)
    data = out.model_dump(mode="json")
    for key in ("id", "created_at", "updated_at"):
        data.pop(key, None)
    return data


async def push_log_masking_rule_op(
    jiuwenclaw_id: str,
    op: str,
    *,
    rule: dict[str, Any] | None = None,
    rule_id: str | None = None,
    updates: dict[str, Any] | None = None,
    rules: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"op": op}
    if rule is not None:
        payload["rule"] = rule
    if rule_id is not None:
        payload["rule_id"] = rule_id
    if updates is not None:
        payload["updates"] = updates
    if rules is not None:
        payload["rules"] = rules
    return await push_config_op(jiuwenclaw_id, {"log_masking_rule": payload})


async def push_log_masking_rules_sync_to_gateway(
    handler: DBHandler,
    jiuwenclaw_id: str,
) -> dict[str, Any]:
    """Gateway 注册后：将 MDB 中该实例全部规则 bulk push 到 GDB（``op=sync``）。"""
    jid = str(jiuwenclaw_id or "").strip()
    if not jid:
        raise ValueError("jiuwenclaw_id is required")

    rows = await handler.list_records(_TABLE, {"jiuwenclaw_id": jid})
    rules = [_row_to_sync_payload(row) for row in rows]
    return await push_log_masking_rule_op(jid, "sync", rules=rules)


class LogMaskingRuleService:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def create(
        self,
        jiuwenclaw_id: str,
        body: LogMaskingRuleCreateBody,
    ) -> LogMaskingRuleOut:
        jid = str(jiuwenclaw_id).strip()
        rule_id = normalize_rule_id(new_uuid4())
        now = utc_now()
        row_data = {
            "jiuwenclaw_id": jid,
            "rule_id": rule_id,
            "rule_name": body.rule_name.strip(),
            "description": body.description,
            "pattern": validate_pattern(body.pattern),
            "replacement": normalize_replacement(body.replacement),
            "priority": int(body.priority),
            "source": _REST_LOG_MASKING_SOURCE,
            "enabled": bool(body.enabled),
            "data": body.data,
            "created_at": now,
            "updated_at": now,
        }

        created = await self._handler.create(_TABLE, row_data)
        if created is None:
            raise ValueError("failed to create log masking rule")

        try:
            await push_log_masking_rule_op(
                jid,
                "create",
                rule={
                    **row_data,
                    "created_at": format_ts(now),
                    "updated_at": format_ts(now),
                },
            )
        except Exception as exc:
            await self._handler.delete(
                _TABLE, {"jiuwenclaw_id": jid, "rule_id": rule_id}
            )
            raise ValueError(f"failed to sync to gateway: {exc}") from exc

        return _row_to_out(created)

    async def list(
        self,
        jiuwenclaw_id: str,
        query: LogMaskingRuleListQuery,
    ) -> dict[str, Any]:
        jid = str(jiuwenclaw_id).strip()
        filters: dict[str, Any] = {"jiuwenclaw_id": jid}
        if query.enabled is not None:
            filters["enabled"] = query.enabled
        source = (query.source or "").strip()
        if source:
            filters["source"] = source

        order_by = resolve_order_by(
            query.sort_by,
            query.sort_order,
            allowed_sort_fields=_ALLOWED_SORT_FIELDS,
            default_order_by=_DEFAULT_LOG_MASKING_ORDER_BY,
        )
        rows = await self._handler.list_records(
            _TABLE,
            filters,
            limit=_LIST_ALL_CAP,
            offset=0,
            order_by=order_by,
        )

        search_query = (query.search or "").strip()
        if search_query:
            rows = [row for row in rows if _matches_search(row, search_query)]

        items = [_row_to_out(r).model_dump(mode="json") for r in rows]
        return {"items": items}

    async def get(self, jiuwenclaw_id: str, rule_id: str) -> LogMaskingRuleOut | None:
        jid = str(jiuwenclaw_id).strip()
        rid = normalize_rule_id(rule_id)
        row = await self._handler.get(_TABLE, {"jiuwenclaw_id": jid, "rule_id": rid})
        if row is None:
            return None
        return _row_to_out(row)

    async def update(
        self,
        jiuwenclaw_id: str,
        rule_id: str,
        body: LogMaskingRuleUpdateBody,
    ) -> LogMaskingRuleOut | None:
        jid = str(jiuwenclaw_id).strip()
        rid = normalize_rule_id(rule_id)
        updates = body.model_dump(exclude_unset=True)
        if not updates:
            return await self.get(jid, rid)

        existing = await self._handler.get(
            _TABLE, {"jiuwenclaw_id": jid, "rule_id": rid}
        )
        if existing is None:
            return None

        if "pattern" in updates and updates["pattern"] is not None:
            existing_source = str(getattr(existing, "source", "custom") or "custom")
            is_custom = existing_source.strip().lower() == "custom"
            updates["pattern"] = validate_pattern(
                updates["pattern"],
                check_structure=is_custom,
                check_performance=is_custom,
            )
        if "replacement" in updates:
            updates["replacement"] = normalize_replacement(updates.get("replacement"))
        updates.pop("source", None)
        if "priority" in updates and updates["priority"] is not None:
            updates["priority"] = int(updates["priority"])

        try:
            await push_log_masking_rule_op(
                jid, "update", rule_id=rid, updates=updates
            )
        except Exception as exc:
            raise ValueError(f"failed to sync to gateway: {exc}") from exc

        updates["updated_at"] = utc_now()
        updated = await self._handler.update(
            _TABLE, {"jiuwenclaw_id": jid, "rule_id": rid}, updates
        )
        if updated is None:
            return None
        return _row_to_out(updated)

    async def delete(self, jiuwenclaw_id: str, rule_id: str) -> None:
        jid = str(jiuwenclaw_id).strip()
        rid = normalize_rule_id(rule_id)
        existing = await self._handler.get(
            _TABLE, {"jiuwenclaw_id": jid, "rule_id": rid}
        )
        if existing is None:
            raise ValueError("log masking rule not found")

        try:
            await push_log_masking_rule_op(jid, "delete", rule_id=rid)
        except Exception as exc:
            raise ValueError(f"failed to sync to gateway: {exc}") from exc

        deleted = await self._handler.delete(
            _TABLE, {"jiuwenclaw_id": jid, "rule_id": rid}
        )
        if not deleted:
            raise ValueError("failed to delete log masking rule")
