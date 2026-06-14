"""配置生效全局兜底策略 config_effective_global_policy 业务逻辑。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.core.instance.instance_service import get_instance_row
from jiuwenclaw_manager.infrastructure.utils import iso_datetime, new_uuid4, utc_now
from jiuwenclaw_manager.manager_ws_server.server import push_config_op
from jiuwenclaw_manager.models.config_effective_policy_models import (
    CONFIG_EFFECTIVE_GLOBAL_POLICY_TABLE_DEF,
)
from jiuwenclaw_manager.core.config_effective_policy.template_ref import (
    apply_template_ref_to_updates,
    normalize_template_ref,
    read_template_ref_from_row,
)
from jiuwenclaw_manager.schemas.config_effective_policy_schemas import (
    ConfigEffectiveGlobalPolicyCreateBody,
    ConfigEffectiveGlobalPolicyOut,
    ConfigEffectiveGlobalPolicyUpdateBody,
)

_GLOBAL_POLICY_TABLE = CONFIG_EFFECTIVE_GLOBAL_POLICY_TABLE_DEF.table_name
_LIST_ALL_CAP = 10_000
# order_by 元组第二项为 is_desc（True=降序），与 SQLAlchemyHandler.list_records 一致。
_DEFAULT_ORDER_BY: list[tuple[str, bool]] = [("priority", False), ("updated_at", False)]
_ALLOWED_SORT_FIELDS = frozenset({"policy_name", "policy_desc", "priority", "updated_at"})


def _resolve_order_by(
    sort_by: str | None,
    sort_order: str | None,
) -> list[tuple[str, bool]]:
    field = (sort_by or "").strip()
    order = (sort_order or "").strip().lower()
    if not field or not order:
        return list(_DEFAULT_ORDER_BY)
    if field not in _ALLOWED_SORT_FIELDS or order not in {"asc", "desc"}:
        return list(_DEFAULT_ORDER_BY)
    is_desc = order == "desc"
    if field == "priority":
        return [(field, is_desc), ("updated_at", is_desc)]
    return [(field, is_desc), ("id", is_desc)]


def _matches_search(row: Any, query: str) -> bool:
    needle = query.strip().lower()
    if not needle:
        return True
    fields = [
        str(getattr(row, "policy_id", "") or ""),
        str(getattr(row, "policy_name", "") or ""),
        str(getattr(row, "policy_desc", "") or ""),
        str(getattr(row, "priority", "") or ""),
    ]
    return any(needle in field.lower() for field in fields)


async def push_config_effective_global_policy_op(
    jiuwenclaw_id: str,
    op: str,
    *,
    policy: dict[str, Any] | None = None,
    row_id: int | None = None,
    updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """推送全局兜底配置生效策略变更（``config.config_effective_global_policies``），返回 config.ack payload。"""
    payload: dict[str, Any] = {"op": op}
    if policy is not None:
        payload["policy"] = policy
    if row_id is not None:
        payload["id"] = row_id
    if updates is not None:
        payload["updates"] = updates
    return await push_config_op(
        jiuwenclaw_id,
        {"config_effective_global_policies": payload},
    )


def _global_policy_pk(jiuwenclaw_id: str, policy_id: int) -> dict[str, Any]:
    return {"jiuwenclaw_id": jiuwenclaw_id, "id": policy_id}


def _row_to_out(row: Any) -> ConfigEffectiveGlobalPolicyOut:
    return ConfigEffectiveGlobalPolicyOut(
        id=row.id,
        jiuwenclaw_id=row.jiuwenclaw_id,
        policy_id=row.policy_id,
        policy_name=row.policy_name,
        policy_desc=row.policy_desc,
        priority=row.priority,
        template_ref=read_template_ref_from_row(row),
        enabled=row.enabled,
        data=row.data,
        created_at=iso_datetime(row.created_at),
        updated_at=iso_datetime(row.updated_at),
    )


class ConfigEffectiveGlobalPolicyService:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def _validate_jiuwenclaw_id(self, jiuwenclaw_id: str) -> str:
        normalized = jiuwenclaw_id.strip()
        if not normalized:
            raise ValueError("jiuwenclaw_id is required")
        inst = await get_instance_row(self._handler, normalized)
        if inst is None:
            raise ValueError(f"unknown jiuwenclaw_id={normalized!r}")
        return normalized

    def _policy_dict_for_push(self, row: dict[str, Any], *, now: datetime) -> dict[str, Any]:
        """构建经 WebSocket 下发给 Gateway 的 policy 对象（不含 id，由 Gateway 自增）。"""
        return {
            "jiuwenclaw_id": row["jiuwenclaw_id"],
            "policy_id": row["policy_id"],
            "policy_name": row.get("policy_name"),
            "policy_desc": row.get("policy_desc"),
            "priority": row["priority"],
            "template_ref": normalize_template_ref(row.get("template_ref")),
            "enabled": row.get("enabled", True),
            "data": row.get("data"),
            "created_at": iso_datetime(row.get("created_at") or now),
            "updated_at": iso_datetime(row.get("updated_at") or now),
        }

    async def create(
        self,
        jiuwenclaw_id: str,
        body: ConfigEffectiveGlobalPolicyCreateBody,
    ) -> ConfigEffectiveGlobalPolicyOut:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)

        now = utc_now()
        row = {
            "jiuwenclaw_id": normalized,
            "policy_id": new_uuid4(),
            "policy_name": body.policy_name,
            "policy_desc": body.policy_desc,
            "priority": body.priority,
            "template_ref": normalize_template_ref(body.template_ref),
            "enabled": body.enabled,
            "data": body.data,
            "created_at": now,
            "updated_at": now,
        }
        ack = await push_config_effective_global_policy_op(
            normalized,
            "create",
            policy=self._policy_dict_for_push(row, now=now),
        )
        ack_result = ack.get("result") if isinstance(ack, dict) else None
        row_id: int | None = None
        if isinstance(ack_result, dict):
            raw_id = ack_result.get("id")
            if raw_id is not None:
                row_id = int(raw_id)
        if row_id is None or row_id < 1:
            raise ValueError(
                "gateway config_effective_global_policies.create returned no id"
            )

        payload = {**row, "id": row_id}
        created = await self._handler.create(_GLOBAL_POLICY_TABLE, payload)
        return _row_to_out(created)

    async def get(
        self, jiuwenclaw_id: str, policy_id: int
    ) -> ConfigEffectiveGlobalPolicyOut | None:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)
        row = await self._handler.get(
            _GLOBAL_POLICY_TABLE, _global_policy_pk(normalized, policy_id)
        )
        if row is None:
            return None
        return _row_to_out(row)

    async def list_policies(
        self,
        jiuwenclaw_id: str,
        *,
        page: int,
        page_size: int,
        enabled: bool | None,
        search: str | None = None,
        sort_by: str | None = None,
        sort_order: str | None = None,
    ) -> dict[str, Any]:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)
        page = max(page, 1)
        page_size = min(max(page_size, 1), 200)
        filters: dict[str, Any] = {"jiuwenclaw_id": normalized}
        if enabled is not None:
            filters["enabled"] = enabled
        order_by = _resolve_order_by(sort_by, sort_order)

        search_query = (search or "").strip()
        if search_query:
            rows = await self._handler.list_records(
                _GLOBAL_POLICY_TABLE,
                filters,
                limit=_LIST_ALL_CAP,
                offset=0,
                order_by=order_by,
            )
            rows = [row for row in rows if _matches_search(row, search_query)]
            total = len(rows)
            offset = (page - 1) * page_size
            page_rows = rows[offset:offset + page_size]
            items = [_row_to_out(row).model_dump(mode="json") for row in page_rows]
            return {
                "items": items,
                "total": total,
                "page": page,
                "page_size": page_size,
            }

        offset = (page - 1) * page_size
        rows = await self._handler.list_records(
            _GLOBAL_POLICY_TABLE,
            filters,
            limit=page_size,
            offset=offset,
            order_by=order_by,
        )
        total = await self._handler.count_records(_GLOBAL_POLICY_TABLE, filters)
        items = [_row_to_out(r).model_dump(mode="json") for r in rows]
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def update(
        self,
        jiuwenclaw_id: str,
        policy_id: int,
        body: ConfigEffectiveGlobalPolicyUpdateBody,
    ) -> ConfigEffectiveGlobalPolicyOut | None:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)

        updates = body.model_dump(exclude_unset=True)

        row = await self._handler.get(
            _GLOBAL_POLICY_TABLE, _global_policy_pk(normalized, policy_id)
        )
        if row is None:
            return None

        if not updates:
            return _row_to_out(row)

        updates = apply_template_ref_to_updates(updates, existing_row=row)

        await push_config_effective_global_policy_op(
            normalized,
            "update",
            row_id=policy_id,
            updates=updates,
        )
        payload = dict(updates)
        payload["updated_at"] = utc_now()
        updated = await self._handler.update(
            _GLOBAL_POLICY_TABLE,
            _global_policy_pk(normalized, policy_id),
            payload,
        )
        if updated is None:
            return None
        return _row_to_out(updated)

    async def delete(
        self,
        jiuwenclaw_id: str,
        policy_id: int,
    ) -> bool:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)
        row = await self._handler.get(
            _GLOBAL_POLICY_TABLE, _global_policy_pk(normalized, policy_id)
        )
        if row is None:
            return False
        await push_config_effective_global_policy_op(
            normalized,
            "delete",
            row_id=policy_id,
        )
        return await self._handler.delete(
            _GLOBAL_POLICY_TABLE, _global_policy_pk(normalized, policy_id)
        )
