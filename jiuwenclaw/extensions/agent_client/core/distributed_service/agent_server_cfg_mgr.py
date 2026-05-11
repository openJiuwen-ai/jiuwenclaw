# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Agent Server 实例配置（instance_config）持久化：基于 ``DBHandler`` 异步读写。

应用启动时由 ``init_all_tables`` 完成 ``init_table(INSTANCE_CONFIG_TABLE_DEF)``。
"""

from __future__ import annotations

from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw.extensions.agent_client.core.utils import format_ts, utc_now
from jiuwenclaw.extensions.agent_client.models.distributed_service_models import (
    INSTANCE_CONFIG_TABLE_DEF,
)
from jiuwenclaw.extensions.agent_client.schemas.distributed_service_schemas import (
    AgentServerConfigUpdateRequest,
)

AGENT_SERVER_COMPONENT = "agent_server"


def validate_agent_server_config_update(req: AgentServerConfigUpdateRequest) -> None:
    """校验本次请求体中出现的字段是否合理"""
    if req.min_replicas is not None and req.min_replicas < 1:
        raise ValueError("min_replicas must be >= 1")
    if req.max_replicas is not None and req.max_replicas < 1:
        raise ValueError("max_replicas must be >= 1")
    if (
        req.min_replicas is not None
        and req.max_replicas is not None
        and req.max_replicas < req.min_replicas
    ):
        raise ValueError("max_replicas must be >= min_replicas")

    if req.autoscale_metrics is None:
        return
    metrics = req.autoscale_metrics
    if not isinstance(metrics, dict):
        raise ValueError("autoscale_metrics must be an object")
    max_concurrency = metrics.get("max_concurrency")
    if max_concurrency is not None and int(max_concurrency) < 1:
        raise ValueError("autoscale_metrics.max_concurrency must be >= 1")
    cpu_target = metrics.get("cpu_target")
    if cpu_target is not None and not (1 <= int(cpu_target) <= 100):
        raise ValueError("autoscale_metrics.cpu_target must be in [1, 100]")
    memory_target = metrics.get("memory_target")
    if memory_target is not None and not (1 <= int(memory_target) <= 100):
        raise ValueError("autoscale_metrics.memory_target must be in [1, 100]")


def _instance_row_to_api_dict(obj: Any) -> dict[str, Any]:
    metrics = getattr(obj, "autoscale_metrics", None)
    if not isinstance(metrics, dict):
        metrics = {}
    return {
        "id": getattr(obj, "id"),
        "component": getattr(obj, "component"),
        "min_replicas": int(getattr(obj, "min_replicas")),
        "max_replicas": int(getattr(obj, "max_replicas")),
        "current_replicas": int(getattr(obj, "current_replicas")),
        "autoscale_enabled": bool(getattr(obj, "autoscale_enabled")),
        "autoscale_metrics": metrics,
        "data": {},
        "created_at": format_ts(getattr(obj, "created_at", None)),
        "updated_at": format_ts(getattr(obj, "updated_at", None)),
    }


async def upsert_agent_server_instance_config(
    handler: DBHandler,
    request: AgentServerConfigUpdateRequest,
) -> dict[str, Any]:
    """插入或更新 ``component=agent_server`` 的实例配置；返回完整 API 字典（与 GET 一致）。

    已存在行时仅更新请求体中显式给出的字段（不补默认、不刷写未出现的列）。
    """
    table = INSTANCE_CONFIG_TABLE_DEF.table_name
    existing = await handler.get(table, {"component": AGENT_SERVER_COMPONENT})
    validate_agent_server_config_update(request)

    now = utc_now()

    if existing is None:
        metrics = dict(request.autoscale_metrics) if isinstance(request.autoscale_metrics, dict) else {}

        min_replicas = request.min_replicas if request.min_replicas is not None else 1
        max_replicas = request.max_replicas if request.max_replicas is not None else 3
        row_data: dict[str, Any] = {
            "component": AGENT_SERVER_COMPONENT,
            "min_replicas": min_replicas,
            "max_replicas": max_replicas,
            "current_replicas": min_replicas,
            "autoscale_enabled": (
                request.autoscale_enabled if request.autoscale_enabled is not None else False
            ),
            "autoscale_metrics": metrics,
            "data": {},
            "created_at": now,
            "updated_at": now,
        }
        record = await handler.create(table, row_data)
        return _instance_row_to_api_dict(record)

    patch: dict[str, Any] = {}
    if request.min_replicas is not None:
        patch["min_replicas"] = request.min_replicas
    if request.max_replicas is not None:
        patch["max_replicas"] = request.max_replicas
    if request.autoscale_enabled is not None:
        patch["autoscale_enabled"] = request.autoscale_enabled
    if request.autoscale_metrics is not None:
        patch["autoscale_metrics"] = dict(request.autoscale_metrics)

    if not patch:
        return _instance_row_to_api_dict(existing)

    patch["updated_at"] = now
    updated = await handler.update(table, {"component": AGENT_SERVER_COMPONENT}, patch)
    if updated is None:
        raise RuntimeError("instance_config update returned no row")
    return _instance_row_to_api_dict(updated)


async def get_agent_server_instance_config(handler: DBHandler) -> dict[str, Any]:
    """读取 ``agent_server`` 实例配置；无记录时返回空字典 ``{}``。"""
    table = INSTANCE_CONFIG_TABLE_DEF.table_name
    existing = await handler.get(table, {"component": AGENT_SERVER_COMPONENT})
    if existing is None:
        return {}
    return _instance_row_to_api_dict(existing)
