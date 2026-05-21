"""模型模板 model_template CRUD API（按组网实例路径隔离）。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.infrastructure.db import get_db_handler
from jiuwenclaw_manager.schemas.common_schemas import ResponseModel
from jiuwenclaw_manager.schemas.template_schemas import (
    ModelTemplateCreateBody,
    ModelTemplateUpdateBody,
)
from jiuwenclaw_manager.core.template.model_template import ModelTemplateService
from jiuwenclaw_manager.manager_ws_server import ManagerWsServer

templates_router = APIRouter()


def _ws_server_from_request(request: Request) -> ManagerWsServer | None:
    return getattr(request.app.state, "manager_ws_server", None)


def _svc(handler: DBHandler) -> ModelTemplateService:
    return ModelTemplateService(handler)


@templates_router.post("/{jiuwenclaw_id}/model-templates", response_model=ResponseModel)
async def create_model_template(
    jiuwenclaw_id: str,
    body: ModelTemplateCreateBody,
    request: Request,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _svc(handler)
    try:
        data = await svc.create(
            jiuwenclaw_id, body, ws_server=_ws_server_from_request(request)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data.model_dump())


@templates_router.get("/{jiuwenclaw_id}/model-templates", response_model=ResponseModel)
async def list_model_templates(
    jiuwenclaw_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    enabled: bool | None = None,
    model_type: str | None = Query(
        default=None,
        description="按模型类型筛选，如 default / video / audio / vision",
    ),
):
    svc = _svc(handler)
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
    return ResponseModel(code=200, message="success", data=data)


@templates_router.get("/{jiuwenclaw_id}/model-templates/{template_id}", response_model=ResponseModel)
async def get_model_template(
    jiuwenclaw_id: str,
    template_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _svc(handler)
    try:
        row = await svc.get(jiuwenclaw_id, template_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="model template not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@templates_router.put("/{jiuwenclaw_id}/model-templates/{template_id}", response_model=ResponseModel)
async def update_model_template(
    jiuwenclaw_id: str,
    template_id: str,
    body: ModelTemplateUpdateBody,
    request: Request,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _svc(handler)
    try:
        row = await svc.update(
            jiuwenclaw_id,
            template_id,
            body,
            ws_server=_ws_server_from_request(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="model template not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@templates_router.delete("/{jiuwenclaw_id}/model-templates/{template_id}", response_model=ResponseModel)
async def delete_model_template(
    jiuwenclaw_id: str,
    template_id: str,
    request: Request,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _svc(handler)
    try:
        ok = await svc.delete(
            jiuwenclaw_id, template_id, ws_server=_ws_server_from_request(request)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="model template not found")
    return ResponseModel(
        code=200, message="success", data={"deleted": True, "template_id": template_id}
    )
