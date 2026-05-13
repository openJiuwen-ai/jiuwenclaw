"""数据访问层：实例与注册服务。"""

from __future__ import annotations

import json
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from jiuwenclaw_manager.models.db.instance import InstanceInfo, ServiceInstance


class InstanceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, row: InstanceInfo) -> InstanceInfo:
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def get(self, jiuwenclaw_id: str) -> InstanceInfo | None:
        return await self._session.get(InstanceInfo, jiuwenclaw_id)

    async def list(
        self,
        *,
        status: str | None,
        offset: int,
        limit: int,
    ) -> tuple[Sequence[InstanceInfo], int]:
        cond = []
        if status:
            cond.append(InstanceInfo.status == status)
        count_stmt = select(func.count()).select_from(InstanceInfo).where(*cond)
        total = int((await self._session.execute(count_stmt)).scalar_one())
        list_stmt = (
            select(InstanceInfo)
            .where(*cond)
            .order_by(InstanceInfo.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        rows = (await self._session.execute(list_stmt)).scalars().all()
        return rows, total

    async def delete(self, instance: InstanceInfo) -> None:
        await self._session.delete(instance)

    async def merge_instance_data(self, jiuwenclaw_id: str, patch: dict) -> InstanceInfo | None:
        row = await self.get(jiuwenclaw_id)
        if row is None:
            return None
        merged = dict(row.data or {})
        merged.update(patch)
        row.data = merged
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def list_services(self, jiuwenclaw_id: str) -> Sequence[ServiceInstance]:
        stmt = select(ServiceInstance).where(ServiceInstance.jiuwenclaw_id == jiuwenclaw_id)
        return (await self._session.execute(stmt)).scalars().all()

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
    ) -> ServiceInstance:
        stmt = select(ServiceInstance).where(
            ServiceInstance.jiuwenclaw_id == jiuwenclaw_id,
            ServiceInstance.service_id == service_id,
        )
        existing = (await self._session.execute(stmt)).scalar_one_or_none()
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        if existing is None:
            row = ServiceInstance(
                jiuwenclaw_id=jiuwenclaw_id,
                service_id=service_id,
                service_type=service_type,
                component_role=component_role,
                manager_id=manager_id,
                endpoint=endpoint,
                version=version,
                capabilities=capabilities,
                data=extra_data,
                status="online",
                last_heartbeat=now,
            )
            self._session.add(row)
            await self._session.flush()
            await self._session.refresh(row)
            return row
        existing.endpoint = endpoint or existing.endpoint
        existing.version = version or existing.version
        if capabilities is not None:
            existing.capabilities = capabilities
        if extra_data is not None:
            merged = dict(existing.data or {})
            merged.update(extra_data)
            existing.data = merged
        existing.last_heartbeat = now
        if existing.status == "pending":
            existing.status = "online"
        else:
            existing.status = "online"
        await self._session.flush()
        await self._session.refresh(existing)
        return existing

    async def set_service_status(
        self,
        *,
        jiuwenclaw_id: str,
        service_id: str,
        status: str,
    ) -> bool:
        stmt = select(ServiceInstance).where(
            ServiceInstance.jiuwenclaw_id == jiuwenclaw_id,
            ServiceInstance.service_id == service_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return False
        row.status = status
        await self._session.flush()
        return True


def dumps_auth_config(cfg: dict) -> str:
    return json.dumps(cfg, ensure_ascii=False, separators=(",", ":"))
