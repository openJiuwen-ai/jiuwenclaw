"""服务配置模板 service_config_template 业务逻辑。"""

from __future__ import annotations

from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.core.template.push_template_to_gateway import (
    assert_template_deletable,
    push_template_to_referencing_gateways,
)
from jiuwenclaw_manager.infrastructure.common import resolve_order_by
from jiuwenclaw_manager.infrastructure.utils import iso_datetime, new_uuid4, utc_now
from jiuwenclaw_manager.models.template_models import SERVICE_CONFIG_TEMPLATE_TABLE_DEF
from jiuwenclaw_manager.schemas.template_schemas import (
    ServiceConfigTemplateCreateBody,
    ServiceConfigTemplateListQuery,
    ServiceConfigTemplateOut,
    ServiceConfigTemplateUpdateBody,
)

_TABLE = SERVICE_CONFIG_TEMPLATE_TABLE_DEF.table_name
_LIST_ALL_CAP = 10_000
_ALLOWED_SORT_FIELDS = frozenset({
    "template_name",
    "description",
    "agent_image",
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
        str(getattr(row, "agent_image", "") or ""),
    ]
    return any(needle in field.lower() for field in fields)


def row_to_out(row: Any) -> ServiceConfigTemplateOut:
    return ServiceConfigTemplateOut(
        id=row.id,
        template_id=str(row.template_id),
        template_name=row.template_name,
        description=row.description,
        agent_image=row.agent_image,
        namespace=row.namespace,
        pod_name=row.pod_name,
        container_name=row.container_name,
        container_port=int(row.container_port),
        port_name=row.port_name,
        image_pull_policy=row.image_pull_policy,
        replicas=int(row.replicas),
        kubeconfig=row.kubeconfig,
        agent_runtime=row.agent_runtime,
        readiness_initial_delay=int(row.readiness_initial_delay),
        readiness_period=int(row.readiness_period),
        ready_timeout=int(row.ready_timeout),
        ready_poll_interval=int(row.ready_poll_interval),
        nfs_server=row.nfs_server,
        nfs_path=row.nfs_path,
        nfs_mount_path=row.nfs_mount_path,
        agent_cpu_request=row.agent_cpu_request,
        agent_memory_request=row.agent_memory_request,
        agent_cpu_limit=row.agent_cpu_limit,
        agent_memory_limit=row.agent_memory_limit,
        jiuwenbox_cpu_request=row.jiuwenbox_cpu_request,
        jiuwenbox_memory_request=row.jiuwenbox_memory_request,
        jiuwenbox_cpu_limit=row.jiuwenbox_cpu_limit,
        jiuwenbox_memory_limit=row.jiuwenbox_memory_limit,
        min_idle_services=int(row.min_idle_services),
        max_services=int(row.max_services),
        service_concurrency=int(row.service_concurrency),
        service_ttl=int(row.service_ttl),
        autoscale_interval=float(row.autoscale_interval),
        message_timeout=int(row.message_timeout),
        session_concurrency=int(row.session_concurrency),
        session_ttl=int(row.session_ttl),
        enabled=row.enabled,
        data=row.data,
        created_at=iso_datetime(row.created_at),
        updated_at=iso_datetime(row.updated_at),
    )


class ServiceConfigTemplateService:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    @staticmethod
    def _build_row_for_create(
        body: ServiceConfigTemplateCreateBody, *, template_id: str
    ) -> dict[str, Any]:
        return {
            "template_id": template_id,
            "template_name": body.template_name,
            "description": body.description,
            "agent_image": body.agent_image,
            "namespace": body.namespace,
            "pod_name": body.pod_name,
            "container_name": body.container_name,
            "container_port": body.container_port,
            "port_name": body.port_name or "http",
            "image_pull_policy": body.image_pull_policy,
            "replicas": body.replicas,
            "kubeconfig": body.kubeconfig,
            "agent_runtime": body.agent_runtime,
            "readiness_initial_delay": body.readiness_initial_delay,
            "readiness_period": body.readiness_period,
            "ready_timeout": body.ready_timeout,
            "ready_poll_interval": body.ready_poll_interval,
            "nfs_server": body.nfs_server,
            "nfs_path": body.nfs_path or "/",
            "nfs_mount_path": body.nfs_mount_path,
            "agent_cpu_request": body.agent_cpu_request,
            "agent_memory_request": body.agent_memory_request,
            "agent_cpu_limit": body.agent_cpu_limit,
            "agent_memory_limit": body.agent_memory_limit,
            "jiuwenbox_cpu_request": body.jiuwenbox_cpu_request,
            "jiuwenbox_memory_request": body.jiuwenbox_memory_request,
            "jiuwenbox_cpu_limit": body.jiuwenbox_cpu_limit,
            "jiuwenbox_memory_limit": body.jiuwenbox_memory_limit,
            "min_idle_services": body.min_idle_services,
            "max_services": body.max_services,
            "service_concurrency": body.service_concurrency,
            "service_ttl": body.service_ttl,
            "autoscale_interval": body.autoscale_interval,
            "message_timeout": body.message_timeout,
            "session_concurrency": body.session_concurrency,
            "session_ttl": body.session_ttl,
            "enabled": body.enabled,
            "data": body.data,
        }

    async def create(
        self,
        body: ServiceConfigTemplateCreateBody,
    ) -> ServiceConfigTemplateOut:
        template_uuid = new_uuid4()
        row = self._build_row_for_create(body, template_id=template_uuid)
        now = utc_now()
        payload = dict(row)
        payload.setdefault("created_at", now)
        payload.setdefault("updated_at", now)
        created = await self._handler.create(_TABLE, payload)
        return row_to_out(created)

    async def get(self, template_id: str) -> ServiceConfigTemplateOut | None:
        row = await self._handler.get(_TABLE, {"template_id": template_id})
        if row is None:
            return None
        return row_to_out(row)

    async def list_templates(
        self,
        query: ServiceConfigTemplateListQuery,
    ) -> dict[str, Any]:
        page = max(query.page, 1)
        page_size = min(max(query.page_size, 1), 200)
        filters: dict[str, Any] = {}
        if query.enabled is not None:
            filters["enabled"] = query.enabled
        if query.namespace is not None:
            filters["namespace"] = query.namespace

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
        body: ServiceConfigTemplateUpdateBody,
    ) -> ServiceConfigTemplateOut | None:
        updates = body.model_dump(exclude_unset=True)

        if not updates:
            row = await self._handler.get(_TABLE, {"template_id": template_id})
            return row_to_out(row) if row is not None else None

        existing = await self._handler.get(_TABLE, {"template_id": template_id})
        if existing is None:
            return None

        await push_template_to_referencing_gateways(
            self._handler,
            "service_config_templates",
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
            self._handler, template_id, "service_config_templates"
        )
        await push_template_to_referencing_gateways(
            self._handler,
            "service_config_templates",
            "delete",
            template_id=template_id,
        )
        return await self._handler.delete(_TABLE, {"template_id": template_id})
