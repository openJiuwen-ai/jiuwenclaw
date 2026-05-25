# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""服务配置模板 WebSocket 同步：将 Claw Manager 下发的 service_config_templates 写入 Gateway 本地库。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from ...infrastructure.utils import assert_jiuwenclaw_id_matches_payload, utc_now
from ...models.template_models import SERVICE_CONFIG_TEMPLATE_TABLE_DEF
from ...schemas.template_schemas import ServiceConfigTemplateUpdateRequest

_TABLE = SERVICE_CONFIG_TEMPLATE_TABLE_DEF.table_name
_ALLOWED_IMAGE_PULL_POLICIES = frozenset({"Always", "IfNotPresent", "Never"})


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


def _autoscale_interval_to_db(value: float) -> str:
    return str(value)


def _autoscale_interval_from_db(value: Any) -> float:
    if value is None:
        return 0.2
    return float(value)


async def _get_row(handler: DBHandler, template_id: str) -> Any | None:
    tid = _normalize_template_id(template_id)
    return await handler.get(_TABLE, {"template_id": tid})


async def update_service_config_template(
    handler: DBHandler,
    template_id: str,
    request: ServiceConfigTemplateUpdateRequest,
    *,
    existing: Any | None = None,
) -> dict[str, Any] | None:
    tid = _normalize_template_id(template_id)
    row = existing if existing is not None else await _get_row(handler, tid)
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
        "cpu_request",
        "memory_request",
        "cpu_limit",
        "memory_limit",
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

    if not updates:
        raise ValueError("请求未包含任何可更新的业务字段")

    updates["updated_at"] = utc_now()
    updated = await handler.update(_TABLE, {"template_id": tid}, updates)
    if updated is None:
        return None
    return {"template_id": str(getattr(updated, "template_id", tid))}


async def delete_service_config_template(handler: DBHandler, template_id: str) -> bool:
    tid = _normalize_template_id(template_id)
    existing = await _get_row(handler, tid)
    if existing is None:
        return False
    return await handler.delete(_TABLE, {"template_id": tid})


def _parse_iso_datetime(value: Any) -> Any:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        return datetime.fromisoformat(text)
    return value


def _build_row_from_template(template: dict[str, Any], *, now: datetime) -> dict[str, Any]:
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
        "readiness_initial_delay": int(template.get("readiness_initial_delay", 5)),
        "readiness_period": int(template.get("readiness_period", 10)),
        "ready_timeout": int(template.get("ready_timeout", 300)),
        "ready_poll_interval": int(template.get("ready_poll_interval", 2)),
        "nfs_server": (
            str(template["nfs_server"]).strip() if template.get("nfs_server") else None
        ),
        "nfs_path": str(template.get("nfs_path", "/")).strip(),
        "nfs_mount_path": (
            str(template["nfs_mount_path"]).strip()
            if template.get("nfs_mount_path")
            else None
        ),
        "cpu_request": str(template.get("cpu_request", "500m")).strip(),
        "memory_request": str(template.get("memory_request", "512Mi")).strip(),
        "cpu_limit": str(template.get("cpu_limit", "1000m")).strip(),
        "memory_limit": str(template.get("memory_limit", "1Gi")).strip(),
        "min_idle_services": int(template.get("min_idle_services", 1)),
        "max_services": int(template.get("max_services", 10)),
        "service_concurrency": int(template.get("service_concurrency", 10)),
        "service_ttl": int(template.get("service_ttl", 30)),
        "autoscale_interval": _autoscale_interval_to_db(
            _autoscale_interval_from_db(template.get("autoscale_interval", "0.2"))
        ),
        "message_timeout": int(template.get("message_timeout", 300)),
        "session_concurrency": int(template.get("session_concurrency", 10)),
        "session_ttl": int(template.get("session_ttl", 20)),
        "enabled": bool(template.get("enabled", True)),
        "data": template.get("data"),
        "created_at": _parse_iso_datetime(template.get("created_at")) or now,
        "updated_at": _parse_iso_datetime(template.get("updated_at")) or now,
    }


async def apply_service_config_template_sync(
    handler: DBHandler,
    op: str,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """应用 Claw Manager 经 WebSocket 下发的 service_config_templates 变更。"""
    assert_jiuwenclaw_id_matches_payload(payload)

    if op == "create":
        template = payload.get("template")
        if not isinstance(template, dict):
            raise ValueError("service_config_templates.create requires template object")
        now = utc_now()
        row_data = _build_row_from_template(template, now=now)
        await handler.create(_TABLE, row_data)
        return {"template_id": row_data["template_id"]}

    if op == "update":
        template_id = payload.get("template_id")
        updates = payload.get("updates")
        if template_id is None:
            raise ValueError("service_config_templates.update requires template_id")
        if not isinstance(updates, dict) or not updates:
            raise ValueError("service_config_templates.update requires non-empty updates")
        req = ServiceConfigTemplateUpdateRequest.model_validate(updates)
        tid = _normalize_template_id(template_id)
        existing = await _get_row(handler, tid)
        row = await update_service_config_template(
            handler, tid, req, existing=existing
        )
        if row is None:
            raise ValueError(f"service config template template_id={tid!r} not found")
        return None

    if op == "delete":
        template_id = payload.get("template_id")
        if template_id is None:
            raise ValueError("service_config_templates.delete requires template_id")
        tid = _normalize_template_id(template_id)
        deleted = await delete_service_config_template(handler, tid)
        if not deleted:
            raise ValueError(f"service config template template_id={tid!r} not found")
        return None

    raise ValueError(f"unsupported service_config_templates.op: {op!r}")
