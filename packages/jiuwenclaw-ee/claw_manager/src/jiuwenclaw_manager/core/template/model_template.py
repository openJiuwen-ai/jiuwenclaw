"""模型模板 model_template 业务逻辑。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.schemas.template_schemas import (
    ModelTemplateCreateBody,
    ModelTemplateOut,
    ModelTemplateUpdateBody,
)
from jiuwenclaw_manager.infrastructure.utils import utc_now
from jiuwenclaw_manager.manager_ws_server import ManagerWsServer
from jiuwenclaw_manager.manager_ws_server.server import push_to_instance
from jiuwenclaw_manager.models.template_models import MODEL_TEMPLATE_TABLE_DEF
from jiuwenclaw_manager.core.instance.instance_service import get_instance_row

_ALLOWED_MODEL_TYPES = frozenset({"default", "video", "audio", "vision"})
_MODEL_TEMPLATE_TABLE = MODEL_TEMPLATE_TABLE_DEF.table_name
_LIST_ALL_CAP = 10_000


async def push_model_template_op(
    jiuwenclaw_id: str,
    op: str,
    *,
    template: dict[str, Any] | None = None,
    template_id: int | None = None,
    updates: dict[str, Any] | None = None,
    server: ManagerWsServer | None = None,
) -> dict[str, Any]:
    """推送模型模板变更（``config.model_templates``），返回 config.ack payload。"""
    payload: dict[str, Any] = {
        "op": op,
        "jiuwenclaw_id": jiuwenclaw_id,
    }
    if template is not None:
        payload["template"] = template
    if template_id is not None:
        payload["template_id"] = template_id
    if updates is not None:
        payload["updates"] = updates
    return await push_to_instance(
        jiuwenclaw_id,
        config={"model_templates": payload},
        server=server,
    )


def _template_pk(jiuwenclaw_id: str, template_id: int) -> dict[str, Any]:
    return {"jiuwenclaw_id": jiuwenclaw_id, "id": template_id}


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


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
        jiuwenclaw_id=row.jiuwenclaw_id,
        display_name=row.display_name,
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
        created_at=_iso(row.created_at),
        updated_at=_iso(row.updated_at),
    )


class ModelTemplateService:
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

    async def _db_update_template(
        self, jiuwenclaw_id: str, template_id: int, updates: dict[str, Any]
    ) -> Any | None:
        if not updates:
            return await self._handler.get(
                _MODEL_TEMPLATE_TABLE, _template_pk(jiuwenclaw_id, template_id)
            )
        payload = dict(updates)
        payload["updated_at"] = utc_now()
        return await self._handler.update(
            _MODEL_TEMPLATE_TABLE, _template_pk(jiuwenclaw_id, template_id), payload
        )

    async def _db_delete_template(self, jiuwenclaw_id: str, template_id: int) -> bool:
        return await self._handler.delete(
            _MODEL_TEMPLATE_TABLE, _template_pk(jiuwenclaw_id, template_id)
        )

    def _template_dict_for_push(self, row: dict[str, Any], *, now: datetime) -> dict[str, Any]:
        """构建经 WebSocket 下发给 Gateway 的 template 对象（不含 id，由 Gateway 自增）。"""
        return {
            "jiuwenclaw_id": row["jiuwenclaw_id"],
            "display_name": row["display_name"],
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
            "created_at": _iso(row.get("created_at") or now),
            "updated_at": _iso(row.get("updated_at") or now),
        }

    def _build_row_for_create(
        self, normalized: str, body: ModelTemplateCreateBody
    ) -> dict[str, Any]:
        model_type = _validate_model_type(body.model_type)
        return {
            "jiuwenclaw_id": normalized,
            "display_name": body.display_name.strip(),
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
        jiuwenclaw_id: str,
        body: ModelTemplateCreateBody,
        *,
        ws_server: ManagerWsServer | None = None,
    ) -> ModelTemplateOut:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)
        _validate_model_type(body.model_type)
        row = self._build_row_for_create(normalized, body)
        now = utc_now()
        payload = dict(row)
        payload.setdefault("created_at", now)
        payload.setdefault("updated_at", now)
        await push_model_template_op(
            normalized,
            "create",
            template=self._template_dict_for_push(payload, now=now),
            server=ws_server,
        )
        created = await self._handler.create(_MODEL_TEMPLATE_TABLE, payload)
        return _row_to_out(created)

    async def get(self, jiuwenclaw_id: str, template_id: int) -> ModelTemplateOut | None:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)
        row = await self._handler.get(
            _MODEL_TEMPLATE_TABLE, _template_pk(normalized, template_id)
        )
        if row is None:
            return None
        return _row_to_out(row)

    async def list_templates(
        self,
        jiuwenclaw_id: str,
        *,
        page: int,
        page_size: int,
        enabled: bool | None,
        model_type: str | None,
    ) -> dict[str, Any]:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)
        page = max(page, 1)
        page_size = min(max(page_size, 1), 200)
        filters: dict[str, Any] = {"jiuwenclaw_id": normalized}
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
        jiuwenclaw_id: str,
        template_id: int,
        body: ModelTemplateUpdateBody,
        *,
        ws_server: ManagerWsServer | None = None,
    ) -> ModelTemplateOut | None:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)

        updates = body.model_dump(exclude_unset=True)
        if "model_type" in updates and updates["model_type"] is not None:
            updates["model_type"] = _validate_model_type(updates["model_type"])
        if "display_name" in updates and updates["display_name"] is not None:
            updates["display_name"] = updates["display_name"].strip()
        if "api_base" in updates and updates["api_base"] is not None:
            updates["api_base"] = updates["api_base"].strip()
        if "model_id" in updates and updates["model_id"] is not None:
            updates["model_id"] = updates["model_id"].strip()
        if "model_provider" in updates and updates["model_provider"] is not None:
            updates["model_provider"] = updates["model_provider"].strip()

        if not updates:
            row = await self._handler.get(
                _MODEL_TEMPLATE_TABLE, _template_pk(normalized, template_id)
            )
            return _row_to_out(row) if row is not None else None

        existing = await self._handler.get(
            _MODEL_TEMPLATE_TABLE, _template_pk(normalized, template_id)
        )
        if existing is None:
            return None

        await push_model_template_op(
            normalized,
            "update",
            template_id=template_id,
            updates=updates,
            server=ws_server,
        )
        row = await self._db_update_template(normalized, template_id, updates)
        if row is None:
            return None
        return _row_to_out(row)

    async def delete(
        self,
        jiuwenclaw_id: str,
        template_id: int,
        *,
        ws_server: ManagerWsServer | None = None,
    ) -> bool:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)
        row = await self._handler.get(
            _MODEL_TEMPLATE_TABLE, _template_pk(normalized, template_id)
        )
        if row is None:
            return False
        await push_model_template_op(
            normalized,
            "delete",
            template_id=template_id,
            server=ws_server,
        )
        return await self._db_delete_template(normalized, template_id)
