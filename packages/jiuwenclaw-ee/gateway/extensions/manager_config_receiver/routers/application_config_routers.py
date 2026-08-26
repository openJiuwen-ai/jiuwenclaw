# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""应用配置同步 API（对齐 Manager application_config_routers：显式 Body schema）。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from openjiuwen_runtime.foundation.db.handler import DBHandler

from ..core.application_config.log_masking_rule import LogMaskingRuleService
from ..core.application_config.logging_config import LoggingConfigService
from ..core.application_config.memory_config import MemoryConfigService
from ..core.application_config.permissions_config import PermissionsConfigService
from ..core.application_config.task_memory_config import TaskMemoryConfigService
from ..schemas.application_config_schemas import (
    LoggingConfigUpsertRequest,
    LogMaskingRuleCreateRequest,
    LogMaskingRuleUpdateRequest,
    MemoryConfigUpsertRequest,
    PermissionsConfigUpsertRequest,
    TaskMemoryUpsertRequest,
)
from ..schemas.common_schemas import ResponseModel
from ..schemas.sync_schemas import make_sync_body
from .deps import (
    SyncContext,
    VerifySyncEnvelopeOnly,
    get_db_handler,
    sync_write_data,
    verify_sync,
)
from .runtime_notify import trigger_runtime_config_update

application_config_router = APIRouter()

LoggingSyncBody = make_sync_body("LoggingSyncBody", LoggingConfigUpsertRequest)
TaskMemorySyncBody = make_sync_body("TaskMemorySyncBody", TaskMemoryUpsertRequest)
PermissionsSyncBody = make_sync_body(
    "PermissionsSyncBody", PermissionsConfigUpsertRequest
)
MemorySyncBody = make_sync_body("MemorySyncBody", MemoryConfigUpsertRequest)
LogMaskingCreateSyncBody = make_sync_body(
    "LogMaskingCreateSyncBody", LogMaskingRuleCreateRequest
)
LogMaskingUpdateSyncBody = make_sync_body(
    "LogMaskingUpdateSyncBody", LogMaskingRuleUpdateRequest
)


def _logging_svc(handler: DBHandler) -> LoggingConfigService:
    return LoggingConfigService(handler)


def _task_memory_svc(handler: DBHandler) -> TaskMemoryConfigService:
    return TaskMemoryConfigService(handler)


def _permissions_svc(handler: DBHandler) -> PermissionsConfigService:
    return PermissionsConfigService(handler)


def _memory_svc(handler: DBHandler) -> MemoryConfigService:
    return MemoryConfigService(handler)


def _log_masking_svc(handler: DBHandler) -> LogMaskingRuleService:
    return LogMaskingRuleService(handler)


@application_config_router.put("/logging", response_model=ResponseModel)
async def upsert_logging(
    sync: Annotated[SyncContext, Depends(verify_sync(LoggingSyncBody))],
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    try:
        result = await _logging_svc(handler).upsert(sync.jiuwenclaw_id, **sync.business)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    trigger_runtime_config_update()
    return ResponseModel(code=200, message="success", data=sync_write_data(sync, result))


@application_config_router.delete("/logging", response_model=ResponseModel)
async def delete_logging(
    sync: VerifySyncEnvelopeOnly,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    try:
        await _logging_svc(handler).delete(sync.jiuwenclaw_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    trigger_runtime_config_update()
    return ResponseModel(code=200, message="success", data=sync_write_data(sync, None))


@application_config_router.put("/task-memory", response_model=ResponseModel)
async def upsert_task_memory(
    sync: Annotated[SyncContext, Depends(verify_sync(TaskMemorySyncBody))],
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    try:
        result = await _task_memory_svc(handler).upsert(
            sync.jiuwenclaw_id, sync.business
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    trigger_runtime_config_update()
    return ResponseModel(code=200, message="success", data=sync_write_data(sync, result))


@application_config_router.delete("/task-memory", response_model=ResponseModel)
async def delete_task_memory(
    sync: VerifySyncEnvelopeOnly,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    try:
        await _task_memory_svc(handler).delete(sync.jiuwenclaw_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    trigger_runtime_config_update()
    return ResponseModel(code=200, message="success", data=sync_write_data(sync, None))


@application_config_router.put("/permissions", response_model=ResponseModel)
async def upsert_permissions(
    sync: Annotated[SyncContext, Depends(verify_sync(PermissionsSyncBody))],
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    try:
        result = await _permissions_svc(handler).upsert(
            sync.jiuwenclaw_id, **sync.business
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    trigger_runtime_config_update()
    return ResponseModel(code=200, message="success", data=sync_write_data(sync, result))


@application_config_router.delete("/permissions", response_model=ResponseModel)
async def delete_permissions(
    sync: VerifySyncEnvelopeOnly,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    try:
        await _permissions_svc(handler).delete(sync.jiuwenclaw_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    trigger_runtime_config_update()
    return ResponseModel(code=200, message="success", data=sync_write_data(sync, None))


@application_config_router.put("/memory", response_model=ResponseModel)
async def upsert_memory(
    sync: Annotated[SyncContext, Depends(verify_sync(MemorySyncBody))],
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    try:
        result = await _memory_svc(handler).upsert(sync.jiuwenclaw_id, **sync.business)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    trigger_runtime_config_update()
    return ResponseModel(code=200, message="success", data=sync_write_data(sync, result))


@application_config_router.delete("/memory", response_model=ResponseModel)
async def delete_memory(
    sync: VerifySyncEnvelopeOnly,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    try:
        await _memory_svc(handler).delete(sync.jiuwenclaw_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    trigger_runtime_config_update()
    return ResponseModel(code=200, message="success", data=sync_write_data(sync, None))


@application_config_router.post("/log-masking-rules", response_model=ResponseModel)
async def create_log_masking_rule(
    sync: Annotated[SyncContext, Depends(verify_sync(LogMaskingCreateSyncBody))],
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    try:
        result = await _log_masking_svc(handler).create(
            sync.jiuwenclaw_id, sync.business
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    trigger_runtime_config_update()
    return ResponseModel(code=200, message="success", data=sync_write_data(sync, result))


@application_config_router.patch(
    "/log-masking-rules/{rule_id}", response_model=ResponseModel
)
async def patch_log_masking_rule(
    rule_id: str,
    sync: Annotated[SyncContext, Depends(verify_sync(LogMaskingUpdateSyncBody))],
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    try:
        result = await _log_masking_svc(handler).update(
            sync.jiuwenclaw_id, rule_id, sync.business
        )
    except ValueError as exc:
        detail = str(exc)
        status = 404 if "not found" in detail else 400
        raise HTTPException(status_code=status, detail=detail) from exc
    trigger_runtime_config_update()
    return ResponseModel(code=200, message="success", data=sync_write_data(sync, result))


@application_config_router.delete(
    "/log-masking-rules/{rule_id}", response_model=ResponseModel
)
async def delete_log_masking_rule(
    rule_id: str,
    sync: VerifySyncEnvelopeOnly,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    try:
        await _log_masking_svc(handler).delete(sync.jiuwenclaw_id, rule_id)
    except ValueError as exc:
        detail = str(exc)
        status = 404 if "not found" in detail else 400
        raise HTTPException(status_code=status, detail=detail) from exc
    trigger_runtime_config_update()
    return ResponseModel(code=200, message="success", data=sync_write_data(sync, None))
