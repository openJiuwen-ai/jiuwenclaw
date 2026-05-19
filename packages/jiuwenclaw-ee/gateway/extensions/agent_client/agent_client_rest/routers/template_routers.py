from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from ..core.template.model_template import (
    create_model_template,
    delete_model_template,
    get_model_template,
    list_model_templates,
    update_model_template,
)
from ..schemas import ModelTemplateCreateRequest, ModelTemplateUpdateRequest, ResponseModel

template_router = APIRouter()


@template_router.post("/model-templates")
async def create_model_template_route(
    request: ModelTemplateCreateRequest,
    req: Request,
) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    try:
        row = await create_model_template(handler, request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=row)


@template_router.get("/model-templates")
async def list_model_templates_route(
    req: Request,
    model_type: str | None = Query(default=None),
    enabled: bool | None = Query(default=None),
    page_size: int = Query(default=20, ge=1, le=200),
    page_num: int = Query(default=1, ge=1),
) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    try:
        data = await list_model_templates(
            handler,
            page_num=page_num,
            page_size=page_size,
            enabled=enabled,
            model_type=model_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@template_router.get("/model-templates/{template_id}")
async def get_model_template_route(
    template_id: int,
    req: Request,
) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    try:
        row = await get_model_template(handler, template_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="model template not found")
    return ResponseModel(code=200, message="success", data=row)


@template_router.put("/model-templates/{template_id}")
async def update_model_template_route(
    template_id: int,
    request: ModelTemplateUpdateRequest,
    req: Request,
) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    try:
        row = await update_model_template(handler, template_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="model template not found")
    return ResponseModel(code=200, message="success", data=row)


@template_router.delete("/model-templates/{template_id}")
async def delete_model_template_route(
    template_id: int,
    req: Request,
) -> ResponseModel[None]:
    handler = req.app.state.db_handler
    try:
        deleted = await delete_model_template(handler, template_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="model template not found")
    return ResponseModel(code=200, message="success")
