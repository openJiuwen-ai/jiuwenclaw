"""实例管理 API（路径与设计文档 4.1 对齐）。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.infrastructure.db import get_db_handler
from jiuwenclaw_manager.infrastructure.config import settings
from jiuwenclaw_manager.manager_ws_server.pod_status_cache import (
    get_pod_status_snapshot,
)
from jiuwenclaw_manager.schemas.common_schemas import ResponseModel
from jiuwenclaw_manager.schemas.instance_schemas import (
    CreateInstanceBody,
    InstanceListQuery,
    InstanceUpdateBody,
    ProvisionLocalInstanceBody,
)
from jiuwenclaw_manager.core.instance import InstanceService, provision_local_jiuwenclaw

instance_router = APIRouter()


def _svc(handler: DBHandler) -> InstanceService:
    return InstanceService(handler)


def _request_volume_value(bv: dict, key: str, legacy_key: str | None = None) -> int:
    value = bv.get(key)
    if value is None and legacy_key is not None:
        value = bv.get(legacy_key)
    return int(value or 0)


def _normalize_request_volume(bv: dict) -> dict:
    return {
        "gateway_queued": _request_volume_value(bv, "gateway_queued"),
        "gateway_running": _request_volume_value(bv, "gateway_running"),
        "service_manager_queued": _request_volume_value(
            bv, "service_manager_queued", "sm_queued"
        ),
        "service_manager_routing": _request_volume_value(
            bv, "service_manager_routing", "sm_routing"
        ),
        "service_manager_running": _request_volume_value(
            bv, "service_manager_running", "sm_running"
        ),
        "requests_started_total": _request_volume_value(bv, "requests_started_total"),
        "requests_finished_total": _request_volume_value(bv, "requests_finished_total"),
        "pods_in_use": _request_volume_value(bv, "pods_in_use"),
        "pods_idle": _request_volume_value(bv, "pods_idle"),
    }


def _build_request_volume_summary(bv: dict) -> dict:
    queued_requests = _request_volume_value(
        bv, "gateway_queued"
    ) + _request_volume_value(
        bv, "service_manager_queued", "sm_queued"
    )
    running_requests = _request_volume_value(
        bv, "service_manager_running", "sm_running"
    ) or _request_volume_value(
        bv, "gateway_running"
    )
    return {
        "queued_requests": queued_requests,
        "running_requests": running_requests,
        "finished_requests": _request_volume_value(bv, "requests_finished_total"),
        "active_pods": _request_volume_value(bv, "pods_in_use"),
        "idle_pods": _request_volume_value(bv, "pods_idle"),
    }


@instance_router.post("/provision-local", response_model=ResponseModel)
async def provision_local_instance(
    body: ProvisionLocalInstanceBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    try:
        data = await provision_local_jiuwenclaw(
            handler,
            settings,
            jiuwenclaw_name=body.jiuwenclaw_name,
            creator_id=body.creator_id,
            description=body.description,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@instance_router.post("/", response_model=ResponseModel)
async def create_instance(
    body: CreateInstanceBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _svc(handler)
    data = await svc.create(body)
    return ResponseModel(code=200, message="success", data=data)


@instance_router.get("/", response_model=ResponseModel)
async def list_instances(
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    query: Annotated[InstanceListQuery, Query()],
):
    svc = _svc(handler)
    data = await svc.list_instances(query)
    return ResponseModel(code=200, message="success", data=data)


@instance_router.patch("/{jiuwenclaw_id}", response_model=ResponseModel)
async def update_instance(
    jiuwenclaw_id: str,
    body: InstanceUpdateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _svc(handler)
    try:
        row = await svc.update(jiuwenclaw_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="instance not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@instance_router.get("/{jiuwenclaw_id}", response_model=ResponseModel)
async def get_instance(
    jiuwenclaw_id: str, handler: Annotated[DBHandler, Depends(get_db_handler)]
):
    svc = _svc(handler)
    row = await svc.get(jiuwenclaw_id)
    if row is None:
        raise HTTPException(status_code=404, detail="instance not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@instance_router.delete("/{jiuwenclaw_id}", response_model=ResponseModel)
async def delete_instance(
    jiuwenclaw_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    force: bool = Query(False),
):
    _ = force  # 预留：后续对接 K8S 强制回收等
    svc = _svc(handler)
    ok = await svc.delete(jiuwenclaw_id)
    if not ok:
        raise HTTPException(status_code=404, detail="instance not found")
    return ResponseModel(code=200, message="success", data={"deleted": True})


@instance_router.get("/{jiuwenclaw_id}/pods", response_model=ResponseModel)
async def get_instance_pods(
    jiuwenclaw_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    include_metrics: bool = Query(False, description="是否包含 CPU/Memory 使用率"),
):
    """获取指定 jiuwenclaw 实例的所有 Pod 状态。

    Args:
        jiuwenclaw_id: jiuwenclaw 实例 ID（即 gateway_id）
        include_metrics: 是否包含 CPU/Memory 使用率

    Returns:
        {
            "code": 200,
            "message": "success",
            "data": {
                "total": 5,
                "running": 4,
                "failed": 1,
                "pods": [...]
            }
        }
    """
    # 1. 验证 jiuwenclaw_id 是否存在
    svc = _svc(handler)
    instance = await svc.get(jiuwenclaw_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="instance not found")

    # 2. 读取 Gateway 通过 Manager WebSocket 上报的最近一次 Pod 状态快照
    pod_data = get_pod_status_snapshot(jiuwenclaw_id)
    if pod_data is None:
        pod_data = {
            "source": "no_snapshot",
            "stale": True,
            "snapshot_age_seconds": None,
            "jiuwenclaw_id": jiuwenclaw_id,
            "namespace": getattr(instance, "k8s_namespace", None) or settings.k8s_namespace,
            "total": 0,
            "running": 0,
            "failed": 0,
            "pods": [],
        }
    pod_data["include_metrics_requested"] = include_metrics
    return ResponseModel(code=200, message="success", data=pod_data)


@instance_router.get("/{jiuwenclaw_id}/request-volume", response_model=ResponseModel)
async def get_instance_request_volume(
    jiuwenclaw_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    """获取指定 Gateway 实例的业务量统计（排队中 / 运行中消息数）。

    数据来源：Gateway 周期性通过 pod_status.report 帧上报的快照（含 request_volume 字段）。
    stale=true 表示快照已超过 90 秒未更新。
    """
    svc = _svc(handler)
    instance = await svc.get(jiuwenclaw_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="instance not found")

    snapshot = get_pod_status_snapshot(jiuwenclaw_id)
    if snapshot is None:
        return ResponseModel(
            code=200,
            message="success",
            data={
                "jiuwenclaw_id": jiuwenclaw_id,
                "snapshot_time": None,
                "stale": True,
                "request_volume": None,
                "summary": None,
            },
        )

    bv = snapshot.get("request_volume")
    summary = None
    if isinstance(bv, dict):
        bv = _normalize_request_volume(bv)
        summary = _build_request_volume_summary(bv)

    return ResponseModel(
        code=200,
        message="success",
        data={
            "jiuwenclaw_id": jiuwenclaw_id,
            "snapshot_time": snapshot.get("snapshot_time"),
            "snapshot_age_seconds": snapshot.get("snapshot_age_seconds"),
            "stale": snapshot.get("stale", True),
            "request_volume": bv,
            "summary": summary,
        },
    )

