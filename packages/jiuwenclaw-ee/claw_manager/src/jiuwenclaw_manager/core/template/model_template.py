"""模型模板 model_template 业务逻辑。"""

from __future__ import annotations

from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.core.template.push_template_to_gateway import (
    push_template_to_referencing_gateways,
)
from jiuwenclaw_manager.schemas.template_schemas import (
    ModelTemplateCreateBody,
    ModelTemplateListQuery,
    ModelTemplateOut,
    ModelTemplateUpdateBody,
)
from jiuwenclaw_manager.infrastructure.common import resolve_order_by
from jiuwenclaw_manager.infrastructure.utils import iso_datetime, new_uuid4, utc_now
from jiuwenclaw_manager.models.template_models import MODEL_TEMPLATE_TABLE_DEF

_MODEL_TEMPLATE_TABLE = MODEL_TEMPLATE_TABLE_DEF.table_name
_ALLOWED_SORT_FIELDS = frozenset({
    "template_name",
    "description",
    "model_provider",
    "model_id",
    "model_type",
    "api_base",
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
        str(getattr(row, "model_provider", "") or ""),
        str(getattr(row, "model_id", "") or ""),
        str(getattr(row, "api_base", "") or ""),
        *(getattr(row, "model_type", None) or []),
    ]
    return any(needle in field.lower() for field in fields)


def row_to_out(row: Any) -> ModelTemplateOut:
    model_tags = row.model_tags
    if model_tags is not None and not isinstance(model_tags, list):
        model_tags = list(model_tags) if model_tags else None
    return ModelTemplateOut(
        id=row.id,
        template_id=str(row.template_id),
        template_name=row.template_name,
        description=row.description,
        model_type=row.model_type,
        model_tags=model_tags,
        api_base=row.api_base,
        api_key=row.api_key,
        model_id=row.model_id,
        model_provider=row.model_provider,
        parameters=row.parameters,
        timeout=row.timeout,
        retry_count=row.retry_count,
        enable_streaming=row.enable_streaming,
        enable_function_calling=row.enable_function_calling,
        verify_ssl=row.verify_ssl,
        enabled=row.enabled,
        data=row.data,
        created_at=iso_datetime(row.created_at),
        updated_at=iso_datetime(row.updated_at),
    )


class ModelTemplateService:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    def _build_row_for_create(
        self, body: ModelTemplateCreateBody, *, template_id: str
    ) -> dict[str, Any]:
        return {
            "template_id": template_id,
            "template_name": body.template_name,
            "description": body.description,
            "model_type": body.model_type,
            "model_tags": body.model_tags,
            "api_base": body.api_base,
            "api_key": body.api_key,
            "model_id": body.model_id,
            "model_provider": body.model_provider,
            "parameters": body.parameters,
            "timeout": body.timeout,
            "retry_count": body.retry_count,
            "enable_streaming": body.enable_streaming,
            "enable_function_calling": body.enable_function_calling,
            "verify_ssl": body.verify_ssl,
            "enabled": body.enabled,
            "data": body.data,
        }

    async def create(
        self,
        body: ModelTemplateCreateBody,
    ) -> ModelTemplateOut:
        template_uuid = new_uuid4()
        row = self._build_row_for_create(body, template_id=template_uuid)
        now = utc_now()
        payload = dict(row)
        payload.setdefault("created_at", now)
        payload.setdefault("updated_at", now)
        created = await self._handler.create(_MODEL_TEMPLATE_TABLE, payload)
        return row_to_out(created)

    async def get(self, template_id: str) -> ModelTemplateOut | None:
        row = await self._handler.get(_MODEL_TEMPLATE_TABLE, {"template_id": template_id})
        if row is None:
            return None
        return row_to_out(row)

    async def list_templates(self, query: ModelTemplateListQuery) -> dict[str, Any]:
        page = max(query.page, 1)
        page_size = min(max(query.page_size, 1), 200)
        filters: dict[str, Any] = {}
        if query.enabled is not None:
            filters["enabled"] = query.enabled
        provider_query = (query.model_provider or "").strip()
        if provider_query:
            filters["model_provider"] = provider_query
        model_type = query.model_type

        order_by = resolve_order_by(
            query.sort_by, query.sort_order, allowed_sort_fields=_ALLOWED_SORT_FIELDS
        )
        search_query = (query.search or "").strip()
        if search_query or model_type:
            rows = await self._handler.list_records(
                _MODEL_TEMPLATE_TABLE,
                filters,
                limit=10_000,
                offset=0,
                order_by=order_by,
            )
            items = []
            for row in rows:
                if model_type and model_type not in (getattr(row, "model_type", None) or []):
                    continue
                if search_query and not _matches_search(row, search_query):
                    continue
                items.append(row_to_out(row).model_dump(mode="json"))
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
            _MODEL_TEMPLATE_TABLE,
            filters,
            limit=page_size,
            offset=offset,
            order_by=order_by,
        )
        total = await self._handler.count_records(_MODEL_TEMPLATE_TABLE, filters)
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
        body: ModelTemplateUpdateBody,
    ) -> ModelTemplateOut | None:
        updates = body.model_dump(exclude_unset=True)

        if not updates:
            row = await self._handler.get(
                _MODEL_TEMPLATE_TABLE, {"template_id": template_id}
            )
            return row_to_out(row) if row is not None else None

        existing = await self._handler.get(
            _MODEL_TEMPLATE_TABLE, {"template_id": template_id}
        )
        if existing is None:
            return None

        await push_template_to_referencing_gateways(
            self._handler,
            "model_templates",
            "update",
            template_id=template_id,
            updates=updates,
        )
        payload = dict(updates)
        payload["updated_at"] = utc_now()
        row = await self._handler.update(
            _MODEL_TEMPLATE_TABLE, {"template_id": template_id}, payload
        )
        if row is None:
            return None
        return row_to_out(row)

    async def delete(self, template_id: str) -> bool:
        row = await self._handler.get(
            _MODEL_TEMPLATE_TABLE, {"template_id": template_id}
        )
        if row is None:
            return False
        await push_template_to_referencing_gateways(
            self._handler,
            "model_templates",
            "delete",
            template_id=template_id,
        )
        return await self._handler.delete(
            _MODEL_TEMPLATE_TABLE, {"template_id": template_id}
        )
