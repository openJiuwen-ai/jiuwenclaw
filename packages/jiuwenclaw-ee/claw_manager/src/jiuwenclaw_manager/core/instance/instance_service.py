"""????????"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.infrastructure.utils import utc_now
from jiuwenclaw_manager.schemas.instance_schemas import (
    CreateInstanceBody,
    InstanceDetail,
    InstanceSummary,
    PatchInstanceDataBody,
    ServiceStatusItem,
    ServiceStatusList,
)
from jiuwenclaw_manager.models.instance_models import (
    INSTANCE_INFO_TABLE_DEF,
    SERVICE_INSTANCE_TABLE_DEF,
)

_INSTANCE_TABLE = INSTANCE_INFO_TABLE_DEF.table_name
_SERVICE_TABLE = SERVICE_INSTANCE_TABLE_DEF.table_name


def dumps_auth_config(cfg: dict) -> str:
    return json.dumps(cfg, ensure_ascii=False, separators=(",", ":"))


async def create_instance_row(handler: DBHandler, row_data: dict[str, Any]) -> Any:
    now = utc_now()
    payload = dict(row_data)
    payload.setdefault("created_at", now)
    payload.setdefault("updated_at", now)
    return await handler.create(_INSTANCE_TABLE, payload)


async def get_instance_row(handler: DBHandler, jiuwenclaw_id: str) -> Any | None:
    return await handler.get(_INSTANCE_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})


async def list_instance_rows(
    handler: DBHandler,
    *,
    status: str | None,
    offset: int,
    limit: int,
) -> tuple[Sequence[Any], int]:
    filters: dict[str, Any] = {}
    if status:
        filters["status"] = status
    total = await handler.count_records(_INSTANCE_TABLE, filters)
    rows = await handler.list_records(_INSTANCE_TABLE, filters, limit=limit, offset=offset)
    return rows, int(total)


async def delete_instance_row(handler: DBHandler, jiuwenclaw_id: str) -> None:
    services = await list_instance_services(handler, jiuwenclaw_id)
    for svc in services:
        sid = getattr(svc, "id", None)
        if sid is not None:
            await handler.delete(_SERVICE_TABLE, {"id": int(sid)})
    await handler.delete(_INSTANCE_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})


async def merge_instance_data(
    handler: DBHandler, jiuwenclaw_id: str, patch: dict
) -> Any | None:
    row = await get_instance_row(handler, jiuwenclaw_id)
    if row is None:
        return None
    merged = dict(getattr(row, "data", None) or {})
    merged.update(patch)
    now = utc_now()
    return await handler.update(
        _INSTANCE_TABLE,
        {"jiuwenclaw_id": jiuwenclaw_id},
        {"data": merged, "updated_at": now},
    )


async def list_instance_services(handler: DBHandler, jiuwenclaw_id: str) -> Sequence[Any]:
    return await handler.list_records(
        _SERVICE_TABLE,
        {"jiuwenclaw_id": jiuwenclaw_id},
        limit=10_000,
        offset=0,
    )


async def upsert_service_heartbeat(
    handler: DBHandler,
    *,
    jiuwenclaw_id: str,
    service_id: str,
    service_type: str,
    component_role: str,
    manager_id: str,
    endpoint: str | None,
    version: str | None,
    capabilities: dict | None,
    extra_data: dict | None,
) -> Any:
    rows = await handler.list_records(
        _SERVICE_TABLE,
        {"jiuwenclaw_id": jiuwenclaw_id, "service_id": service_id},
        limit=1,
        offset=0,
    )
    now = utc_now()
    if not rows:
        return await handler.create(
            _SERVICE_TABLE,
            {
                "jiuwenclaw_id": jiuwenclaw_id,
                "service_id": service_id,
                "service_type": service_type,
                "component_role": component_role,
                "manager_id": manager_id,
                "endpoint": endpoint,
                "version": version,
                "capabilities": capabilities,
                "data": extra_data,
                "status": "online",
                "last_heartbeat": now,
                "created_at": now,
                "updated_at": now,
            },
        )

    existing = rows[0]
    row_id = int(getattr(existing, "id"))
    merged_data = dict(getattr(existing, "data", None) or {})
    if extra_data is not None:
        merged_data.update(extra_data)
    updates: dict[str, Any] = {
        "endpoint": endpoint or getattr(existing, "endpoint", None),
        "version": version or getattr(existing, "version", None),
        "last_heartbeat": now,
        "status": "online",
        "updated_at": now,
    }
    if capabilities is not None:
        updates["capabilities"] = capabilities
    if extra_data is not None:
        updates["data"] = merged_data
    return await handler.update(_SERVICE_TABLE, {"id": row_id}, updates)


async def set_service_status(
    handler: DBHandler,
    *,
    jiuwenclaw_id: str,
    service_id: str,
    status: str,
) -> bool:
    rows = await handler.list_records(
        _SERVICE_TABLE,
        {"jiuwenclaw_id": jiuwenclaw_id, "service_id": service_id},
        limit=1,
        offset=0,
    )
    if not rows:
        return False
    row_id = int(getattr(rows[0], "id"))
    updated = await handler.update(
        _SERVICE_TABLE,
        {"id": row_id},
        {"status": status, "updated_at": utc_now()},
    )
    return updated is not None


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


class InstanceService:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def create(self, body: CreateInstanceBody) -> dict:
        jiuwenclaw_id = f"sp-{uuid.uuid4().hex[:12]}"
        data_dict: dict | None = None
        if body.management_api_base and str(body.management_api_base).strip():
            data_dict = {"management_api_base": str(body.management_api_base).strip().rstrip("/")}
        row_data = {
            "jiuwenclaw_id": jiuwenclaw_id,
            "jiuwenclaw_name": body.jiuwenclaw_name,
            "creator_id": body.creator_id,
            "description": body.description,
            "k8s_master_host": body.k8s_master_host,
            "k8s_auth_type": body.k8s_auth_type,
            "k8s_auth_config": dumps_auth_config(body.k8s_auth_config),
            "k8s_namespace": body.k8s_namespace,
            "status": "active",
            "resource_quota": body.resource_quota,
            "data": data_dict,
            "group_id": body.group_id,
            "space_id": body.space_id,
        }
        row = await create_instance_row(self._handler, row_data)
        return {"jiuwenclaw_id": jiuwenclaw_id, "status": getattr(row, "status", "active")}

    async def list_instances(
        self, *, page: int, page_size: int, status: str | None
    ) -> dict:
        page = max(page, 1)
        page_size = min(max(page_size, 1), 200)
        offset = (page - 1) * page_size
        rows, total = await list_instance_rows(
            self._handler, status=status, offset=offset, limit=page_size
        )
        items = [
            InstanceSummary(
                jiuwenclaw_id=r.jiuwenclaw_id,
                jiuwenclaw_name=r.jiuwenclaw_name,
                status=r.status,
                k8s_namespace=r.k8s_namespace,
                group_id=r.group_id,
                space_id=r.space_id,
                created_at=_iso(r.created_at),
            )
            for r in rows
        ]
        return {
            "items": [i.model_dump() for i in items],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def get(self, jiuwenclaw_id: str) -> InstanceDetail | None:
        row = await get_instance_row(self._handler, jiuwenclaw_id)
        if row is None:
            return None
        return InstanceDetail(
            jiuwenclaw_id=row.jiuwenclaw_id,
            jiuwenclaw_name=row.jiuwenclaw_name,
            status=row.status,
            k8s_namespace=row.k8s_namespace,
            group_id=row.group_id,
            space_id=row.space_id,
            created_at=_iso(row.created_at),
            description=row.description,
            k8s_master_host=row.k8s_master_host,
            k8s_auth_type=row.k8s_auth_type,
            resource_quota=row.resource_quota,
            data=row.data,
        )

    async def delete(self, jiuwenclaw_id: str) -> bool:
        from jiuwenclaw_manager.core.instance.instance_provisioner import (
            terminate_local_if_present,
        )

        row = await get_instance_row(self._handler, jiuwenclaw_id)
        if row is None:
            return False
        await terminate_local_if_present(self._handler, jiuwenclaw_id)
        await delete_instance_row(self._handler, jiuwenclaw_id)
        return True

    async def services_status(self, jiuwenclaw_id: str) -> ServiceStatusList | None:
        parent = await get_instance_row(self._handler, jiuwenclaw_id)
        if parent is None:
            return None
        rows = await list_instance_services(self._handler, jiuwenclaw_id)
        items = [
            ServiceStatusItem(
                service_id=r.service_id,
                service_type=r.service_type,
                component_role=r.component_role,
                status=r.status,
                last_heartbeat=_iso(r.last_heartbeat),
                endpoint=r.endpoint,
                version=r.version,
            )
            for r in rows
        ]
        return ServiceStatusList(items=items)

    async def apply_heartbeat(
        self,
        *,
        jiuwenclaw_id: str,
        service_id: str,
        service_type: str,
        component_role: str,
        manager_id: str,
        endpoint: str | None,
        version: str | None,
        capabilities: dict | None,
        extra: dict | None,
    ) -> bool:
        if await get_instance_row(self._handler, jiuwenclaw_id) is None:
            return False
        await upsert_service_heartbeat(
            self._handler,
            jiuwenclaw_id=jiuwenclaw_id,
            service_id=service_id,
            service_type=service_type,
            component_role=component_role,
            manager_id=manager_id,
            endpoint=endpoint,
            version=version,
            capabilities=capabilities,
            extra_data=extra,
        )
        if extra and service_type == "gateway":
            raw_base = extra.get("management_api_base") or extra.get("agent_client_rest_base")
            if isinstance(raw_base, str) and raw_base.strip():
                await merge_instance_data(
                    self._handler,
                    jiuwenclaw_id,
                    {"management_api_base": raw_base.strip().rstrip("/")},
                )
        return True

    async def patch_instance_data(
        self, jiuwenclaw_id: str, body: PatchInstanceDataBody
    ) -> InstanceDetail | None:
        row = await merge_instance_data(self._handler, jiuwenclaw_id, body.data)
        if row is None:
            return None
        return await self.get(jiuwenclaw_id)
