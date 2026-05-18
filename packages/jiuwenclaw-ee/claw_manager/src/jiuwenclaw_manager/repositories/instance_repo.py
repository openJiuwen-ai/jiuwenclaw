"""数据访问层：实例与注册服务（DBHandler）。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.core.utils import utc_now
from jiuwenclaw_manager.models.table_defs.instance_models import (
    INSTANCE_INFO_TABLE_DEF,
    SERVICE_INSTANCE_TABLE_DEF,
)

_INSTANCE = INSTANCE_INFO_TABLE_DEF.table_name
_SERVICE = SERVICE_INSTANCE_TABLE_DEF.table_name


class InstanceRepository:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def create(self, row_data: dict[str, Any]) -> Any:
        now = utc_now()
        payload = dict(row_data)
        payload.setdefault("created_at", now)
        payload.setdefault("updated_at", now)
        return await self._handler.create(_INSTANCE, payload)

    async def get(self, jiuwenclaw_id: str) -> Any | None:
        return await self._handler.get(_INSTANCE, {"jiuwenclaw_id": jiuwenclaw_id})

    async def list(
        self,
        *,
        status: str | None,
        offset: int,
        limit: int,
    ) -> tuple[Sequence[Any], int]:
        filters: dict[str, Any] = {}
        if status:
            filters["status"] = status
        total = await self._handler.count_records(_INSTANCE, filters)
        rows = await self._handler.list_records(
            _INSTANCE, filters, limit=limit, offset=offset
        )
        return rows, int(total)

    async def delete(self, jiuwenclaw_id: str) -> None:
        services = await self.list_services(jiuwenclaw_id)
        for svc in services:
            sid = getattr(svc, "id", None)
            if sid is not None:
                await self._handler.delete(_SERVICE, {"id": int(sid)})
        await self._handler.delete(_INSTANCE, {"jiuwenclaw_id": jiuwenclaw_id})

    async def merge_instance_data(self, jiuwenclaw_id: str, patch: dict) -> Any | None:
        row = await self.get(jiuwenclaw_id)
        if row is None:
            return None
        merged = dict(getattr(row, "data", None) or {})
        merged.update(patch)
        now = utc_now()
        updated = await self._handler.update(
            _INSTANCE,
            {"jiuwenclaw_id": jiuwenclaw_id},
            {"data": merged, "updated_at": now},
        )
        return updated

    async def list_services(self, jiuwenclaw_id: str) -> Sequence[Any]:
        return await self._handler.list_records(
            _SERVICE,
            {"jiuwenclaw_id": jiuwenclaw_id},
            limit=10_000,
            offset=0,
        )

    async def upsert_service_heartbeat(
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
        extra_data: dict | None,
    ) -> Any:
        rows = await self._handler.list_records(
            _SERVICE,
            {"jiuwenclaw_id": jiuwenclaw_id, "service_id": service_id},
            limit=1,
            offset=0,
        )
        now = utc_now()
        if not rows:
            return await self._handler.create(
                _SERVICE,
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
        return await self._handler.update(_SERVICE, {"id": row_id}, updates)

    async def set_service_status(
        self,
        *,
        jiuwenclaw_id: str,
        service_id: str,
        status: str,
    ) -> bool:
        rows = await self._handler.list_records(
            _SERVICE,
            {"jiuwenclaw_id": jiuwenclaw_id, "service_id": service_id},
            limit=1,
            offset=0,
        )
        if not rows:
            return False
        row_id = int(getattr(rows[0], "id"))
        updated = await self._handler.update(
            _SERVICE,
            {"id": row_id},
            {"status": status, "updated_at": utc_now()},
        )
        return updated is not None


def dumps_auth_config(cfg: dict) -> str:
    return json.dumps(cfg, ensure_ascii=False, separators=(",", ":"))
