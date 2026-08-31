# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""服务配置模板：将 Claw Manager 下发的 service_config_templates 写入 Gateway 本地库。"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from jiuwenswarm.gateway.config.enterprise.repository import EnterpriseRecordRepository
from jiuwenswarm.gateway.config.enterprise.tables.template_models import SERVICE_CONFIG_TEMPLATE_TABLE_DEF

from ...infrastructure.repository_access import require_enterprise_repository
from ...infrastructure.utils import parse_iso_datetime, utc_now
from ...schemas.template_schemas import ServiceConfigTemplateUpdateRequest

_TABLE = SERVICE_CONFIG_TEMPLATE_TABLE_DEF.table_name
_ALLOWED_IMAGE_PULL_POLICIES = frozenset({"Always", "IfNotPresent", "Never"})
logger = logging.getLogger(__name__)


def _normalize_template_id(template_id: Any) -> str:
    normalized = str(template_id or "").strip()
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


async def _get_row_for_instance(
    repo: EnterpriseRecordRepository,
    template_id: str,
) -> dict[str, Any] | None:
    return await repo.get(template_id=_normalize_template_id(template_id))


async def update_service_config_template(
    repo: EnterpriseRecordRepository,
    template_id: str,
    request: ServiceConfigTemplateUpdateRequest,
    *,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    tid = _normalize_template_id(template_id)
    row = existing if existing is not None else await _get_row_for_instance(repo, tid)
    if row is None:
        return None

    updates = request.model_dump(exclude_unset=True)
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

    if not updates:
        raise ValueError("请求未包含任何可更新的业务字段")

    updates["updated_at"] = utc_now()
    updated = await repo.update({"template_id": tid}, updates)
    if updated is None:
        return None
    return {"template_id": str(updated.get("template_id", tid))}


async def delete_service_config_template(
    repo: EnterpriseRecordRepository,
    template_id: str,
) -> bool:
    tid = _normalize_template_id(template_id)
    existing = await _get_row_for_instance(repo, tid)
    if existing is None:
        return False
    return await repo.delete(template_id=tid)


def _build_row_from_template(
    template: dict[str, Any],
    *,
    now: datetime,
) -> dict[str, Any]:
    template_uuid = _normalize_template_id(template.get("template_id"))
    return {
        "template_id": template_uuid,
        "template_name": str(template["template_name"]).strip(),
        "description": template.get("description"),
        "agent_image": str(template["agent_image"]).strip(),
        "namespace": str(template["namespace"]).strip(),
        "pod_name": (
            str(template["pod_name"]).strip() if template.get("pod_name") else None
        ),
        "container_name": str(template["container_name"]).strip(),
        "container_port": int(template["container_port"]),
        "port_name": str(template.get("port_name", "http")).strip(),
        "image_pull_policy": _normalize_image_pull_policy(
            str(template.get("image_pull_policy", "IfNotPresent"))
        ),
        "replicas": int(template.get("replicas", 1)),
        "kubeconfig": (
            str(template["kubeconfig"]).strip() if template.get("kubeconfig") else None
        ),
        "agent_runtime": (
            str(template["agent_runtime"]).strip()
            if template.get("agent_runtime")
            else None
        ),
        "readiness_initial_delay": int(template.get("readiness_initial_delay", 10)),
        "readiness_period": int(template.get("readiness_period", 5)),
        "ready_timeout": int(template.get("ready_timeout", 300)),
        "ready_poll_interval": int(template.get("ready_poll_interval", 5)),
        "nfs_server": (
            str(template["nfs_server"]).strip() if template.get("nfs_server") else None
        ),
        "nfs_path": str(template.get("nfs_path", "/")).strip(),
        "nfs_mount_path": (
            str(template["nfs_mount_path"]).strip()
            if template.get("nfs_mount_path")
            else None
        ),
        "agent_cpu_request": (
            str(template["agent_cpu_request"]).strip()
            if template.get("agent_cpu_request")
            else None
        ),
        "agent_memory_request": (
            str(template["agent_memory_request"]).strip()
            if template.get("agent_memory_request")
            else None
        ),
        "agent_cpu_limit": (
            str(template["agent_cpu_limit"]).strip()
            if template.get("agent_cpu_limit")
            else None
        ),
        "agent_memory_limit": (
            str(template["agent_memory_limit"]).strip()
            if template.get("agent_memory_limit")
            else None
        ),
        "jiuwenbox_cpu_request": (
            str(template["jiuwenbox_cpu_request"]).strip()
            if template.get("jiuwenbox_cpu_request")
            else None
        ),
        "jiuwenbox_memory_request": (
            str(template["jiuwenbox_memory_request"]).strip()
            if template.get("jiuwenbox_memory_request")
            else None
        ),
        "jiuwenbox_cpu_limit": (
            str(template["jiuwenbox_cpu_limit"]).strip()
            if template.get("jiuwenbox_cpu_limit")
            else None
        ),
        "jiuwenbox_memory_limit": (
            str(template["jiuwenbox_memory_limit"]).strip()
            if template.get("jiuwenbox_memory_limit")
            else None
        ),
        "min_idle_services": int(template.get("min_idle_services", 1)),
        "max_services": int(template.get("max_services", 20)),
        "service_concurrency": int(template.get("service_concurrency", 10)),
        "service_ttl": int(template.get("service_ttl", 180)),
        "autoscale_interval": float(template.get("autoscale_interval", 5)),
        "message_timeout": int(template.get("message_timeout", 60)),
        "session_concurrency": int(template.get("session_concurrency", 10)),
        "session_ttl": int(template.get("session_ttl", 60)),
        "enabled": bool(template.get("enabled", True)),
        "data": template.get("data"),
        "created_at": now,
        "updated_at": now,
    }


async def _upsert_service_config_template_from_sync(
    repo: EnterpriseRecordRepository,
    template: dict[str, Any]
) -> None:
    now = utc_now()
    tid = _normalize_template_id(template.get("template_id"))
    existing = await _get_row_for_instance(repo, tid)
    row_data = _build_row_from_template(
        template, now=now
    )
    if existing is None:
        await repo.create(row_data)
        return
    created_at = existing.get("created_at")
    if created_at is not None:
        # existing 可能是 ISO 字符串；asyncpg 要求 datetime
        row_data["created_at"] = parse_iso_datetime(created_at) or now
    updates = {
        k: v for k, v in row_data.items() if k not in ("template_id",)
    }
    updates["updated_at"] = utc_now()
    await repo.update({"template_id": tid}, updates)


async def _sync_service_config_templates_records(
    repo: EnterpriseRecordRepository,
    templates: list[dict[str, Any]]
) -> dict[str, Any]:
    incoming_ids: set[str] = set()
    synced = 0
    for item in templates:
        if not isinstance(item, dict):
            raise ValueError("service_config_templates.sync templates must be objects")
        tid = _normalize_template_id(item.get("template_id"))
        incoming_ids.add(tid)
        await _upsert_service_config_template_from_sync(
            repo, item
        )
        synced += 1
    deleted = 0
    for row in await repo.list():
        tid = str(row.get("template_id") or "")
        if tid and tid not in incoming_ids:
            if await delete_service_config_template(repo, tid):
                deleted += 1
    return {"synced_count": synced, "deleted_count": deleted}


class ServiceConfigTemplateService:

    async def create(
        self,
        template: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(template, dict):
            raise ValueError("service_config_templates.create requires template object")
        repo = require_enterprise_repository(_TABLE)
        await _upsert_service_config_template_from_sync(
            repo, template
        )
        result = {
            "template_id": _normalize_template_id(template.get("template_id")),
        }
        logger.info(
            "[ManagerConfigReceiver] service_config_templates create template_id=%s",
            result["template_id"],
        )
        return result

    async def update(
        self,
        template_id: str,
        updates: dict[str, Any],
    ) -> None:
        if template_id is None:
            raise ValueError("service_config_templates.update requires template_id")
        if not isinstance(updates, dict) or not updates:
            raise ValueError("service_config_templates.update requires non-empty updates")
        req = ServiceConfigTemplateUpdateRequest.model_validate(updates)
        tid = _normalize_template_id(template_id)
        repo = require_enterprise_repository(_TABLE)
        existing = await _get_row_for_instance(repo, tid)
        row = await update_service_config_template(
            repo, tid, req, existing=existing
        )
        if row is None:
            raise ValueError(f"service config template template_id={tid!r} not found")
        logger.info(
            "[ManagerConfigReceiver] service_config_templates update template_id=%s",
            tid,
        )

    async def delete(self, template_id: str) -> None:
        if template_id is None:
            raise ValueError("service_config_templates.delete requires template_id")
        tid = _normalize_template_id(template_id)
        repo = require_enterprise_repository(_TABLE)
        await delete_service_config_template(repo, tid)
        logger.info(
            "[ManagerConfigReceiver] service_config_templates delete template_id=%s",
            tid,
        )
