"""模型模板 model_template 业务逻辑。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.schemas.template_schemas import (
    ModelTemplateCreateBody,
    ModelTemplateOut,
    ModelTemplateUpdateBody,
)
from jiuwenclaw_manager.infrastructure.utils import iso_datetime, new_uuid4, utc_now
from jiuwenclaw_manager.manager_ws_server.server import push_config_op_to_all
from jiuwenclaw_manager.models.template_models import MODEL_TEMPLATE_TABLE_DEF

_ALLOWED_MODEL_TYPES = frozenset({"default", "video", "audio", "vision"})
_MODEL_TEMPLATE_TABLE = MODEL_TEMPLATE_TABLE_DEF.table_name
_MODEL_TEMPLATES_CONFIG_SECTION = "model_templates"
_LIST_ALL_CAP = 10_000


async def push_model_templates_to_all_gateways(
    op: str,
    *,
    template: dict[str, Any] | None = None,
    template_id: str | None = None,
    updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """向所有已注册 Gateway 推送模型模板变更。"""
    payload: dict[str, Any] = {"op": op}
    if template is not None:
        payload["template"] = template
    if template_id is not None:
        payload["template_id"] = template_id
    if updates is not None:
        payload["updates"] = updates
    return await push_config_op_to_all({_MODEL_TEMPLATES_CONFIG_SECTION: payload})


def _template_pk(template_id: str) -> dict[str, Any]:
    return {"template_id": template_id.strip()}


def _normalize_template_id(template_id: str) -> str:
    normalized = template_id.strip()
    if not normalized:
        raise ValueError("template_id is required")
    if len(normalized) > 100:
        raise ValueError("template_id must be at most 100 characters")
    return normalized


def _validate_model_type(value: str | list[str]) -> str | list[str]:
    if isinstance(value, str):
        if value not in _ALLOWED_MODEL_TYPES:
            raise ValueError(
                f"model_type must be one of {sorted(_ALLOWED_MODEL_TYPES)}, got {value!r}"
            )
        return value
    if isinstance(value, list):
        if not value:
            raise ValueError("model_type list cannot be empty")
        for item in value:
            if item not in _ALLOWED_MODEL_TYPES:
                raise ValueError(
                    f"model_type entries must be in {sorted(_ALLOWED_MODEL_TYPES)}, got {item!r}"
                )
        return value
    raise ValueError("model_type must be a string or a list of strings")


def _matches_model_type(row_model_type: Any, filter_type: str) -> bool:
    if isinstance(row_model_type, str):
        return row_model_type == filter_type
    if isinstance(row_model_type, list):
        return filter_type in row_model_type
    return str(row_model_type) == filter_type


def _row_to_out(row: Any) -> ModelTemplateOut:
    model_type = row.model_type
    if not isinstance(model_type, (str, list)):
        model_type = str(model_type)
    model_tags = row.model_tags
    if model_tags is not None and not isinstance(model_tags, list):
        model_tags = list(model_tags) if model_tags else None
    return ModelTemplateOut(
        id=row.id,
        template_id=str(row.template_id),
        template_name=row.template_name,
        description=row.description,
        model_type=model_type,
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

    async def _db_update_template(
        self, template_id: str, updates: dict[str, Any]
    ) -> Any | None:
        if not updates:
            return await self._handler.get(
                _MODEL_TEMPLATE_TABLE, _template_pk(template_id)
            )
        payload = dict(updates)
        payload["updated_at"] = utc_now()
        return await self._handler.update(
            _MODEL_TEMPLATE_TABLE, _template_pk(template_id), payload
        )

    async def _db_delete_template(self, template_id: str) -> bool:
        return await self._handler.delete(
            _MODEL_TEMPLATE_TABLE, _template_pk(template_id)
        )

    def _template_dict_for_push(self, row: dict[str, Any], *, now: datetime) -> dict[str, Any]:
        """构建经 WebSocket 下发给 Gateway 的 template 对象（含 template_id UUID）。"""
        return {
            "template_id": row["template_id"],
            "template_name": row["template_name"],
            "description": row.get("description"),
            "model_type": row["model_type"],
            "model_tags": row.get("model_tags"),
            "api_base": row["api_base"],
            "api_key": row["api_key"],
            "model_id": row["model_id"],
            "model_provider": row["model_provider"],
            "parameters": row.get("parameters"),
            "timeout": row.get("timeout"),
            "retry_count": row.get("retry_count"),
            "enable_streaming": row.get("enable_streaming"),
            "enable_function_calling": row.get("enable_function_calling"),
            "verify_ssl": row.get("verify_ssl"),
            "enabled": row.get("enabled"),
            "data": row.get("data"),
            "created_at": iso_datetime(row.get("created_at") or now),
            "updated_at": iso_datetime(row.get("updated_at") or now),
        }

    def _build_row_for_create(
        self, body: ModelTemplateCreateBody, *, template_id: str
    ) -> dict[str, Any]:
        model_type = _validate_model_type(body.model_type)
        return {
            "template_id": template_id,
            "template_name": body.template_name.strip(),
            "description": body.description,
            "model_type": model_type,
            "model_tags": body.model_tags,
            "api_base": body.api_base.strip(),
            "api_key": body.api_key,
            "model_id": body.model_id.strip(),
            "model_provider": body.model_provider.strip(),
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
        await push_model_templates_to_all_gateways(
            "create",
            template=self._template_dict_for_push(payload, now=now),
        )
        created = await self._handler.create(_MODEL_TEMPLATE_TABLE, payload)
        return _row_to_out(created)

    async def get(self, template_id: str) -> ModelTemplateOut | None:
        tid = _normalize_template_id(template_id)
        row = await self._handler.get(_MODEL_TEMPLATE_TABLE, _template_pk(tid))
        if row is None:
            return None
        return _row_to_out(row)

    async def list_templates(
        self,
        *,
        page: int,
        page_size: int,
        enabled: bool | None,
        model_type: str | None,
    ) -> dict[str, Any]:
        page = max(page, 1)
        page_size = min(max(page_size, 1), 200)
        filters: dict[str, Any] = {}
        if enabled is not None:
            filters["enabled"] = enabled

        if model_type:
            rows = await self._handler.list_records(
                _MODEL_TEMPLATE_TABLE, filters, limit=_LIST_ALL_CAP, offset=0
            )
            items = [
                _row_to_out(r).model_dump(mode="json")
                for r in rows
                if _matches_model_type(getattr(r, "model_type", None), model_type)
            ]
            total = len(items)
            offset = (page - 1) * page_size
            page_items = items[offset : offset + page_size]
            return {
                "items": page_items,
                "total": total,
                "page": page,
                "page_size": page_size,
            }

        offset = (page - 1) * page_size
        rows = await self._handler.list_records(
            _MODEL_TEMPLATE_TABLE, filters, limit=page_size, offset=offset
        )
        total = await self._handler.count_records(_MODEL_TEMPLATE_TABLE, filters)
        items = [_row_to_out(r).model_dump(mode="json") for r in rows]
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
        tid = _normalize_template_id(template_id)
        updates = body.model_dump(exclude_unset=True)
        if "model_type" in updates and updates["model_type"] is not None:
            updates["model_type"] = _validate_model_type(updates["model_type"])
        if "template_name" in updates and updates["template_name"] is not None:
            updates["template_name"] = updates["template_name"].strip()
        if "api_base" in updates and updates["api_base"] is not None:
            updates["api_base"] = updates["api_base"].strip()
        if "model_id" in updates and updates["model_id"] is not None:
            updates["model_id"] = updates["model_id"].strip()
        if "model_provider" in updates and updates["model_provider"] is not None:
            updates["model_provider"] = updates["model_provider"].strip()

        if not updates:
            row = await self._handler.get(_MODEL_TEMPLATE_TABLE, _template_pk(tid))
            return _row_to_out(row) if row is not None else None

        existing = await self._handler.get(_MODEL_TEMPLATE_TABLE, _template_pk(tid))
        if existing is None:
            return None

        await push_model_templates_to_all_gateways(
            "update",
            template_id=tid,
            updates=updates,
        )
        row = await self._db_update_template(tid, updates)
        if row is None:
            return None
        return _row_to_out(row)

    async def delete(self, template_id: str) -> bool:
        tid = _normalize_template_id(template_id)
        row = await self._handler.get(_MODEL_TEMPLATE_TABLE, _template_pk(tid))
        if row is None:
            return False
        await push_model_templates_to_all_gateways(
            "delete",
            template_id=tid,
        )
        return await self._db_delete_template(tid)
