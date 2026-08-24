# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""实例数据生命周期同步 API。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from openjiuwen_runtime.foundation.db.handler import DBHandler

from ..core.instance.instance_data_lifecycle import InstanceDataLifecycleService
from ..schemas.application_config_schemas import InstanceDataLifecycleRequest
from ..schemas.common_schemas import ResponseModel
from ..schemas.sync_schemas import make_sync_body
from .deps import SyncContext, get_db_handler, sync_write_data, verify_sync

instance_router = APIRouter()

LifecycleSyncBody = make_sync_body("LifecycleSyncBody", InstanceDataLifecycleRequest)


def _lifecycle_svc(handler: DBHandler) -> InstanceDataLifecycleService:
    return InstanceDataLifecycleService(handler)


@instance_router.post("/instance-data-lifecycle", response_model=ResponseModel)
async def instance_data_lifecycle(
    sync: Annotated[SyncContext, Depends(verify_sync(LifecycleSyncBody))],
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    op = str(sync.business.get("op") or "purge").strip()
    if op != "purge":
        raise HTTPException(
            status_code=400, detail=f"unsupported instance_data_lifecycle.op: {op!r}"
        )
    try:
        result = await _lifecycle_svc(handler).purge(sync.jiuwenclaw_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=sync_write_data(sync, result))
