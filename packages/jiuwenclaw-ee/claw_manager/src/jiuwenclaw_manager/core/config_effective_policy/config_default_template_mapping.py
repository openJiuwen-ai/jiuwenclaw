"""用户与群组默认模板映射 config_default_template_mapping 业务逻辑。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.core.template.push_template_to_gateway import (
    sync_gateway_templates_after_mapping_change,
)
from jiuwenclaw_manager.infrastructure.common import (
    DEFAULT_POLICY_ORDER_BY,
    resolve_order_by,
)
from jiuwenclaw_manager.infrastructure.jiuwenclaw_id import validate_jiuwenclaw_id
from jiuwenclaw_manager.infrastructure.utils import (
    iso_datetime,
    new_uuid4,
    strip_optional,
    utc_now,
)
from jiuwenclaw_manager.manager_ws_server.server import push_config_op
from jiuwenclaw_manager.models.config_effective_policy_models import (
    CONFIG_DEFAULT_TEMPLATE_MAPPING_TABLE_DEF,
)
from jiuwenclaw_manager.schemas.config_effective_policy_schemas import (
    ConfigDefaultTemplateMappingCreateBody,
    ConfigDefaultTemplateMappingListQuery,
    ConfigDefaultTemplateMappingOut,
    ConfigDefaultTemplateMappingUpdateBody,
)

_TEMPLATE_MAPPING_TABLE = CONFIG_DEFAULT_TEMPLATE_MAPPING_TABLE_DEF.table_name
_LIST_ALL_CAP = 10_000
_ALLOWED_SORT_FIELDS = frozenset({
    "policy_name",
    "policy_desc",
    "priority",
    "user_id",
    "group_id",
    "template_type",
    "template_id",
    "updated_at",
})


def _matches_search(row: Any, query: str) -> bool:
    needle = query.strip().lower()
    if not needle:
        return True
    fields = [
        str(getattr(row, "policy_id", "") or ""),
        str(getattr(row, "policy_name", "") or ""),
        str(getattr(row, "policy_desc", "") or ""),
        str(getattr(row, "user_id", "") or ""),
        str(getattr(row, "group_id", "") or ""),
        str(getattr(row, "template_type", "") or ""),
        str(getattr(row, "template_id", "") or ""),
        str(getattr(row, "priority", "") or ""),
    ]
    return any(needle in field.lower() for field in fields)


async def push_config_default_template_mapping_op(
    jiuwenclaw_id: str,
    op: str,
    *,
    mapping: dict[str, Any] | None = None,
    row_id: int | None = None,
    updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """推送默认模板映射变更（``config.config_default_template_mappings``），返回 config.ack payload。"""
    payload: dict[str, Any] = {"op": op}
    if mapping is not None:
        payload["mapping"] = mapping
    if row_id is not None:
        payload["id"] = row_id
    if updates is not None:
        payload["updates"] = updates
    return await push_config_op(
        jiuwenclaw_id,
        {"config_default_template_mappings": payload},
    )


def _mapping_pk(jiuwenclaw_id: str, mapping_id: int) -> dict[str, Any]:
    return {"jiuwenclaw_id": jiuwenclaw_id, "id": mapping_id}


def _validate_dimension_keys(
    user_id: str | None, group_id: str | None
) -> tuple[str | None, str | None]:
    uid = strip_optional(user_id)
    gid = strip_optional(group_id)
    if uid is None and gid is None:
        raise ValueError("at least one of user_id or group_id is required")
    return uid, gid


def _row_to_out(row: Any) -> ConfigDefaultTemplateMappingOut:
    return ConfigDefaultTemplateMappingOut(
        id=row.id,
        jiuwenclaw_id=row.jiuwenclaw_id,
        policy_id=row.policy_id,
        policy_name=row.policy_name,
        policy_desc=row.policy_desc,
        user_id=row.user_id,
        group_id=row.group_id,
        priority=row.priority,
        template_id=row.template_id,
        template_type=row.template_type,
        enabled=row.enabled,
        data=row.data,
        created_at=iso_datetime(row.created_at),
        updated_at=iso_datetime(row.updated_at),
    )


class ConfigDefaultTemplateMappingService:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    def _mapping_dict_for_push(
        self, row: dict[str, Any], *, now: datetime
    ) -> dict[str, Any]:
        """构建经 WebSocket 下发给 Gateway 的 mapping 对象（不含 id，由 Gateway 自增）。"""
        return {
            "jiuwenclaw_id": row["jiuwenclaw_id"],
            "policy_id": row["policy_id"],
            "policy_name": row.get("policy_name"),
            "policy_desc": row.get("policy_desc"),
            "user_id": row.get("user_id"),
            "group_id": row.get("group_id"),
            "priority": row["priority"],
            "template_id": row["template_id"],
            "template_type": row["template_type"],
            "enabled": row.get("enabled", True),
            "data": row.get("data"),
            "created_at": iso_datetime(row.get("created_at") or now),
            "updated_at": iso_datetime(row.get("updated_at") or now),
        }

    async def create(
        self,
        jiuwenclaw_id: str,
        body: ConfigDefaultTemplateMappingCreateBody,
    ) -> ConfigDefaultTemplateMappingOut:
        normalized = await validate_jiuwenclaw_id(self._handler, jiuwenclaw_id)
        user_id, group_id = _validate_dimension_keys(body.user_id, body.group_id)

        now = utc_now()
        row = {
            "jiuwenclaw_id": normalized,
            "policy_id": new_uuid4(),
            "policy_name": body.policy_name,
            "policy_desc": body.policy_desc,
            "user_id": user_id,
            "group_id": group_id,
            "priority": body.priority,
            "template_id": body.template_id,
            "template_type": body.template_type,
            "enabled": body.enabled,
            "data": body.data,
            "created_at": now,
            "updated_at": now,
        }
        ack = await push_config_default_template_mapping_op(
            normalized,
            "create",
            mapping=self._mapping_dict_for_push(row, now=now),
        )
        ack_result = ack.get("result") if isinstance(ack, dict) else None
        mapping_id: int | None = None
        if isinstance(ack_result, dict):
            raw_id = ack_result.get("id")
            if raw_id is not None:
                mapping_id = int(raw_id)
        if mapping_id is None or mapping_id < 1:
            raise ValueError(
                "gateway config_default_template_mappings.create returned no id"
            )

        payload = {**row, "id": mapping_id}
        created = await self._handler.create(_TEMPLATE_MAPPING_TABLE, payload)
        await sync_gateway_templates_after_mapping_change(
            self._handler,
            normalized,
            old_template_type=None,
            old_template_id=None,
            new_template_type=body.template_type,
            new_template_id=body.template_id,
        )
        return _row_to_out(created)

    async def get(
        self, jiuwenclaw_id: str, mapping_id: int
    ) -> ConfigDefaultTemplateMappingOut | None:
        normalized = await validate_jiuwenclaw_id(self._handler, jiuwenclaw_id)
        row = await self._handler.get(
            _TEMPLATE_MAPPING_TABLE, _mapping_pk(normalized, mapping_id)
        )
        if row is None:
            return None
        return _row_to_out(row)

    async def list_mappings(
        self,
        jiuwenclaw_id: str,
        query: ConfigDefaultTemplateMappingListQuery,
    ) -> dict[str, Any]:
        normalized = await validate_jiuwenclaw_id(self._handler, jiuwenclaw_id)
        template_type = query.template_type

        page = max(query.page, 1)
        page_size = min(max(query.page_size, 1), 200)
        filters: dict[str, Any] = {"jiuwenclaw_id": normalized}
        if query.user_id:
            filters["user_id"] = strip_optional(query.user_id)
        if query.group_id:
            filters["group_id"] = strip_optional(query.group_id)
        if template_type:
            filters["template_type"] = template_type
        if query.template_id:
            filters["template_id"] = query.template_id.strip()
        if query.enabled is not None:
            filters["enabled"] = query.enabled

        order_by = resolve_order_by(
            query.sort_by,
            query.sort_order,
            allowed_sort_fields=_ALLOWED_SORT_FIELDS,
            default_order_by=DEFAULT_POLICY_ORDER_BY,
        )
        search_query = (query.search or "").strip()
        if search_query:
            rows = await self._handler.list_records(
                _TEMPLATE_MAPPING_TABLE,
                filters,
                limit=_LIST_ALL_CAP,
                offset=0,
                order_by=order_by,
            )
            items = [
                _row_to_out(row).model_dump(mode="json")
                for row in rows
                if _matches_search(row, search_query)
            ]
            total = len(items)
            offset = (page - 1) * page_size
            page_items = items[offset:offset + page_size]
            return {
                "items": page_items,
                "total": total,
                "page": page,
                "page_size": page_size,
            }

        offset = (page - 1) * page_size
        rows = await self._handler.list_records(
            _TEMPLATE_MAPPING_TABLE,
            filters,
            limit=page_size,
            offset=offset,
            order_by=order_by,
        )
        total = await self._handler.count_records(_TEMPLATE_MAPPING_TABLE, filters)
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
        mapping_id: int,
        body: ConfigDefaultTemplateMappingUpdateBody,
    ) -> ConfigDefaultTemplateMappingOut | None:
        normalized = await validate_jiuwenclaw_id(self._handler, jiuwenclaw_id)

        updates = body.model_dump(exclude_unset=True)
        if "user_id" in updates:
            updates["user_id"] = strip_optional(updates["user_id"])
        if "group_id" in updates:
            updates["group_id"] = strip_optional(updates["group_id"])

        row = await self._handler.get(
            _TEMPLATE_MAPPING_TABLE, _mapping_pk(normalized, mapping_id)
        )
        if row is None:
            return None

        merged_user = updates.get("user_id", row.user_id)
        merged_group = updates.get("group_id", row.group_id)
        _validate_dimension_keys(merged_user, merged_group)

        if not updates:
            return _row_to_out(row)

        await push_config_default_template_mapping_op(
            normalized,
            "update",
            row_id=mapping_id,
            updates=updates,
        )
        payload = dict(updates)
        payload["updated_at"] = utc_now()
        updated = await self._handler.update(
            _TEMPLATE_MAPPING_TABLE,
            _mapping_pk(normalized, mapping_id),
            payload,
        )
        if updated is None:
            return None
        if "template_type" in updates or "template_id" in updates:
            await sync_gateway_templates_after_mapping_change(
                self._handler,
                normalized,
                old_template_type=str(getattr(row, "template_type", "") or ""),
                old_template_id=str(getattr(row, "template_id", "") or ""),
                new_template_type=str(
                    updates.get("template_type", getattr(row, "template_type", "") or "")
                ),
                new_template_id=str(
                    updates.get("template_id", getattr(row, "template_id", "") or "")
                ),
            )
        return _row_to_out(updated)

    async def delete(
        self,
        jiuwenclaw_id: str,
        mapping_id: int,
    ) -> bool:
        normalized = await validate_jiuwenclaw_id(self._handler, jiuwenclaw_id)
        row = await self._handler.get(
            _TEMPLATE_MAPPING_TABLE, _mapping_pk(normalized, mapping_id)
        )
        if row is None:
            return False
        await push_config_default_template_mapping_op(
            normalized,
            "delete",
            row_id=mapping_id,
        )
        deleted = await self._handler.delete(
            _TEMPLATE_MAPPING_TABLE, _mapping_pk(normalized, mapping_id)
        )
        if deleted:
            await sync_gateway_templates_after_mapping_change(
                self._handler,
                normalized,
                old_template_type=str(getattr(row, "template_type", "") or ""),
                old_template_id=str(getattr(row, "template_id", "") or ""),
                new_template_type=None,
                new_template_id=None,
            )
        return deleted
