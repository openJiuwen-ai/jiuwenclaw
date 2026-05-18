"""模型模板 model_template CRUD API（按组网实例路径隔离）。"""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.routers.deps import get_db_handler, get_http_client
from jiuwenclaw_manager.models.schemas import (
    ApiResponse,
    ModelTemplateCreateBody,
    ModelTemplateUpdateBody,
)
from jiuwenclaw_manager.core.template import ModelTemplateService

router = APIRouter()


def _svc(handler: DBHandler, client: httpx.AsyncClient) -> ModelTemplateService:
    return ModelTemplateService(handler, client)


@router.post("/{jiuwenclaw_id}/model-templates", response_model=ApiResponse)
async def create_model_template(
    jiuwenclaw_id: str,
    body: ModelTemplateCreateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
):
    svc = _svc(handler, client)
    try:
        data = await svc.create(jiuwenclaw_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse(data=data.model_dump())


@router.get("/{jiuwenclaw_id}/model-templates", response_model=ApiResponse)
async def list_model_templates(
    jiuwenclaw_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    enabled: bool | None = None,
    model_type: str | None = Query(
        default=None,
        description="按模型类型筛选，如 default / video / audio / vision",
    ),
):
    svc = _svc(handler, client)
    try:
        data = await svc.list_templates(
            jiuwenclaw_id,
            page=page,
            page_size=page_size,
            enabled=enabled,
            model_type=model_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse(data=data)


@router.get("/{jiuwenclaw_id}/model-templates/{template_id}", response_model=ApiResponse)
async def get_model_template(
    jiuwenclaw_id: str,
    template_id: int,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
):
    svc = _svc(handler, client)
    try:
        row = await svc.get(jiuwenclaw_id, template_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="model template not found")
    return ApiResponse(data=row.model_dump())


@router.put("/{jiuwenclaw_id}/model-templates/{template_id}", response_model=ApiResponse)
async def update_model_template(
    jiuwenclaw_id: str,
    template_id: int,
    body: ModelTemplateUpdateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
):
    svc = _svc(handler, client)
    try:
        row = await svc.update(jiuwenclaw_id, template_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="model template not found")
    return ApiResponse(data=row.model_dump())


@router.delete("/{jiuwenclaw_id}/model-templates/{template_id}", response_model=ApiResponse)
async def delete_model_template(
    jiuwenclaw_id: str,
    template_id: int,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
):
    svc = _svc(handler, client)
    try:
        ok = await svc.delete(jiuwenclaw_id, template_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="model template not found")
    return ApiResponse(data={"deleted": True, "id": template_id})
