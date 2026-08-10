# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""服务配置模板 WebSocket 同步：将 Claw Manager 下发的 service_config_templates 写入 Gateway 本地库。"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from ...infrastructure.db import ensure_db_handler
from ...infrastructure.utils import get_jiuwenclaw_id, parse_iso_datetime, utc_now
from ...models.template_models import SERVICE_CONFIG_TEMPLATE_TABLE_DEF
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


def _template_pk(jiuwenclaw_id: str, template_id: str) -> dict[str, str]:
    return {
        "jiuwenclaw_id": jiuwenclaw_id,
        "template_id": _normalize_template_id(template_id),
    }


async def _get_row_for_instance(
    handler: DBHandler,
    template_id: str,
    jiuwenclaw_id: str,
) -> Any | None:
    return await handler.get(_TABLE, _template_pk(jiuwenclaw_id, template_id))


async def update_service_config_template(
    handler: DBHandler,
    template_id: str,
    request: ServiceConfigTemplateUpdateRequest,
    *,
    existing: Any | None = None,
) -> dict[str, Any] | None:
    jiuwenclaw_id = get_jiuwenclaw_id()
    tid = _normalize_template_id(template_id)
    row = existing if existing is not None else await _get_row_for_instance(
        handler, tid, jiuwenclaw_id
    )
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
    updated = await handler.update(_TABLE, _template_pk(jiuwenclaw_id, tid), updates)
    if updated is None:
        return None
    return {"template_id": str(getattr(updated, "template_id", tid))}


async def delete_service_config_template(handler: DBHandler, template_id: str) -> bool:
    jiuwenclaw_id = get_jiuwenclaw_id()
    tid = _normalize_template_id(template_id)
    existing = await _get_row_for_instance(handler, tid, jiuwenclaw_id)
    if existing is None:
        return False
    return await handler.delete(_TABLE, _template_pk(jiuwenclaw_id, tid))


def _build_row_from_template(
    template: dict[str, Any],
    *,
    jiuwenclaw_id: str,
    now: datetime,
) -> dict[str, Any]:
    template_uuid = _normalize_template_id(template.get("template_id"))
    return {
        "jiuwenclaw_id": jiuwenclaw_id,
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
        "created_at": parse_iso_datetime(template.get("created_at")) or now,
        "updated_at": parse_iso_datetime(template.get("updated_at")) or now,
    }


async def _upsert_service_config_template_from_sync(
    handler: DBHandler,
    template: dict[str, Any],
    *,
    jiuwenclaw_id: str,
) -> None:
    now = utc_now()
    tid = _normalize_template_id(template.get("template_id"))
    existing = await _get_row_for_instance(handler, tid, jiuwenclaw_id)
    row_data = _build_row_from_template(
        template, jiuwenclaw_id=jiuwenclaw_id, now=now
    )
    if existing is None:
        await handler.create(_TABLE, row_data)
        return
    created_at = getattr(existing, "created_at", None)
    if created_at is not None:
        row_data["created_at"] = created_at
    updates = {
        k: v for k, v in row_data.items() if k not in ("jiuwenclaw_id", "template_id")
    }
    updates["updated_at"] = utc_now()
    await handler.update(_TABLE, _template_pk(jiuwenclaw_id, tid), updates)


async def _sync_service_config_templates_records(
    handler: DBHandler,
    templates: list[dict[str, Any]],
    *,
    jiuwenclaw_id: str,
) -> dict[str, Any]:
    incoming_ids: set[str] = set()
    synced = 0
    for item in templates:
        if not isinstance(item, dict):
            raise ValueError("service_config_templates.sync templates must be objects")
        tid = _normalize_template_id(item.get("template_id"))
        incoming_ids.add(tid)
        await _upsert_service_config_template_from_sync(
            handler, item, jiuwenclaw_id=jiuwenclaw_id
        )
        synced += 1
    deleted = 0
    existing_rows = await handler.list_records(_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})
    for row in existing_rows:
        tid = str(getattr(row, "template_id", "") or "")
        if tid and tid not in incoming_ids:
            if await delete_service_config_template(handler, tid):
                deleted += 1
    return {"synced_count": synced, "deleted_count": deleted}


async def apply_service_config_template(
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """应用 Claw Manager 经 WebSocket 下发的 service_config_templates 变更。"""
    op = str(payload.get("op") or "").strip()
    if not op:
        raise ValueError("service_config_templates.op is required")

    jiuwenclaw_id = get_jiuwenclaw_id()
    if not jiuwenclaw_id:
        raise ValueError("jiuwenclaw_id is not set")
    handler = await ensure_db_handler()

    if op == "create":
        template = payload.get("template")
        if not isinstance(template, dict):
            raise ValueError("service_config_templates.create requires template object")
        await _upsert_service_config_template_from_sync(
            handler, template, jiuwenclaw_id=jiuwenclaw_id
        )
        result: dict[str, Any] | None = {
            "template_id": _normalize_template_id(template.get("template_id")),
        }

    elif op == "update":
        template_id = payload.get("template_id")
        updates = payload.get("updates")
        if template_id is None:
            raise ValueError("service_config_templates.update requires template_id")
        if not isinstance(updates, dict) or not updates:
            raise ValueError("service_config_templates.update requires non-empty updates")
        req = ServiceConfigTemplateUpdateRequest.model_validate(updates)
        tid = _normalize_template_id(template_id)
        existing = await _get_row_for_instance(handler, tid, jiuwenclaw_id)
        row = await update_service_config_template(
            handler, tid, req, existing=existing
        )
        if row is None:
            raise ValueError(f"service config template template_id={tid!r} not found")
        result = None

    elif op == "delete":
        template_id = payload.get("template_id")
        if template_id is None:
            raise ValueError("service_config_templates.delete requires template_id")
        tid = _normalize_template_id(template_id)
        await delete_service_config_template(handler, tid)
        result = None

    elif op == "sync":
        templates = payload.get("templates")
        if not isinstance(templates, list):
            raise ValueError("service_config_templates.sync requires templates array")
        result = await _sync_service_config_templates_records(
            handler, templates, jiuwenclaw_id=jiuwenclaw_id
        )

    else:
        raise ValueError(f"unsupported service_config_templates.op: {op!r}")

    logger.info(
        "[ManagerWsClient] service_config_templates sync op=%s template_id=%s",
        op,
        (result or {}).get("template_id")
        or payload.get("template_id")
        or (payload.get("template") or {}).get("template_id"),
    )
    return result
