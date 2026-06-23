"""扩展配置模板 extension_config_template 业务逻辑。"""

from __future__ import annotations

from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.core.template.push_template_to_gateway import (
    assert_template_deletable,
    push_template_to_referencing_gateways,
)
from jiuwenclaw_manager.infrastructure.common import resolve_order_by
from jiuwenclaw_manager.infrastructure.utils import iso_datetime, new_uuid4, utc_now
from jiuwenclaw_manager.models.template_models import EXTENSION_CONFIG_TEMPLATE_TABLE_DEF
from jiuwenclaw_manager.schemas.template_schemas import (
    ExtensionConfigTemplateCreateBody,
    ExtensionConfigTemplateListQuery,
    ExtensionConfigTemplateOut,
    ExtensionConfigTemplateUpdateBody,
)

_TABLE = EXTENSION_CONFIG_TEMPLATE_TABLE_DEF.table_name
_LIST_ALL_CAP = 10_000
_ALLOWED_SORT_FIELDS = frozenset({
    "template_name",
    "description",
    "component",
    "hook_type",
    "updated_at",
})


def _matches_search(row: Any, query: str) -> bool:
    needle = query.strip().lower()
    if not needle:
        return True
    fields = [
        str(getattr(row, "template_id", "") or ""),
        str(getattr(row, "template_name", "") or ""),
        str(getattr(row, "description", "") or ""),
        str(getattr(row, "component", "") or ""),
        str(getattr(row, "hook_type", "") or ""),
    ]
    return any(needle in field.lower() for field in fields)


def _validate_hook_config(hook_config: dict[str, Any], *, hook_type: str) -> dict[str, Any]:
    handler = str(hook_config.get("handler") or "").strip()
    if not handler:
        raise ValueError("hook_config.handler is required")
    if hook_type == "schedule":
        schedule = str(hook_config.get("schedule") or "").strip()
        if not schedule:
            raise ValueError("hook_config.schedule is required when hook_type=schedule")
    return hook_config


def row_to_out(row: Any) -> ExtensionConfigTemplateOut:
    hook_config = row.hook_config
    if not isinstance(hook_config, dict):
        hook_config = dict(hook_config) if hook_config else {}
    custom_config = row.custom_config
    if custom_config is not None and not isinstance(custom_config, dict):
        custom_config = dict(custom_config)
    return ExtensionConfigTemplateOut(
        id=row.id,
        template_id=str(row.template_id),
        template_name=row.template_name,
        description=row.description,
        component=row.component,
        hook_type=row.hook_type,
        hook_config=hook_config,
        custom_config=custom_config,
        enabled=row.enabled,
        data=row.data,
        created_at=iso_datetime(row.created_at),
        updated_at=iso_datetime(row.updated_at),
    )


class ExtensionConfigTemplateService:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    @staticmethod
    def _build_row_for_create(
        body: ExtensionConfigTemplateCreateBody, *, template_id: str
    ) -> dict[str, Any]:
        hook_config = _validate_hook_config(body.hook_config, hook_type=body.hook_type)
        custom_config = body.custom_config if body.custom_config is not None else {}
        return {
            "template_id": template_id,
            "template_name": body.template_name,
            "description": body.description,
            "component": body.component,
            "hook_type": body.hook_type,
            "hook_config": hook_config,
            "custom_config": custom_config,
            "enabled": body.enabled,
            "data": body.data,
        }

    async def create(
        self,
        body: ExtensionConfigTemplateCreateBody,
    ) -> ExtensionConfigTemplateOut:
        template_uuid = new_uuid4()
        row = self._build_row_for_create(body, template_id=template_uuid)
        now = utc_now()
        payload = dict(row)
        payload.setdefault("created_at", now)
        payload.setdefault("updated_at", now)
        created = await self._handler.create(_TABLE, payload)
        return row_to_out(created)

    async def get(self, template_id: str) -> ExtensionConfigTemplateOut | None:
        row = await self._handler.get(_TABLE, {"template_id": template_id})
        if row is None:
            return None
        return row_to_out(row)

    async def list_templates(
        self,
        query: ExtensionConfigTemplateListQuery,
    ) -> dict[str, Any]:
        page = max(query.page, 1)
        page_size = min(max(query.page_size, 1), 200)
        filters: dict[str, Any] = {}
        if query.enabled is not None:
            filters["enabled"] = query.enabled
        if query.component is not None:
            filters["component"] = query.component
        if query.hook_type is not None:
            filters["hook_type"] = query.hook_type

        order_by = resolve_order_by(
            query.sort_by, query.sort_order, allowed_sort_fields=_ALLOWED_SORT_FIELDS
        )
        search_query = (query.search or "").strip()
        if search_query:
            rows = await self._handler.list_records(
                _TABLE,
                filters,
                limit=_LIST_ALL_CAP,
                offset=0,
                order_by=order_by,
            )
            items = [
                row_to_out(r).model_dump(mode="json")
                for r in rows
                if _matches_search(r, search_query)
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
            _TABLE,
            filters,
            limit=page_size,
            offset=offset,
            order_by=order_by,
        )
        total = await self._handler.count_records(_TABLE, filters)
        items = [row_to_out(r).model_dump(mode="json") for r in rows]
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def update(
        self,
        template_id: str,
        body: ExtensionConfigTemplateUpdateBody,
    ) -> ExtensionConfigTemplateOut | None:
        updates = body.model_dump(exclude_unset=True)

        if not updates:
            row = await self._handler.get(_TABLE, {"template_id": template_id})
            return row_to_out(row) if row is not None else None

        existing = await self._handler.get(_TABLE, {"template_id": template_id})
        if existing is None:
            return None

        hook_type = updates.get("hook_type", getattr(existing, "hook_type", ""))
        if "hook_config" in updates and updates["hook_config"] is not None:
            updates["hook_config"] = _validate_hook_config(
                updates["hook_config"], hook_type=hook_type
            )

        await push_template_to_referencing_gateways(
            self._handler,
            "extension_config_templates",
            "update",
            template_id=template_id,
            updates=updates,
        )
        payload = dict(updates)
        payload["updated_at"] = utc_now()
        row = await self._handler.update(
            _TABLE, {"template_id": template_id}, payload
        )
        if row is None:
            return None
        return row_to_out(row)

    async def delete(self, template_id: str) -> bool:
        row = await self._handler.get(_TABLE, {"template_id": template_id})
        if row is None:
            return False
        await assert_template_deletable(
            self._handler, template_id, "extension_config_templates"
        )
        await push_template_to_referencing_gateways(
            self._handler,
            "extension_config_templates",
            "delete",
            template_id=template_id,
        )
        return await self._handler.delete(_TABLE, {"template_id": template_id})
