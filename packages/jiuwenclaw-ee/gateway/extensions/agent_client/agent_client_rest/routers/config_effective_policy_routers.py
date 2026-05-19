from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from ..core.config_effective_policy.config_default_template_mapping import (
    create_config_default_template_mapping_record,
    delete_config_default_template_mapping_record,
    get_config_default_template_mapping_record,
    list_config_default_template_mapping_records,
    update_config_default_template_mapping_record,
)
from ..core.config_effective_policy.config_effective_agent_policy import (
    create_config_effective_agent_policy_record,
    delete_config_effective_agent_policy_record,
    get_config_effective_agent_policy_record,
    list_config_effective_agent_policy_records,
    update_config_effective_agent_policy_record,
)
from ..core.config_effective_policy.config_effective_global_policy import (
    create_config_effective_global_policy_record,
    delete_config_effective_global_policy_record,
    get_config_effective_global_policy_record,
    list_config_effective_global_policy_records,
    update_config_effective_global_policy_record,
)
from ..core.config_effective_policy.config_effective_service_policy import (
    create_config_effective_service_policy_record,
    delete_config_effective_service_policy_record,
    get_config_effective_service_policy_record,
    list_config_effective_service_policy_records,
    update_config_effective_service_policy_record,
)
from ..schemas import (
    ConfigDefaultTemplateMappingCreateRequest,
    ConfigDefaultTemplateMappingUpdateRequest,
    ConfigEffectiveAgentPolicyCreateRequest,
    ConfigEffectiveAgentPolicyUpdateRequest,
    ConfigEffectiveGlobalPolicyCreateRequest,
    ConfigEffectiveGlobalPolicyUpdateRequest,
    ConfigEffectiveServicePolicyCreateRequest,
    ConfigEffectiveServicePolicyUpdateRequest,
    ResponseModel,
)

config_effective_policy_router = APIRouter()


@config_effective_policy_router.post("/config-default-template-mappings")
async def create_template_mapping(
    request: ConfigDefaultTemplateMappingCreateRequest,
    req: Request,
) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    try:
        row = await create_config_default_template_mapping_record(handler, request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=row)


