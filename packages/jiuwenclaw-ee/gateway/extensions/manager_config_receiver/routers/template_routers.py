# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""模板同步 API（显式 Body schema，对齐 Manager template_routers）。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..core.template.agent_template import AgentTemplateService
from ..core.template.embedding_template import EmbeddingTemplateService
from ..core.template.extension_config_template import ExtensionConfigTemplateService
from ..core.template.model_template import ModelTemplateService
from ..core.template.service_config_template import ServiceConfigTemplateService
from ..core.template.skill_whitelist_template import SkillWhitelistTemplateService
from ..schemas.common_schemas import ResponseModel
from ..schemas.sync_schemas import SyncEnvelopeOnlyBody, make_sync_body
from ..schemas.template_schemas import (
    AgentTemplateCreateRequest,
    AgentTemplateUpdateRequest,
    EmbeddingTemplateCreateRequest,
    EmbeddingTemplateUpdateRequest,
    ExtensionConfigTemplateCreateRequest,
    ExtensionConfigTemplateUpdateRequest,
    ModelTemplateCreateRequest,
    ModelTemplateUpdateRequest,
    ServiceConfigTemplateCreateRequest,
    ServiceConfigTemplateUpdateRequest,
    SkillWhitelistTemplateCreateRequest,
    SkillWhitelistTemplateUpdateRequest,
)
from .deps import build_sync_context, sync_write_data
from .runtime_notify import trigger_runtime_config_update

templates_router = APIRouter()

_ServiceFactory = Callable[[], Any]


def _http_exc(exc: ValueError) -> HTTPException:
    detail = str(exc)
    status = 404 if "not found" in detail else 400
    return HTTPException(status_code=status, detail=detail)


def _add_template_crud(
    path: str,
    svc_factory: _ServiceFactory,
    tag: str,
    create_body: type,
    update_body: type,
) -> None:
    create_sync = make_sync_body(f"{tag.title().replace('_', '')}TemplateCreateSyncBody", create_body)
    update_sync = make_sync_body(f"{tag.title().replace('_', '')}TemplateUpdateSyncBody", update_body)

    async def create_template(
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

    async def update_template(
        request: Request,
        template_id: str,
        body: Any,
    ):
        sync = await build_sync_context(body, request.method)
        try:
            await svc_factory().update(
                sync.jiuwenclaw_id, template_id, sync.business
            )
        except ValueError as exc:
            raise _http_exc(exc) from exc
        trigger_runtime_config_update()
        return ResponseModel(
            code=200, message="success", data=sync_write_data(sync, None)
        )

    async def delete_template(
        request: Request,
        template_id: str,
        body: SyncEnvelopeOnlyBody,
    ):
        sync = await build_sync_context(body, request.method)
        try:
            await svc_factory().delete(sync.jiuwenclaw_id, template_id)
        except ValueError as exc:
            raise _http_exc(exc) from exc
        trigger_runtime_config_update()
        return ResponseModel(
            code=200, message="success", data=sync_write_data(sync, None)
        )

    create_template.__name__ = f"create_{tag}_template"
    update_template.__name__ = f"update_{tag}_template"
    delete_template.__name__ = f"delete_{tag}_template"
    create_template.__annotations__["body"] = create_sync
    update_template.__annotations__["body"] = update_sync

    templates_router.add_api_route(
        path, create_template, methods=["POST"], response_model=ResponseModel
    )
    templates_router.add_api_route(
        f"{path}/{{template_id}}",
        update_template,
        methods=["PATCH"],
        response_model=ResponseModel,
    )
    templates_router.add_api_route(
        f"{path}/{{template_id}}",
        delete_template,
        methods=["DELETE"],
        response_model=ResponseModel,
    )


_add_template_crud(
    "/model-templates",
    ModelTemplateService,
    "model",
    ModelTemplateCreateRequest,
    ModelTemplateUpdateRequest,
)
_add_template_crud(
    "/embedding-templates",
    EmbeddingTemplateService,
    "embedding",
    EmbeddingTemplateCreateRequest,
    EmbeddingTemplateUpdateRequest,
)
_add_template_crud(
    "/extension-config-templates",
    ExtensionConfigTemplateService,
    "extension_config",
    ExtensionConfigTemplateCreateRequest,
    ExtensionConfigTemplateUpdateRequest,
)
_add_template_crud(
    "/skill-whitelist-templates",
    SkillWhitelistTemplateService,
    "skill_whitelist",
    SkillWhitelistTemplateCreateRequest,
    SkillWhitelistTemplateUpdateRequest,
)
_add_template_crud(
    "/service-config-templates",
    ServiceConfigTemplateService,
    "service_config",
    ServiceConfigTemplateCreateRequest,
    ServiceConfigTemplateUpdateRequest,
)
_add_template_crud(
    "/agent-templates",
    AgentTemplateService,
    "agent",
    AgentTemplateCreateRequest,
    AgentTemplateUpdateRequest,
)
