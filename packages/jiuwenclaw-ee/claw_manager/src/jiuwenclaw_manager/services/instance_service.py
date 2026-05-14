"""实例域业务逻辑。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from jiuwenclaw_manager.models.db.instance import InstanceInfo
from jiuwenclaw_manager.models.schemas import (
    CreateInstanceBody,
    InstanceDetail,
    InstanceSummary,
    PatchInstanceDataBody,
    ServiceStatusItem,
    ServiceStatusList,
)
from jiuwenclaw_manager.repositories.instance_repo import InstanceRepository, dumps_auth_config


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


class InstanceService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = InstanceRepository(session)

    async def create(self, body: CreateInstanceBody) -> dict:
        jiuwenclaw_id = f"sp-{uuid.uuid4().hex[:12]}"
        data_dict: dict | None = None
        if body.management_api_base and str(body.management_api_base).strip():
            data_dict = {"management_api_base": str(body.management_api_base).strip().rstrip("/")}
        row = InstanceInfo(
            jiuwenclaw_id=jiuwenclaw_id,
            jiuwenclaw_name=body.jiuwenclaw_name,
            creator_id=body.creator_id,
            description=body.description,
            k8s_master_host=body.k8s_master_host,
            k8s_auth_type=body.k8s_auth_type,
            k8s_auth_config=dumps_auth_config(body.k8s_auth_config),
            k8s_namespace=body.k8s_namespace,
            status="active",
            resource_quota=body.resource_quota,
            data=data_dict,
            group_id=body.group_id,
            space_id=body.space_id,
        )
        await self._repo.create(row)
        return {"jiuwenclaw_id": jiuwenclaw_id, "status": row.status}

    async def list_instances(
        self, *, page: int, page_size: int, status: str | None
    ) -> dict:
        page = max(page, 1)
        page_size = min(max(page_size, 1), 200)
        offset = (page - 1) * page_size
        rows, total = await self._repo.list(status=status, offset=offset, limit=page_size)
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
        return {"items": [i.model_dump() for i in items], "total": total, "page": page, "page_size": page_size}

    async def get(self, jiuwenclaw_id: str) -> InstanceDetail | None:
        row = await self._repo.get(jiuwenclaw_id)
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
        from jiuwenclaw_manager.services.instance_provisioner import terminate_local_if_present

        row = await self._repo.get(jiuwenclaw_id)
        if row is None:
            return False
        await terminate_local_if_present(self._session, jiuwenclaw_id)
        await self._repo.delete(row)
        return True

    async def services_status(self, jiuwenclaw_id: str) -> ServiceStatusList | None:
        parent = await self._repo.get(jiuwenclaw_id)
        if parent is None:
            return None
        rows = await self._repo.list_services(jiuwenclaw_id)
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
        if await self._repo.get(jiuwenclaw_id) is None:
            return False
        await self._repo.upsert_service_heartbeat(
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
                inst = await self._repo.get(jiuwenclaw_id)
                if inst is not None:
                    md = dict(inst.data or {})
                    md["management_api_base"] = raw_base.strip().rstrip("/")
                    inst.data = md
                    await self._session.flush()
        return True

    async def process_instance_mq_payload(
            self, payload: dict[str, Any], routing_key: str
    ) -> None:
        """处理设计文档 5.2 的 instance.* 事件（RabbitMQ 消息体 + routing_key）。"""
        et = str(payload.get("event_type") or "")
        blob = f"{routing_key} {et}".lower()
        if "heartbeat" in blob:
            kind = "heartbeat"
        elif "offline" in blob:
            kind = "offline"
        elif "online" in blob:
            kind = "online"
        else:
            raise ValueError(f"unsupported instance event: routing_key={routing_key!r} event_type={et!r}")

        jiuwenclaw_id = str(payload.get("jiuwenclaw_id") or "").strip()
        service_id = str(payload.get("service_id") or "").strip()
        manager_id = str(payload.get("manager_id") or "").strip()
        service_type = str(payload.get("service_type") or "").strip()
        if not jiuwenclaw_id:
            raise ValueError("missing jiuwenclaw_id")
        if not service_id:
            raise ValueError("missing service_id")
        if not manager_id:
            raise ValueError("missing manager_id")
        if not service_type:
            raise ValueError("missing service_type")

        component_role = str(payload.get("component_role") or "").strip()
        if not component_role:
            st = service_type.lower()
            if "gateway" in st:
                component_role = "gateway"
            elif "agent" in st:
                component_role = "agent_server"
            else:
                component_role = service_type

        data = payload.get("data") if isinstance(payload.get("data"), dict) else None
        endpoint = None
        version = None
        capabilities = None
        if data:
            endpoint = data.get("endpoint") if isinstance(data.get("endpoint"), str) else None
            version = data.get("version") if isinstance(data.get("version"), str) else None
            capabilities = data.get("capabilities") if isinstance(data.get("capabilities"), dict) else None

        if await self._repo.get(jiuwenclaw_id) is None:
            raise ValueError(f"unknown jiuwenclaw_id={jiuwenclaw_id}")

        if kind == "offline":
            await self._repo.set_service_status(
                jiuwenclaw_id=jiuwenclaw_id, service_id=service_id, status="offline"
            )
            return

        # 提前提取条件，避免过多的布尔表达式
        is_heartbeat_or_online = kind in ("heartbeat", "online")
        has_gateway_service = data and service_type == "gateway"
        has_management_base = (
                data and
                (data.get("management_api_base") or data.get("agent_client_rest_base"))
        )

        if is_heartbeat_or_online:
            await self._repo.upsert_service_heartbeat(
                jiuwenclaw_id=jiuwenclaw_id,
                service_id=service_id,
                service_type=service_type,
                component_role=component_role,
                manager_id=manager_id,
                endpoint=endpoint,
                version=version,
                capabilities=capabilities,
                extra_data=data,
            )

            if has_gateway_service and has_management_base:
                raw_base = data.get("management_api_base") or data.get("agent_client_rest_base")
                if isinstance(raw_base, str) and raw_base.strip():
                    inst = await self._repo.get(jiuwenclaw_id)
                    if inst is not None:
                        md = dict(inst.data or {})
                        md["management_api_base"] = raw_base.strip().rstrip("/")
                        inst.data = md
                        await self._session.flush()
            return

    async def patch_instance_data(self, jiuwenclaw_id: str, body: PatchInstanceDataBody) -> InstanceDetail | None:
        row = await self._repo.merge_instance_data(jiuwenclaw_id, body.data)
        if row is None:
            return None
        return await self.get(jiuwenclaw_id)
