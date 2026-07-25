"""配置生效策略与默认模板映射 CRUD API（按组网实例路径隔离）。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.infrastructure.db import get_db_handler
from jiuwenclaw_manager.schemas.common_schemas import ResponseModel
from jiuwenclaw_manager.schemas.config_effective_policy_schemas import (
    ConfigDefaultTemplateMappingCreateBody,
    ConfigDefaultTemplateMappingListQuery,
    ConfigDefaultTemplateMappingUpdateBody,
    ConfigEffectiveAgentPolicyCreateBody,
    ConfigEffectiveAgentPolicyListQuery,
    ConfigEffectiveAgentPolicyUpdateBody,
    ConfigEffectiveGlobalPolicyCreateBody,
    ConfigEffectiveGlobalPolicyListQuery,
    ConfigEffectiveGlobalPolicyUpdateBody,
    ConfigEffectiveServicePolicyCreateBody,
    ConfigEffectiveServicePolicyListQuery,
    ConfigEffectiveServicePolicyUpdateBody,
)
from jiuwenclaw_manager.core.config_effective_policy import (
    ConfigDefaultTemplateMappingService,
    ConfigEffectiveAgentPolicyService,
    ConfigEffectiveGlobalPolicyService,
    ConfigEffectiveServicePolicyService,
)

# --- 默认模板映射 ---

mapping_router = APIRouter(
    prefix="/{jiuwenclaw_id}/config-default-template-mappings",
)


def _mapping_svc(handler: DBHandler) -> ConfigDefaultTemplateMappingService:
    return ConfigDefaultTemplateMappingService(handler)


@mapping_router.post("/", response_model=ResponseModel)
async def create_template_mapping(
    jiuwenclaw_id: str,
    body: ConfigDefaultTemplateMappingCreateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _mapping_svc(handler)
    try:
        data = await svc.create(jiuwenclaw_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data.model_dump())


@mapping_router.get("/", response_model=ResponseModel)
async def list_template_mappings(
    jiuwenclaw_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    query: Annotated[ConfigDefaultTemplateMappingListQuery, Query()],
):
    svc = _mapping_svc(handler)
    try:
        data = await svc.list_mappings(jiuwenclaw_id, query)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@mapping_router.get("/{row_id}", response_model=ResponseModel)
async def get_template_mapping(
    jiuwenclaw_id: str,
    row_id: int,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _mapping_svc(handler)
    try:
        row = await svc.get(jiuwenclaw_id, row_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="template mapping not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@mapping_router.patch("/{row_id}", response_model=ResponseModel)
async def update_template_mapping(
    jiuwenclaw_id: str,
    row_id: int,
    body: ConfigDefaultTemplateMappingUpdateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _mapping_svc(handler)
    try:
        row = await svc.update(jiuwenclaw_id, row_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="template mapping not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@mapping_router.delete("/{row_id}", response_model=ResponseModel)
async def delete_template_mapping(
    jiuwenclaw_id: str,
    row_id: int,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _mapping_svc(handler)
    try:
        ok = await svc.delete(jiuwenclaw_id, row_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="template mapping not found")
    return ResponseModel(
        code=200, message="success", data={"deleted": True, "id": row_id}
    )

# --- Global 兜底 ---

global_router = APIRouter(
    prefix="/{jiuwenclaw_id}/config-effective/global-policies",
)


def _global_svc(handler: DBHandler) -> ConfigEffectiveGlobalPolicyService:
    return ConfigEffectiveGlobalPolicyService(handler)


@global_router.post("/", response_model=ResponseModel)
async def create_global_policy(
    jiuwenclaw_id: str,
    body: ConfigEffectiveGlobalPolicyCreateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _global_svc(handler)
    try:
        data = await svc.create(jiuwenclaw_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data.model_dump())


@global_router.get("/", response_model=ResponseModel)
async def list_global_policies(
    jiuwenclaw_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    query: Annotated[ConfigEffectiveGlobalPolicyListQuery, Query()],
):
    svc = _global_svc(handler)
    try:
        data = await svc.list_policies(jiuwenclaw_id, query)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@global_router.get("/{row_id}", response_model=ResponseModel)
async def get_global_policy(
    jiuwenclaw_id: str,
    row_id: int,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _global_svc(handler)
    try:
        row = await svc.get(jiuwenclaw_id, row_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="global policy not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@global_router.patch("/{row_id}", response_model=ResponseModel)
async def update_global_policy(
    jiuwenclaw_id: str,
    row_id: int,
    body: ConfigEffectiveGlobalPolicyUpdateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _global_svc(handler)
    try:
        row = await svc.update(jiuwenclaw_id, row_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="global policy not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@global_router.delete("/{row_id}", response_model=ResponseModel)
async def delete_global_policy(
    jiuwenclaw_id: str,
    row_id: int,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _global_svc(handler)
    try:
        ok = await svc.delete(jiuwenclaw_id, row_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="global policy not found")
    return ResponseModel(
        code=200, message="success", data={"deleted": True, "id": row_id}
    )

# --- Service 层级 ---

service_router = APIRouter(
    prefix="/{jiuwenclaw_id}/config-effective/service-policies",
)


def _service_svc(handler: DBHandler) -> ConfigEffectiveServicePolicyService:
    return ConfigEffectiveServicePolicyService(handler)


@service_router.post("/", response_model=ResponseModel)
async def create_service_policy(
    jiuwenclaw_id: str,
    body: ConfigEffectiveServicePolicyCreateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _service_svc(handler)
    try:
        data = await svc.create(jiuwenclaw_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data.model_dump())


@service_router.get("/", response_model=ResponseModel)
async def list_service_policies(
    jiuwenclaw_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    query: Annotated[ConfigEffectiveServicePolicyListQuery, Query()],
):
    svc = _service_svc(handler)
    try:
        data = await svc.list_policies(jiuwenclaw_id, query)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@service_router.get("/{row_id}", response_model=ResponseModel)
async def get_service_policy(
    jiuwenclaw_id: str,
    row_id: int,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _service_svc(handler)
    try:
        row = await svc.get(jiuwenclaw_id, row_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="service policy not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@service_router.patch("/{row_id}", response_model=ResponseModel)
async def update_service_policy(
    jiuwenclaw_id: str,
    row_id: int,
    body: ConfigEffectiveServicePolicyUpdateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _service_svc(handler)
    try:
        row = await svc.update(jiuwenclaw_id, row_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="service policy not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@service_router.delete("/{row_id}", response_model=ResponseModel)
async def delete_service_policy(
    jiuwenclaw_id: str,
    row_id: int,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _service_svc(handler)
    try:
        ok = await svc.delete(jiuwenclaw_id, row_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="service policy not found")
    return ResponseModel(
        code=200, message="success", data={"deleted": True, "id": row_id}
    )


# --- Agent 层级 ---

agent_router = APIRouter(
    prefix="/{jiuwenclaw_id}/config-effective/agent-policies",
)


def _agent_svc(handler: DBHandler) -> ConfigEffectiveAgentPolicyService:
    return ConfigEffectiveAgentPolicyService(handler)


@agent_router.post("/", response_model=ResponseModel)
async def create_agent_policy(
    jiuwenclaw_id: str,
    body: ConfigEffectiveAgentPolicyCreateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _agent_svc(handler)
    try:
        data = await svc.create(jiuwenclaw_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data.model_dump())


@agent_router.get("/", response_model=ResponseModel)
async def list_agent_policies(
    jiuwenclaw_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    query: Annotated[ConfigEffectiveAgentPolicyListQuery, Query()],
):
    svc = _agent_svc(handler)
    try:
        data = await svc.list_policies(jiuwenclaw_id, query)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@agent_router.get("/{row_id}", response_model=ResponseModel)
async def get_agent_policy(
    jiuwenclaw_id: str,
    row_id: int,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _agent_svc(handler)
    try:
        row = await svc.get(jiuwenclaw_id, row_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="agent policy not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@agent_router.patch("/{row_id}", response_model=ResponseModel)
async def update_agent_policy(
    jiuwenclaw_id: str,
    row_id: int,
    body: ConfigEffectiveAgentPolicyUpdateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _agent_svc(handler)
    try:
        row = await svc.update(jiuwenclaw_id, row_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="agent policy not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@agent_router.delete("/{row_id}", response_model=ResponseModel)
async def delete_agent_policy(
    jiuwenclaw_id: str,
    row_id: int,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _agent_svc(handler)
    try:
        ok = await svc.delete(jiuwenclaw_id, row_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="agent policy not found")
    return ResponseModel(
        code=200, message="success", data={"deleted": True, "id": row_id}
    )


config_effective_policy_router = APIRouter()
config_effective_policy_router.include_router(mapping_router)
config_effective_policy_router.include_router(global_router)
config_effective_policy_router.include_router(service_router)
config_effective_policy_router.include_router(agent_router)
