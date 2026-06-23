"""Skill 白名单模板 skill_whitelist_template 业务逻辑。"""

from __future__ import annotations

from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.core.template.push_template_to_gateway import (
    assert_template_deletable,
    push_template_to_referencing_gateways,
)
from jiuwenclaw_manager.infrastructure.common import resolve_order_by
from jiuwenclaw_manager.infrastructure.utils import iso_datetime, new_uuid4, utc_now
from jiuwenclaw_manager.models.template_models import SKILL_WHITELIST_TEMPLATE_TABLE_DEF
from jiuwenclaw_manager.schemas.template_schemas import (
    SkillWhitelistTemplateCreateBody,
    SkillWhitelistTemplateListQuery,
    SkillWhitelistTemplateOut,
    SkillWhitelistTemplateUpdateBody,
)

_TABLE = SKILL_WHITELIST_TEMPLATE_TABLE_DEF.table_name
_LIST_ALL_CAP = 10_000
_ALLOWED_SORT_FIELDS = frozenset({
    "template_name",
    "description",
    "skill_source",
    "skill_id",
    "skill_version",
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
        str(getattr(row, "skill_source", "") or ""),
        str(getattr(row, "skill_id", "") or ""),
        str(getattr(row, "skill_version", "") or ""),
    ]
    return any(needle in field.lower() for field in fields)


def row_to_out(row: Any) -> SkillWhitelistTemplateOut:
    return SkillWhitelistTemplateOut(
        id=row.id,
        template_id=str(row.template_id),
        template_name=row.template_name,
        description=row.description,
        skill_id=row.skill_id,
        skill_version=row.skill_version,
        skill_source=row.skill_source,
        enabled=row.enabled,
        data=row.data,
        created_at=iso_datetime(row.created_at),
        updated_at=iso_datetime(row.updated_at),
    )


class SkillWhitelistTemplateService:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    @staticmethod
    def _build_row_for_create(
        body: SkillWhitelistTemplateCreateBody, *, template_id: str
    ) -> dict[str, Any]:
        return {
            "template_id": template_id,
            "template_name": body.template_name,
            "description": body.description,
            "skill_id": body.skill_id,
            "skill_version": body.skill_version,
            "skill_source": body.skill_source,
            "enabled": body.enabled,
            "data": body.data,
        }

    async def create(
        self,
        body: SkillWhitelistTemplateCreateBody,
    ) -> SkillWhitelistTemplateOut:
        template_uuid = new_uuid4()
        row = self._build_row_for_create(body, template_id=template_uuid)
        now = utc_now()
        payload = dict(row)
        payload.setdefault("created_at", now)
        payload.setdefault("updated_at", now)
        created = await self._handler.create(_TABLE, payload)
        return row_to_out(created)

    async def get(self, template_id: str) -> SkillWhitelistTemplateOut | None:
        row = await self._handler.get(_TABLE, {"template_id": template_id})
        if row is None:
            return None
        return row_to_out(row)

    async def list_templates(
        self,
        query: SkillWhitelistTemplateListQuery,
    ) -> dict[str, Any]:
        page = max(query.page, 1)
        page_size = min(max(query.page_size, 1), 200)
        filters: dict[str, Any] = {}
        if query.enabled is not None:
            filters["enabled"] = query.enabled

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

        if query.skill_id is not None:
            filters["skill_id"] = query.skill_id
        if query.skill_source is not None:
            filters["skill_source"] = query.skill_source

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
        body: SkillWhitelistTemplateUpdateBody,
    ) -> SkillWhitelistTemplateOut | None:
        updates = body.model_dump(exclude_unset=True)

        if not updates:
            row = await self._handler.get(_TABLE, {"template_id": template_id})
            return row_to_out(row) if row is not None else None

        existing = await self._handler.get(_TABLE, {"template_id": template_id})
        if existing is None:
            return None

        await push_template_to_referencing_gateways(
            self._handler,
            "skill_whitelist_templates",
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
            self._handler, template_id, "skill_whitelist_templates"
        )
        await push_template_to_referencing_gateways(
            self._handler,
            "skill_whitelist_templates",
            "delete",
            template_id=template_id,
        )
        return await self._handler.delete(_TABLE, {"template_id": template_id})
