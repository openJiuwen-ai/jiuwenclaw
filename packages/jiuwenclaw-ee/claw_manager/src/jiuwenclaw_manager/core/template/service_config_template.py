"""服务配置模板 service_config_template 业务逻辑。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.infrastructure.utils import iso_datetime, new_template_id, utc_now
from jiuwenclaw_manager.manager_ws_server.server import push_config_op_to_all
from jiuwenclaw_manager.models.template_models import SERVICE_CONFIG_TEMPLATE_TABLE_DEF
from jiuwenclaw_manager.schemas.template_schemas import (
    ServiceConfigTemplateCreateBody,
    ServiceConfigTemplateOut,
    ServiceConfigTemplateUpdateBody,
)

_TABLE = SERVICE_CONFIG_TEMPLATE_TABLE_DEF.table_name
_CONFIG_SECTION = "service_config_templates"
_ALLOWED_IMAGE_PULL_POLICIES = frozenset({"Always", "IfNotPresent", "Never"})


async def push_service_config_templates_to_all_gateways(
    op: str,
    *,
    template: dict[str, Any] | None = None,
    template_id: str | None = None,
    updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """向所有已注册 Gateway 推送服务配置模板变更。"""
    payload: dict[str, Any] = {"op": op}
    if template is not None:
        payload["template"] = template
    if template_id is not None:
        payload["template_id"] = template_id
    if updates is not None:
        payload["updates"] = updates
    return await push_config_op_to_all(_CONFIG_SECTION, payload)


def _template_pk(template_id: str) -> dict[str, Any]:
    return {"template_id": template_id.strip()}


def _normalize_template_id(template_id: str) -> str:
    normalized = template_id.strip()
    if not normalized:
        raise ValueError("template_id is required")
    if len(normalized) > 100:
        raise ValueError("template_id must be at most 100 characters")
    return normalized


def _normalize_image_pull_policy(value: str) -> str:
    normalized = value.strip()
    if normalized not in _ALLOWED_IMAGE_PULL_POLICIES:
        raise ValueError(
            f"image_pull_policy must be one of {sorted(_ALLOWED_IMAGE_PULL_POLICIES)}, "
            f"got {value!r}"
        )
    return normalized


def _autoscale_interval_to_db(value: float) -> str:
    return str(value)


def _autoscale_interval_from_db(value: Any) -> float:
    if value is None:
        return 0.2
    return float(value)


def _row_to_out(row: Any) -> ServiceConfigTemplateOut:
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
        autoscale_interval=_autoscale_interval_from_db(row.autoscale_interval),
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
            "agent_image": row["agent_image"],
            "namespace": row["namespace"],
            "pod_name": row.get("pod_name"),
            "container_name": row["container_name"],
            "container_port": int(row["container_port"]),
            "port_name": row.get("port_name", "http"),
            "image_pull_policy": row.get("image_pull_policy", "IfNotPresent"),
            "replicas": int(row.get("replicas", 1)),
            "kubeconfig": row.get("kubeconfig"),
            "agent_runtime": row.get("agent_runtime"),
            "readiness_initial_delay": int(row.get("readiness_initial_delay", 5)),
            "readiness_period": int(row.get("readiness_period", 10)),
            "ready_timeout": int(row.get("ready_timeout", 300)),
            "ready_poll_interval": int(row.get("ready_poll_interval", 2)),
            "nfs_server": row.get("nfs_server"),
            "nfs_path": row.get("nfs_path", "/"),
            "nfs_mount_path": row.get("nfs_mount_path"),
            "agent_cpu_request": row.get("agent_cpu_request", ""),
            "agent_memory_request": row.get("agent_memory_request", ""),
            "agent_cpu_limit": row.get("agent_cpu_limit", ""),
            "agent_memory_limit": row.get("agent_memory_limit", ""),
            "jiuwenbox_cpu_request": row.get("jiuwenbox_cpu_request", ""),
            "jiuwenbox_memory_request": row.get("jiuwenbox_memory_request", ""),
            "jiuwenbox_cpu_limit": row.get("jiuwenbox_cpu_limit", ""),
            "jiuwenbox_memory_limit": row.get("jiuwenbox_memory_limit", ""),
            "min_idle_services": int(row.get("min_idle_services", 1)),
            "max_services": int(row.get("max_services", 10)),
            "service_concurrency": int(row.get("service_concurrency", 10)),
            "service_ttl": int(row.get("service_ttl", 30)),
            "autoscale_interval": _autoscale_interval_from_db(
                row.get("autoscale_interval", "0.2")
            ),
            "message_timeout": int(row.get("message_timeout", 300)),
            "session_concurrency": int(row.get("session_concurrency", 10)),
            "session_ttl": int(row.get("session_ttl", 20)),
            "enabled": row.get("enabled", True),
            "data": row.get("data"),
            "created_at": iso_datetime(row.get("created_at") or now),
            "updated_at": iso_datetime(row.get("updated_at") or now),
        }

    @staticmethod
    def _build_row_for_create(
        body: ServiceConfigTemplateCreateBody, *, template_id: str
    ) -> dict[str, Any]:
        return {
            "template_id": template_id,
            "template_name": body.template_name.strip(),
            "description": body.description.strip() if body.description else None,
            "agent_image": body.agent_image.strip(),
            "namespace": body.namespace.strip(),
            "pod_name": body.pod_name.strip() if body.pod_name else None,
            "container_name": body.container_name.strip(),
            "container_port": body.container_port,
            "port_name": body.port_name.strip() if body.port_name else "http",
            "image_pull_policy": _normalize_image_pull_policy(
                body.image_pull_policy.strip() if body.image_pull_policy else "IfNotPresent"
            ),
            "replicas": body.replicas,
            "kubeconfig": body.kubeconfig.strip() if body.kubeconfig else None,
            "agent_runtime": body.agent_runtime.strip() if body.agent_runtime else None,
            "readiness_initial_delay": body.readiness_initial_delay,
            "readiness_period": body.readiness_period,
            "ready_timeout": body.ready_timeout,
            "ready_poll_interval": body.ready_poll_interval,
            "nfs_server": body.nfs_server.strip() if body.nfs_server else None,
            "nfs_path": body.nfs_path.strip() if body.nfs_path else "/",
            "nfs_mount_path": body.nfs_mount_path.strip() if body.nfs_mount_path else None,
            "agent_cpu_request": body.agent_cpu_request.strip() if body.agent_cpu_request else None,
            "agent_memory_request": body.agent_memory_request.strip() if body.agent_memory_request else None,
            "agent_cpu_limit": body.agent_cpu_limit.strip() if body.agent_cpu_limit else None,
            "agent_memory_limit": body.agent_memory_limit.strip() if body.agent_memory_limit else None,
            "jiuwenbox_cpu_request": body.jiuwenbox_cpu_request.strip() if body.jiuwenbox_cpu_request else None,
            "jiuwenbox_memory_request": (
                body.jiuwenbox_memory_request.strip()
                if body.jiuwenbox_memory_request
                else None
            ),
            "jiuwenbox_cpu_limit": body.jiuwenbox_cpu_limit.strip() if body.jiuwenbox_cpu_limit else None,
            "jiuwenbox_memory_limit": body.jiuwenbox_memory_limit.strip() if body.jiuwenbox_memory_limit else None,
            "min_idle_services": body.min_idle_services,
            "max_services": body.max_services,
            "service_concurrency": body.service_concurrency,
            "service_ttl": body.service_ttl,
            "autoscale_interval": _autoscale_interval_to_db(body.autoscale_interval),
            "message_timeout": body.message_timeout,
            "session_concurrency": body.session_concurrency,
            "session_ttl": body.session_ttl,
            "enabled": body.enabled,
            "data": body.data,
        }

    @staticmethod
    def _normalize_updates(updates: dict[str, Any]) -> dict[str, Any]:
        strip_fields = (
            "template_name",
            "agent_image",
            "namespace",
            "pod_name",
            "container_name",
            "port_name",
            "kubeconfig",
            "agent_runtime",
            "nfs_server",
            "nfs_path",
            "nfs_mount_path",
            "agent_cpu_request",
            "agent_memory_request",
            "agent_cpu_limit",
            "agent_memory_limit",
            "jiuwenbox_cpu_request",
            "jiuwenbox_memory_request",
            "jiuwenbox_cpu_limit",
            "jiuwenbox_memory_limit",
        )
        for field in strip_fields:
            if field in updates and updates[field] is not None:
                updates[field] = str(updates[field]).strip()
        if "image_pull_policy" in updates and updates["image_pull_policy"] is not None:
            updates["image_pull_policy"] = _normalize_image_pull_policy(
                updates["image_pull_policy"]
            )
        if "autoscale_interval" in updates and updates["autoscale_interval"] is not None:
            updates["autoscale_interval"] = _autoscale_interval_to_db(
                float(updates["autoscale_interval"])
            )
        return updates

    async def create(
        self,
        body: ServiceConfigTemplateCreateBody,
    ) -> ServiceConfigTemplateOut:
        template_uuid = new_template_id()
        row = self._build_row_for_create(body, template_id=template_uuid)
        now = utc_now()
        payload = dict(row)
        payload.setdefault("created_at", now)
        payload.setdefault("updated_at", now)
        await push_service_config_templates_to_all_gateways(
            "create",
            template=self._template_dict_for_push(payload, now=now),
        )
        created = await self._handler.create(_TABLE, payload)
        return _row_to_out(created)

    async def get(self, template_id: str) -> ServiceConfigTemplateOut | None:
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
        namespace: str | None,
    ) -> dict[str, Any]:
        page = max(page, 1)
        page_size = min(max(page_size, 1), 200)
        filters: dict[str, Any] = {}
        if enabled is not None:
            filters["enabled"] = enabled
        if namespace is not None:
            filters["namespace"] = namespace.strip()

        offset = (page - 1) * page_size
        rows = await self._handler.list_records(
            _TABLE, filters, limit=page_size, offset=offset
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
        body: ServiceConfigTemplateUpdateBody,
    ) -> ServiceConfigTemplateOut | None:
        tid = _normalize_template_id(template_id)
        updates = body.model_dump(exclude_unset=True)

        if not updates:
            row = await self._handler.get(_TABLE, _template_pk(tid))
            return _row_to_out(row) if row is not None else None

        existing = await self._handler.get(_TABLE, _template_pk(tid))
        if existing is None:
            return None

        updates = self._normalize_updates(updates)

        await push_service_config_templates_to_all_gateways(
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
        await push_service_config_templates_to_all_gateways(
            "delete",
            template_id=tid,
        )
        return await self._db_delete_template(tid)
