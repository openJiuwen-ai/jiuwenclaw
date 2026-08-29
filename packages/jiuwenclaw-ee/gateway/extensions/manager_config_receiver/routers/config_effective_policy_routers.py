# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""配置生效策略与默认模板映射同步 API（显式 Body schema，对齐 Manager）。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ..core.config_effective_policy import (
    ConfigDefaultTemplateMappingService,
    ConfigEffectiveAgentPolicyService,
    ConfigEffectiveGlobalPolicyService,
    ConfigEffectiveServicePolicyService,
)
from ..schemas.common_schemas import ResponseModel
from ..schemas.config_effective_policy_schemas import (
    ConfigDefaultTemplateMappingCreateRequest,
    ConfigDefaultTemplateMappingUpdateRequest,
    ConfigEffectiveAgentPolicyCreateRequest,
    ConfigEffectiveAgentPolicyUpdateRequest,
    ConfigEffectiveGlobalPolicyCreateRequest,
    ConfigEffectiveGlobalPolicyUpdateRequest,
    ConfigEffectiveServicePolicyCreateRequest,
    ConfigEffectiveServicePolicyUpdateRequest,
)
from ..schemas.sync_schemas import SyncEnvelopeOnlyBody, make_sync_body
from .deps import (
    SyncContext,
    VerifySyncEnvelopeOnly,
    build_sync_context,
    sync_write_data,
    verify_sync,
)
from .runtime_notify import trigger_runtime_config_update

mapping_router = APIRouter(prefix="/config-default-template-mappings")
agent_policy_router = APIRouter(prefix="/config-effective/agent-policies")
global_policy_router = APIRouter(prefix="/config-effective/global-policies")
service_policy_router = APIRouter(prefix="/config-effective/service-policies")

MappingCreateSyncBody = make_sync_body(
    "MappingCreateSyncBody", ConfigDefaultTemplateMappingCreateRequest
)
MappingUpdateSyncBody = make_sync_body(
    "MappingUpdateSyncBody", ConfigDefaultTemplateMappingUpdateRequest
)

_ServiceFactory = Callable[[], Any]


def _http_exc(exc: ValueError) -> HTTPException:
    detail = str(exc)
    status = 404 if "not found" in detail else 400
    return HTTPException(status_code=status, detail=detail)


@mapping_router.post("/", response_model=ResponseModel)
async def create_template_mapping(
    sync: Annotated[SyncContext, Depends(verify_sync(MappingCreateSyncBody))],
):
    try:
        result = await ConfigDefaultTemplateMappingService().create(
            sync.jiuwenclaw_id, sync.business
        )
    except ValueError as exc:
        raise _http_exc(exc) from exc
    trigger_runtime_config_update()
    return ResponseModel(code=200, message="success", data=sync_write_data(sync, result))


@mapping_router.patch("/{mapping_id}", response_model=ResponseModel)
async def update_template_mapping(
    mapping_id: int,
    sync: Annotated[SyncContext, Depends(verify_sync(MappingUpdateSyncBody))],
):
    try:
        await ConfigDefaultTemplateMappingService().update(
            sync.jiuwenclaw_id, mapping_id, sync.business
        )
    except ValueError as exc:
        raise _http_exc(exc) from exc
    trigger_runtime_config_update()
    return ResponseModel(code=200, message="success", data=sync_write_data(sync, None))


@mapping_router.delete("/{mapping_id}", response_model=ResponseModel)
async def delete_template_mapping(
    mapping_id: int,
    sync: VerifySyncEnvelopeOnly,
):
    try:
        await ConfigDefaultTemplateMappingService().delete(
            sync.jiuwenclaw_id, mapping_id
        )
    except ValueError as exc:
        raise _http_exc(exc) from exc
    trigger_runtime_config_update()
    return ResponseModel(code=200, message="success", data=sync_write_data(sync, None))


def _add_policy_crud(
    router: APIRouter,
    svc_factory: _ServiceFactory,
    tag: str,
    create_body: type,
    update_body: type,
) -> None:
    create_sync = make_sync_body(f"{tag.title()}PolicyCreateSyncBody", create_body)
    update_sync = make_sync_body(f"{tag.title()}PolicyUpdateSyncBody", update_body)

    async def create_policy(
        request: Request,
        body: Any,
    ):
        sync = await build_sync_context(body, request.method)
        try:
            result = await svc_factory().create(
                sync.jiuwenclaw_id, sync.business
            )
        except ValueError as exc:
            raise _http_exc(exc) from exc
        trigger_runtime_config_update()
        return ResponseModel(
            code=200, message="success", data=sync_write_data(sync, result)
        )

    async def update_policy(
        request: Request,
        policy_id: int,
        body: Any,
    ):
        sync = await build_sync_context(body, request.method)
        try:
            await svc_factory().update(
                sync.jiuwenclaw_id, policy_id, sync.business
            )
        except ValueError as exc:
            raise _http_exc(exc) from exc
        trigger_runtime_config_update()
        return ResponseModel(
            code=200, message="success", data=sync_write_data(sync, None)
        )

    async def delete_policy(
        request: Request,
        policy_id: int,
        body: SyncEnvelopeOnlyBody,
    ):
        sync = await build_sync_context(body, request.method)
        try:
            await svc_factory().delete(sync.jiuwenclaw_id, policy_id)
        except ValueError as exc:
            raise _http_exc(exc) from exc
        trigger_runtime_config_update()
        return ResponseModel(
            code=200, message="success", data=sync_write_data(sync, None)
        )

    create_policy.__name__ = f"create_{tag}_policy"
    update_policy.__name__ = f"update_{tag}_policy"
    delete_policy.__name__ = f"delete_{tag}_policy"
    create_policy.__annotations__["body"] = create_sync
    update_policy.__annotations__["body"] = update_sync

    router.add_api_route(
        "/", create_policy, methods=["POST"], response_model=ResponseModel
    )
    router.add_api_route(
        "/{policy_id}",
        update_policy,
        methods=["PATCH"],
        response_model=ResponseModel,
    )
    router.add_api_route(
        "/{policy_id}",
        delete_policy,
        methods=["DELETE"],
        response_model=ResponseModel,
    )


_add_policy_crud(
    agent_policy_router,
    ConfigEffectiveAgentPolicyService,
    "agent",
    ConfigEffectiveAgentPolicyCreateRequest,
    ConfigEffectiveAgentPolicyUpdateRequest,
)
_add_policy_crud(
    global_policy_router,
    ConfigEffectiveGlobalPolicyService,
    "global",
    ConfigEffectiveGlobalPolicyCreateRequest,
    ConfigEffectiveGlobalPolicyUpdateRequest,
)
_add_policy_crud(
    service_policy_router,
    ConfigEffectiveServicePolicyService,
    "service",
    ConfigEffectiveServicePolicyCreateRequest,
    ConfigEffectiveServicePolicyUpdateRequest,
)

config_effective_policy_routers: list[APIRouter] = [
    mapping_router,
    agent_policy_router,
    global_policy_router,
    service_policy_router,
]
