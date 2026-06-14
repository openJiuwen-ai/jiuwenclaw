"""扩展配置模板 extension_config_template 业务逻辑。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.infrastructure.utils import iso_datetime, new_uuid4, utc_now
from jiuwenclaw_manager.manager_ws_server.server import push_config_op, push_config_op_to_all
from jiuwenclaw_manager.models.template_models import EXTENSION_CONFIG_TEMPLATE_TABLE_DEF
from jiuwenclaw_manager.schemas.template_schemas import (
    ExtensionConfigTemplateCreateBody,
    ExtensionConfigTemplateOut,
    ExtensionConfigTemplateUpdateBody,
)

_ALLOWED_COMPONENTS = frozenset({"gateway", "agent_server"})
_ALLOWED_HOOK_TYPES = frozenset({"pre_request", "post_request", "error", "schedule"})
_TABLE = EXTENSION_CONFIG_TEMPLATE_TABLE_DEF.table_name
_CONFIG_SECTION = "extension_config_templates"
_LIST_ALL_CAP = 10_000
# order_by 元组第二项为 is_desc（True=降序），与 SQLAlchemyHandler.list_records 一致。
_DEFAULT_ORDER_BY: list[tuple[str, bool]] = [("updated_at", True)]
_ALLOWED_SORT_FIELDS = frozenset({
    "template_name",
    "description",
    "component",
    "hook_type",
    "updated_at",
})


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
    return [(field, is_desc), ("template_id", is_desc)]


async def push_extension_config_templates_to_all_gateways(
    op: str,
    *,
    template: dict[str, Any] | None = None,
    template_id: str | None = None,
    updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """向所有已注册 Gateway 推送扩展配置模板变更。"""
    payload: dict[str, Any] = {"op": op}
    if template is not None:
        payload["template"] = template
    if template_id is not None:
        payload["template_id"] = template_id
    if updates is not None:
        payload["updates"] = updates
    return await push_config_op_to_all({_CONFIG_SECTION: payload})


async def push_extension_config_templates_op(
    jiuwenclaw_id: str,
    op: str,
    *,
    template: dict[str, Any] | None = None,
    template_id: str | None = None,
    updates: dict[str, Any] | None = None,
    templates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"op": op}
    if template is not None:
        payload["template"] = template
    if template_id is not None:
        payload["template_id"] = template_id
    if updates is not None:
        payload["updates"] = updates
    if templates is not None:
        payload["templates"] = templates
    return await push_config_op(jiuwenclaw_id, {_CONFIG_SECTION: payload})


async def push_extension_config_templates_sync_to_gateway(
    handler: DBHandler,
    jiuwenclaw_id: str,
) -> dict[str, Any]:
    jid = str(jiuwenclaw_id or "").strip()
    if not jid:
        raise ValueError("jiuwenclaw_id is required")
    rows = await handler.list_records(_TABLE, {}, limit=_LIST_ALL_CAP, offset=0)
    templates = [_row_to_sync_payload(row) for row in rows]
    return await push_extension_config_templates_op(jid, "sync", templates=templates)


def _template_pk(template_id: str) -> dict[str, Any]:
    return {"template_id": template_id.strip()}


def _normalize_template_id(template_id: str) -> str:
    normalized = template_id.strip()
    if not normalized:
        raise ValueError("template_id is required")
    if len(normalized) > 100:
        raise ValueError("template_id must be at most 100 characters")
    return normalized


def _validate_component(value: str) -> str:
    normalized = value.strip()
    if normalized not in _ALLOWED_COMPONENTS:
        raise ValueError(
            f"component must be one of {sorted(_ALLOWED_COMPONENTS)}, got {value!r}"
        )
    return normalized


def _validate_hook_type(value: str) -> str:
    normalized = value.strip()
    if normalized not in _ALLOWED_HOOK_TYPES:
        raise ValueError(
            f"hook_type must be one of {sorted(_ALLOWED_HOOK_TYPES)}, got {value!r}"
        )
    return normalized


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
    if not isinstance(hook_config, dict):
        raise ValueError("hook_config must be an object")
    handler = str(hook_config.get("handler") or "").strip()
    if not handler:
        raise ValueError("hook_config.handler is required")
    if hook_type == "schedule":
        schedule = str(hook_config.get("schedule") or "").strip()
        if not schedule:
            raise ValueError("hook_config.schedule is required when hook_type=schedule")
    return hook_config


def _row_to_out(row: Any) -> ExtensionConfigTemplateOut:
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


def _row_to_sync_payload(row: Any) -> dict[str, Any]:
    out = _row_to_out(row)
    data = out.model_dump(mode="json")
    for key in ("id", "created_at", "updated_at"):
        data.pop(key, None)
    return data


class ExtensionConfigTemplateService:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def _db_update_template(
        self, template_id: str, updates: dict[str, Any]
    ) -> Any | None:
        if not updates:
            return await self._handler.get(_TABLE, _template_pk(template_id))
        payload = dict(updates)
        payload["updated_at"] = utc_now()
        return await self._handler.update(
            _TABLE, _template_pk(template_id), payload
        )

    async def _db_delete_template(self, template_id: str) -> bool:
        return await self._handler.delete(_TABLE, _template_pk(template_id))

    @staticmethod
    def _template_dict_for_push(row: dict[str, Any], *, now: datetime) -> dict[str, Any]:
        return {
            "template_id": row["template_id"],
            "template_name": row["template_name"],
            "description": row.get("description"),
            "component": row["component"],
            "hook_type": row["hook_type"],
            "hook_config": row["hook_config"],
            "custom_config": row.get("custom_config"),
            "enabled": row.get("enabled"),
            "data": row.get("data"),
            "created_at": iso_datetime(row.get("created_at") or now),
            "updated_at": iso_datetime(row.get("updated_at") or now),
        }

    @staticmethod
    def _build_row_for_create(
        body: ExtensionConfigTemplateCreateBody, *, template_id: str
    ) -> dict[str, Any]:
        component = _validate_component(body.component)
        hook_type = _validate_hook_type(body.hook_type)
        hook_config = _validate_hook_config(body.hook_config, hook_type=hook_type)
        custom_config = body.custom_config if body.custom_config is not None else {}
        return {
            "template_id": template_id,
            "template_name": body.template_name.strip(),
            "description": body.description,
            "component": component,
            "hook_type": hook_type,
            "hook_config": hook_config,
            "custom_config": custom_config,
            "enabled": body.enabled,
            "data": body.data,
        }

    @staticmethod
    def _normalize_updates(updates: dict[str, Any], existing: Any) -> dict[str, Any]:
        hook_type = _validate_hook_type(
            updates.get("hook_type", getattr(existing, "hook_type", ""))
        )
        if "hook_type" in updates and updates["hook_type"] is not None:
            updates["hook_type"] = hook_type
        if "component" in updates and updates["component"] is not None:
            updates["component"] = _validate_component(updates["component"])
        if "template_name" in updates and updates["template_name"] is not None:
            updates["template_name"] = updates["template_name"].strip()
        if "hook_config" in updates and updates["hook_config"] is not None:
            updates["hook_config"] = _validate_hook_config(
                updates["hook_config"], hook_type=hook_type
            )
        return updates

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
        await push_extension_config_templates_to_all_gateways(
            "create",
            template=self._template_dict_for_push(payload, now=now),
        )
        created = await self._handler.create(_TABLE, payload)
        return _row_to_out(created)

    async def get(self, template_id: str) -> ExtensionConfigTemplateOut | None:
        tid = _normalize_template_id(template_id)
        row = await self._handler.get(_TABLE, _template_pk(tid))
        if row is None:
            return None
        return _row_to_out(row)

    async def list_templates(
        self,
        *,
        page: int,
        page_size: int,
        enabled: bool | None,
        component: str | None,
        hook_type: str | None,
        search: str | None = None,
        sort_by: str | None = None,
        sort_order: str | None = None,
    ) -> dict[str, Any]:
        page = max(page, 1)
        page_size = min(max(page_size, 1), 200)
        filters: dict[str, Any] = {}
        if enabled is not None:
            filters["enabled"] = enabled
        if component is not None:
            filters["component"] = _validate_component(component)
        if hook_type is not None:
            filters["hook_type"] = _validate_hook_type(hook_type)

        order_by = _resolve_order_by(sort_by, sort_order)
        search_query = (search or "").strip()
        if search_query:
            rows = await self._handler.list_records(
                _TABLE,
                filters,
                limit=_LIST_ALL_CAP,
                offset=0,
                order_by=order_by,
            )
            items = [
                _row_to_out(r).model_dump(mode="json")
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
        body: ExtensionConfigTemplateUpdateBody,
    ) -> ExtensionConfigTemplateOut | None:
        tid = _normalize_template_id(template_id)
        updates = body.model_dump(exclude_unset=True)

        if not updates:
            row = await self._handler.get(_TABLE, _template_pk(tid))
            return _row_to_out(row) if row is not None else None

        existing = await self._handler.get(_TABLE, _template_pk(tid))
        if existing is None:
            return None

        updates = self._normalize_updates(updates, existing)

        await push_extension_config_templates_to_all_gateways(
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
        row = await self._handler.get(_TABLE, _template_pk(tid))
        if row is None:
            return False
        await push_extension_config_templates_to_all_gateways(
            "delete",
            template_id=tid,
        )
        return await self._db_delete_template(tid)
