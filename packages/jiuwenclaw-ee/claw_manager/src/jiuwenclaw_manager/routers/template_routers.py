"""模板 CRUD API：model_template、extension_config_template、skill_whitelist_template（全局模板，变更同步至所有已连接 Gateway）。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.core.template.extension_config_template import (
    ExtensionConfigTemplateService,
)
from jiuwenclaw_manager.core.template.model_template import ModelTemplateService
from jiuwenclaw_manager.core.template.skill_whitelist_template import (
    SkillWhitelistTemplateService,
)
from jiuwenclaw_manager.infrastructure.db import get_db_handler
from jiuwenclaw_manager.schemas.common_schemas import ResponseModel
from jiuwenclaw_manager.schemas.template_schemas import (
    ExtensionConfigTemplateCreateBody,
    ExtensionConfigTemplateListQuery,
    ExtensionConfigTemplateUpdateBody,
    ModelTemplateCreateBody,
    ModelTemplateUpdateBody,
    SkillWhitelistTemplateCreateBody,
    SkillWhitelistTemplateListQuery,
    SkillWhitelistTemplateUpdateBody,
)

templates_router = APIRouter()


def _model_template_svc(handler: DBHandler) -> ModelTemplateService:
    return ModelTemplateService(handler)


def _extension_config_template_svc(handler: DBHandler) -> ExtensionConfigTemplateService:
    return ExtensionConfigTemplateService(handler)


def _skill_whitelist_template_svc(handler: DBHandler) -> SkillWhitelistTemplateService:
    return SkillWhitelistTemplateService(handler)


# --- model_template ---


@templates_router.post("/model-templates", response_model=ResponseModel)
async def create_model_template(
    body: ModelTemplateCreateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _model_template_svc(handler)
    try:
        data = await svc.create(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data.model_dump())


@templates_router.get("/model-templates", response_model=ResponseModel)
async def list_model_templates(
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    enabled: bool | None = None,
    model_type: str | None = Query(
        default=None,
        description="按模型类型筛选，如 default / video / audio / vision",
    ),
):
    svc = _model_template_svc(handler)
    try:
        data = await svc.list_templates(
            page=page,
            page_size=page_size,
            enabled=enabled,
            model_type=model_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@templates_router.get("/model-templates/{template_id}", response_model=ResponseModel)
async def get_model_template(
    template_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _model_template_svc(handler)
    try:
        row = await svc.get(template_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="model template not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@templates_router.put("/model-templates/{template_id}", response_model=ResponseModel)
async def update_model_template(
    template_id: str,
    body: ModelTemplateUpdateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _model_template_svc(handler)
    try:
        row = await svc.update(template_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="model template not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@templates_router.delete("/model-templates/{template_id}", response_model=ResponseModel)
async def delete_model_template(
    template_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _model_template_svc(handler)
    try:
        ok = await svc.delete(template_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="model template not found")
    return ResponseModel(
        code=200, message="success", data={"deleted": True, "template_id": template_id}
    )


# --- extension_config_template ---


@templates_router.post("/extension-config-templates", response_model=ResponseModel)
async def create_extension_config_template(
    body: ExtensionConfigTemplateCreateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _extension_config_template_svc(handler)
    try:
        data = await svc.create(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data.model_dump())


@templates_router.get("/extension-config-templates", response_model=ResponseModel)
async def list_extension_config_templates(
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    query: Annotated[ExtensionConfigTemplateListQuery, Query()],
):
    svc = _extension_config_template_svc(handler)
    try:
        data = await svc.list_templates(
            page=query.page,
            page_size=query.page_size,
            enabled=query.enabled,
            component=query.component,
            hook_type=query.hook_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@templates_router.get(
    "/extension-config-templates/{template_id}", response_model=ResponseModel
)
async def get_extension_config_template(
    template_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _extension_config_template_svc(handler)
    try:
        row = await svc.get(template_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="extension config template not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@templates_router.put(
    "/extension-config-templates/{template_id}", response_model=ResponseModel
)
async def update_extension_config_template(
    template_id: str,
    body: ExtensionConfigTemplateUpdateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _extension_config_template_svc(handler)
    try:
        row = await svc.update(template_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="extension config template not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@templates_router.delete(
    "/extension-config-templates/{template_id}", response_model=ResponseModel
)
async def delete_extension_config_template(
    template_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _extension_config_template_svc(handler)
    try:
        ok = await svc.delete(template_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="extension config template not found")
    return ResponseModel(
        code=200, message="success", data={"deleted": True, "template_id": template_id}
    )


# --- skill_whitelist_template ---


@templates_router.post("/skill-whitelist-templates", response_model=ResponseModel)
async def create_skill_whitelist_template(
    body: SkillWhitelistTemplateCreateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _skill_whitelist_template_svc(handler)
    try:
        data = await svc.create(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data.model_dump())


@templates_router.get("/skill-whitelist-templates", response_model=ResponseModel)
async def list_skill_whitelist_templates(
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    query: Annotated[SkillWhitelistTemplateListQuery, Query()],
):
    svc = _skill_whitelist_template_svc(handler)
    try:
        data = await svc.list_templates(
            page=query.page,
            page_size=query.page_size,
            enabled=query.enabled,
            skill_id=query.skill_id,
            skill_source=query.skill_source,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@templates_router.get(
    "/skill-whitelist-templates/{template_id}", response_model=ResponseModel
)
async def get_skill_whitelist_template(
    template_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _skill_whitelist_template_svc(handler)
    try:
        row = await svc.get(template_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="skill whitelist template not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@templates_router.put(
    "/skill-whitelist-templates/{template_id}", response_model=ResponseModel
)
async def update_skill_whitelist_template(
    template_id: str,
    body: SkillWhitelistTemplateUpdateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _skill_whitelist_template_svc(handler)
    try:
        row = await svc.update(template_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="skill whitelist template not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@templates_router.delete(
    "/skill-whitelist-templates/{template_id}", response_model=ResponseModel
)
async def delete_skill_whitelist_template(
    template_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _skill_whitelist_template_svc(handler)
    try:
        ok = await svc.delete(template_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="skill whitelist template not found")
    return ResponseModel(
        code=200, message="success", data={"deleted": True, "template_id": template_id}
    )
