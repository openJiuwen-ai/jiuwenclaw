"""实例管理 API（路径与设计文档 4.1 对齐）。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.infrastructure.db import get_db_handler
from jiuwenclaw_manager.infrastructure.config import settings
from jiuwenclaw_manager.schemas.common_schemas import ResponseModel
from jiuwenclaw_manager.schemas.instance_schemas import (
    CreateInstanceBody,
    PatchInstanceDataBody,
    ProvisionLocalInstanceBody,
)
from jiuwenclaw_manager.core.instance import InstanceService, provision_local_jiuwenclaw

instance_router = APIRouter()


class HeartbeatIngestBody(BaseModel):
    """与 RabbitMQ 消息体字段对齐；REST 入口便于联调，生产可由 consumer 调用同一服务层。"""

    service_id: str
    service_type: str
    component_role: str
    manager_id: str
    endpoint: str | None = None
    version: str | None = None
    capabilities: dict[str, Any] | None = None
    data: dict[str, Any] | None = None


def _svc(handler: DBHandler) -> InstanceService:
    return InstanceService(handler)


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


@instance_router.post("", response_model=ResponseModel)
async def create_instance(
    body: CreateInstanceBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _svc(handler)
    data = await svc.create(body)
    return ResponseModel(code=200, message="success", data=data)


@instance_router.get("", response_model=ResponseModel)
async def list_instances(
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    status: str | None = None,
):
    svc = _svc(handler)
    data = await svc.list_instances(page=page, page_size=page_size, status=status)
    return ResponseModel(code=200, message="success", data=data)


@instance_router.patch("/{jiuwenclaw_id}", response_model=ResponseModel)
async def patch_instance(
    jiuwenclaw_id: str,
    body: PatchInstanceDataBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _svc(handler)
    row = await svc.patch_instance_data(jiuwenclaw_id, body)
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


@instance_router.get("/{jiuwenclaw_id}/services/status", response_model=ResponseModel)
async def services_status(
    jiuwenclaw_id: str, handler: Annotated[DBHandler, Depends(get_db_handler)]
):
    svc = _svc(handler)
    data = await svc.services_status(jiuwenclaw_id)
    if data is None:
        raise HTTPException(status_code=404, detail="instance not found")
    return ResponseModel(code=200, message="success", data=data.model_dump())


@instance_router.post("/{jiuwenclaw_id}/events/heartbeat", response_model=ResponseModel)
async def ingest_heartbeat(
    jiuwenclaw_id: str,
    body: HeartbeatIngestBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _svc(handler)
    ok = await svc.apply_heartbeat(
        jiuwenclaw_id=jiuwenclaw_id,
        service_id=body.service_id,
        service_type=body.service_type,
        component_role=body.component_role,
        manager_id=body.manager_id,
        endpoint=body.endpoint,
        version=body.version,
        capabilities=body.capabilities,
        extra=body.data,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="instance not found")
    return ResponseModel(code=200, message="success", data={"accepted": True})