@config_effective_policy_router.get("/config-default-template-mappings")
async def list_template_mappings(
    req: Request,
    user_id: str | None = Query(default=None, description="按 user_id 精确筛选"),
    group_id: str | None = Query(default=None, description="按 group_id 精确筛选"),
    template_type: str | None = Query(
        default=None,
        description="模板类型：model / channel / skill_whitelist / service_resource",
    ),
    template_id: str | None = Query(default=None, description="按 template_id 精确筛选"),
    enabled: bool | None = None,
    page_size: int = Query(default=20, ge=1, le=200),
    page_num: int = Query(default=1, ge=1),
) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    try:
        data = await list_config_default_template_mapping_records(
            handler,
            user_id=user_id,
            group_id=group_id,
            template_type=template_type,
            template_id=template_id,
            enabled=enabled,
            page_size=page_size,
            page_num=page_num,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@config_effective_policy_router.get("/config-default-template-mappings/{mapping_id}")
async def get_template_mapping(
    mapping_id: int,
    req: Request,
) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    try:
        row = await get_config_default_template_mapping_record(handler, mapping_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="template mapping not found")
    return ResponseModel(code=200, message="success", data=row)


@config_effective_policy_router.put("/config-default-template-mappings/{mapping_id}")
async def update_template_mapping(
    mapping_id: int,
    request: ConfigDefaultTemplateMappingUpdateRequest,
    req: Request,
) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    try:
        row = await update_config_default_template_mapping_record(
            handler, mapping_id, request
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="template mapping not found")
    return ResponseModel(code=200, message="success", data=row)


@config_effective_policy_router.delete("/config-default-template-mappings/{mapping_id}")
async def delete_template_mapping(
    mapping_id: int,
    req: Request,
) -> ResponseModel[None]:
    handler = req.app.state.db_handler
    try:
        deleted = await delete_config_default_template_mapping_record(handler, mapping_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="template mapping not found")
    return ResponseModel(code=200, message="success")


@config_effective_policy_router.post("/config-effective/global-policies")
async def create_global_policy(
    request: ConfigEffectiveGlobalPolicyCreateRequest,
    req: Request,
) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    try:
        row = await create_config_effective_global_policy_record(handler, request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=row)


@config_effective_policy_router.get("/config-effective/global-policies")
async def list_global_policies(
    req: Request,
    enabled: bool | None = None,
    page_size: int = Query(default=20, ge=1, le=200),
    page_num: int = Query(default=1, ge=1),
) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    try:
        data = await list_config_effective_global_policy_records(
            handler,
            enabled=enabled,
            page_size=page_size,
            page_num=page_num,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@config_effective_policy_router.get("/config-effective/global-policies/{policy_id}")
async def get_global_policy(
    policy_id: int,
    req: Request,
) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    try:
        row = await get_config_effective_global_policy_record(handler, policy_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="global policy not found")
    return ResponseModel(code=200, message="success", data=row)


@config_effective_policy_router.put("/config-effective/global-policies/{policy_id}")
async def update_global_policy(
    policy_id: int,
    request: ConfigEffectiveGlobalPolicyUpdateRequest,
    req: Request,
) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    try:
        row = await update_config_effective_global_policy_record(
            handler, policy_id, request
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="global policy not found")
    return ResponseModel(code=200, message="success", data=row)


@config_effective_policy_router.delete("/config-effective/global-policies/{policy_id}")
async def delete_global_policy(
    policy_id: int,
    req: Request,
) -> ResponseModel[None]:
    handler = req.app.state.db_handler
    try:
        deleted = await delete_config_effective_global_policy_record(handler, policy_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="global policy not found")
    return ResponseModel(code=200, message="success")


@config_effective_policy_router.post("/config-effective/service-policies")
async def create_service_policy(
    request: ConfigEffectiveServicePolicyCreateRequest,
    req: Request,
) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    try:
        row = await create_config_effective_service_policy_record(handler, request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=row)


@config_effective_policy_router.get("/config-effective/service-policies")
async def list_service_policies(
    req: Request,
    enabled: bool | None = None,
    page_size: int = Query(default=20, ge=1, le=200),
    page_num: int = Query(default=1, ge=1),
) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    try:
        data = await list_config_effective_service_policy_records(
            handler,
            enabled=enabled,
            page_size=page_size,
            page_num=page_num,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@config_effective_policy_router.get("/config-effective/service-policies/{policy_id}")
async def get_service_policy(
    policy_id: int,
    req: Request,
) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    try:
        row = await get_config_effective_service_policy_record(handler, policy_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="service policy not found")
    return ResponseModel(code=200, message="success", data=row)


@config_effective_policy_router.put("/config-effective/service-policies/{policy_id}")
async def update_service_policy(
    policy_id: int,
    request: ConfigEffectiveServicePolicyUpdateRequest,
    req: Request,
) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    try:
        row = await update_config_effective_service_policy_record(
            handler, policy_id, request
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="service policy not found")
    return ResponseModel(code=200, message="success", data=row)


@config_effective_policy_router.delete("/config-effective/service-policies/{policy_id}")
async def delete_service_policy(
    policy_id: int,
    req: Request,
) -> ResponseModel[None]:
    handler = req.app.state.db_handler
    try:
        deleted = await delete_config_effective_service_policy_record(handler, policy_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="service policy not found")
    return ResponseModel(code=200, message="success")


@config_effective_policy_router.post("/config-effective/agent-policies")
async def create_agent_policy(
    request: ConfigEffectiveAgentPolicyCreateRequest,
    req: Request,
) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    try:
        row = await create_config_effective_agent_policy_record(handler, request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=row)


@config_effective_policy_router.get("/config-effective/agent-policies")
async def list_agent_policies(
    req: Request,
    service_policy_id: int | None = Query(
        default=None, description="按服务级策略 ID 筛选"
    ),
    enabled: bool | None = None,
    page_size: int = Query(default=20, ge=1, le=200),
    page_num: int = Query(default=1, ge=1),
) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    try:
        data = await list_config_effective_agent_policy_records(
            handler,
            service_policy_id=service_policy_id,
            enabled=enabled,
            page_size=page_size,
            page_num=page_num,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@config_effective_policy_router.get("/config-effective/agent-policies/{policy_id}")
async def get_agent_policy(
    policy_id: int,
    req: Request,
) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    try:
        row = await get_config_effective_agent_policy_record(handler, policy_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="agent policy not found")
    return ResponseModel(code=200, message="success", data=row)


@config_effective_policy_router.put("/config-effective/agent-policies/{policy_id}")
async def update_agent_policy(
    policy_id: int,
    request: ConfigEffectiveAgentPolicyUpdateRequest,
    req: Request,
) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    try:
        row = await update_config_effective_agent_policy_record(
            handler, policy_id, request
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="agent policy not found")
    return ResponseModel(code=200, message="success", data=row)


@config_effective_policy_router.delete("/config-effective/agent-policies/{policy_id}")
async def delete_agent_policy(
    policy_id: int,
    req: Request,
) -> ResponseModel[None]:
    handler = req.app.state.db_handler
    try:
        deleted = await delete_config_effective_agent_policy_record(handler, policy_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="agent policy not found")
    return ResponseModel(code=200, message="success")
