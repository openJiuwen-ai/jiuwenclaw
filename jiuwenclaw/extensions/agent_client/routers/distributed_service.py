from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from jiuwenclaw.extensions.agent_client.core.distributed_service.agent_server_cfg_mgr import (
    get_agent_server_instance_config,
    upsert_agent_server_instance_config,
)
from jiuwenclaw.extensions.agent_client.core.distributed_service.agent_server_status import (
    get_agent_server_service_detail,
    list_agent_server_service_status,
)
from jiuwenclaw.extensions.agent_client.core.distributed_service.session_map import (
    get_session_mapping_detail,
    list_session_mappings,
)
from jiuwenclaw.extensions.agent_client.core.distributed_service.tenant_isolation_mgr import (
    list_tenant_isolation_policies,
    upsert_tenant_isolation_policy,
)
from jiuwenclaw.extensions.agent_client.core.distributed_service.session_affinity_mgr import (
    list_session_affinity_policies,
    upsert_session_affinity_policy,
)
from jiuwenclaw.extensions.agent_client.schemas import (
    AgentServerConfigUpdateRequest,
    ResponseModel,
    SessionMappingListQueryRequest,
    SessionAffinityPolicyUpdateRequest,
    TenantIsolationPolicyUpdateRequest,
)

distributed_service_router = APIRouter()


@distributed_service_router.put("/agent-server/config")
async def update_instance_agent_server_config(
    request: AgentServerConfigUpdateRequest,
    req: Request,
) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    try:
        data = await upsert_agent_server_instance_config(handler, request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@distributed_service_router.get("/agent-server/config")
async def get_instance_agent_server_config(req: Request) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    data = await get_agent_server_instance_config(handler)
    return ResponseModel(code=200, message="success", data=data)


@distributed_service_router.get("/services/status")
async def get_instance_service_status_list(
    req: Request,
    component: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page_num: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=1000),
) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    items = await list_agent_server_service_status(
        handler,
        component=component,
        status=status,
        page_num=page_num,
        page_size=page_size,
    )
    return ResponseModel(code=200, message="success", data={"items": items})


@distributed_service_router.get("/services/{pod_name}")
async def get_instance_service_detail(pod_name: str, req: Request) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    detail = await get_agent_server_service_detail(handler, pod_name=pod_name)
    if detail is None:
        raise HTTPException(status_code=404, detail="service pod not found")
    return ResponseModel(code=200, message="success", data=detail)


@distributed_service_router.get("/sessions")
async def get_instance_session_mapping_list(
    req: Request,
    query: Annotated[SessionMappingListQueryRequest, Depends()],
) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    data = await list_session_mappings(
        handler,
        session_id=query.session_id,
        user_id=query.user_id,
        group_id=query.group_id,
        bot_id=query.bot_id,
        page_num=query.page_num,
        page_size=query.page_size,
    )
    return ResponseModel(code=200, message="success", data=data)


@distributed_service_router.get("/sessions/{session_id}")
async def get_instance_session_mapping_detail(
    session_id: str,
    req: Request,
) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    detail = await get_session_mapping_detail(handler, session_id=session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="session mapping not found")
    return ResponseModel(code=200, message="success", data=detail)


@distributed_service_router.get("/isolation-policies")
async def get_instance_isolation_policy_list(
    req: Request,
    isolation_level: str | None = Query(default=None),
    enabled: bool | None = Query(default=None),
    page_num: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=1000),
) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    items = await list_tenant_isolation_policies(
        handler,
        isolation_level=isolation_level,
        enabled=enabled,
        page_num=page_num,
        page_size=page_size,
    )
    brief_items = [
        {
            "policy_id": item.get("id"),
            "policy_name": item.get("policy_name"),
            "isolation_level": item.get("isolation_level"),
            "enabled": item.get("enabled"),
            "priority": item.get("priority"),
        }
        for item in items
    ]
    return ResponseModel(code=200, message="success", data={"items": brief_items})


@distributed_service_router.put("/isolation-policies/{policy_id}")
async def update_instance_isolation_policy(
    policy_id: int,
    request: TenantIsolationPolicyUpdateRequest,
    req: Request,
) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    try:
        data = await upsert_tenant_isolation_policy(
            handler,
            policy_id=policy_id,
            request=request,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@distributed_service_router.put("/session-affinity")
async def update_instance_session_affinity_policy(
    request: SessionAffinityPolicyUpdateRequest,
    req: Request,
) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    try:
        row = await upsert_session_affinity_policy(handler, request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=row)


@distributed_service_router.get("/session-affinity")
async def get_instance_session_affinity_policy(
    req: Request,
    page_num: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=1000),
) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    data = await list_session_affinity_policies(
        handler,
        page_num=page_num,
        page_size=page_size,
    )
    return ResponseModel(code=200, message="success", data=data)


# Backward compatibility alias
router = distributed_service_router
  