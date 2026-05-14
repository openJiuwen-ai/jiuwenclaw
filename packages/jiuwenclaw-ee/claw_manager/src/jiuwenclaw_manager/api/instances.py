"""实例管理 API（路径与设计文档 4.1 对齐）。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from jiuwenclaw_manager.api.deps import get_db
from jiuwenclaw_manager.config import settings
from jiuwenclaw_manager.models.schemas import (
    ApiResponse,
    CreateInstanceBody,
    PatchInstanceDataBody,
    ProvisionLocalInstanceBody,
)
from jiuwenclaw_manager.services.instance_provisioner import provision_local_jiuwenclaw
from jiuwenclaw_manager.services.instance_service import InstanceService

router = APIRouter(prefix="/instances", tags=["instances"])


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


def _svc(session: AsyncSession) -> InstanceService:
    return InstanceService(session)


@router.post("/provision-local", response_model=ApiResponse)
async def provision_local_instance(
    body: ProvisionLocalInstanceBody,
    session: Annotated[AsyncSession, Depends(get_db)],
):
    try:
        data = await provision_local_jiuwenclaw(
            session,
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
    return ApiResponse(data=data)


@router.post("", response_model=ApiResponse)
async def create_instance(
    body: CreateInstanceBody,
    session: Annotated[AsyncSession, Depends(get_db)],
):
    svc = _svc(session)
    data = await svc.create(body)
    return ApiResponse(data=data)


@router.get("", response_model=ApiResponse)
async def list_instances(
    session: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    status: str | None = None,
):
    svc = _svc(session)
    data = await svc.list_instances(page=page, page_size=page_size, status=status)
    return ApiResponse(data=data)


@router.patch("/{jiuwenclaw_id}", response_model=ApiResponse)
async def patch_instance(
    jiuwenclaw_id: str,
    body: PatchInstanceDataBody,
    session: Annotated[AsyncSession, Depends(get_db)],
):
    svc = _svc(session)
    row = await svc.patch_instance_data(jiuwenclaw_id, body)
    if row is None:
        raise HTTPException(status_code=404, detail="instance not found")
    return ApiResponse(data=row.model_dump())


@router.get("/{jiuwenclaw_id}", response_model=ApiResponse)
async def get_instance(jiuwenclaw_id: str, session: Annotated[AsyncSession, Depends(get_db)]):
    svc = _svc(session)
    row = await svc.get(jiuwenclaw_id)
    if row is None:
        raise HTTPException(status_code=404, detail="instance not found")
    return ApiResponse(data=row.model_dump())


@router.delete("/{jiuwenclaw_id}", response_model=ApiResponse)
async def delete_instance(
    jiuwenclaw_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    force: bool = Query(False),
):
    _ = force  # 预留：后续对接 K8S 强制回收等
    svc = _svc(session)
    ok = await svc.delete(jiuwenclaw_id)
    if not ok:
        raise HTTPException(status_code=404, detail="instance not found")
    return ApiResponse(data={"deleted": True})


@router.get("/{jiuwenclaw_id}/services/status", response_model=ApiResponse)
async def services_status(jiuwenclaw_id: str, session: Annotated[AsyncSession, Depends(get_db)]):
    svc = _svc(session)
    data = await svc.services_status(jiuwenclaw_id)
    if data is None:
        raise HTTPException(status_code=404, detail="instance not found")
    return ApiResponse(data=data.model_dump())


@router.post("/{jiuwenclaw_id}/events/heartbeat", response_model=ApiResponse)
async def ingest_heartbeat(
    jiuwenclaw_id: str,
    body: HeartbeatIngestBody,
    session: Annotated[AsyncSession, Depends(get_db)],
):
    svc = _svc(session)
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
    return ApiResponse(data={"accepted": True})
