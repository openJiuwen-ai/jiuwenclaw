from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile

from jiuwenclaw.extensions.agent_client.core.application_config.channel_management import (
    create_channel_config_record,
    delete_channel_config_record,
    list_channel_config_records,
    set_channel_status,
)
from jiuwenclaw.extensions.agent_client.core.application_config.model_management import (
    batch_import_model_configs,
    create_model_config_record,
    delete_model_config_record,
    list_model_config_records,
    update_model_config_record,
)
from jiuwenclaw.extensions.agent_client.schemas import (
    ChannelConfigCreateRequest,
    ModelConfigCreateRequest,
    ModelConfigUpdateRequest,
    ResponseModel,
)
application_config_router = APIRouter()


@application_config_router.post("/models")
async def create_model_config(
    request: ModelConfigCreateRequest,
    req: Request,
) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    try:
        row = await create_model_config_record(handler, request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=row)


@application_config_router.get("/models")
async def list_model_configs(
    req: Request,
    model_type: str | None = Query(default=None),
    enabled: bool | None = Query(default=None),
    page_size: int = Query(default=10, ge=1),
    page_num: int = Query(default=1, ge=1),
) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    data = await list_model_config_records(
        handler,
        model_type=model_type,
        enabled=enabled,
        page_size=page_size,
        page_num=page_num,
    )
    return ResponseModel(code=200, message="success", data=data)


@application_config_router.post("/models/import")
async def import_model_configs(
    req: Request,
    file: UploadFile = File(...),
) -> ResponseModel[dict[str, Any]]:
    """批量导入模型配置（multipart CSV/JSON），写入数据库 ``model_config`` 表。"""
    raw = await file.read()
    handler = req.app.state.db_handler
    try:
        data = await batch_import_model_configs(handler, file.filename or "", raw)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid import file: {exc}") from exc
    return ResponseModel(code=200, message="success", data=data)


@application_config_router.put("/models/{model_id}")
async def update_model_config(
    model_id: int,
    request: ModelConfigUpdateRequest,
    req: Request,
) -> ResponseModel[None]:
    handler = req.app.state.db_handler
    try:
        await update_model_config_record(handler, model_id, request)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success")


@application_config_router.delete("/models/{model_id}")
async def delete_model_config(model_id: int, req: Request) -> ResponseModel[None]:
    handler = req.app.state.db_handler
    deleted = await delete_model_config_record(handler, model_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="model config not found")
    return ResponseModel(code=200, message="success")


@application_config_router.post("/channels")
async def register_channel(
    request: ChannelConfigCreateRequest,
    req: Request,
) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    try:
        row = await create_channel_config_record(handler, request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=row)


@application_config_router.get("/channels")
async def list_channels(
    req: Request,
    channel_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page_size: int = Query(default=10, ge=1),
    page_num: int = Query(default=1, ge=1),
) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    data = await list_channel_config_records(
        handler,
        channel_type=channel_type,
        status=status,
        page_size=page_size,
        page_num=page_num,
    )
    return ResponseModel(code=200, message="success", data=data)


@application_config_router.post("/channels/{channel_id}/activate")
async def activate_channel(channel_id: str, req: Request) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    row = await set_channel_status(handler, channel_id, "active")
    if row is None:
        raise HTTPException(status_code=404, detail="channel not found")
    return ResponseModel(
        code=200,
        message="success",
        data={"channel_id": row["channel_id"], "status": row["status"]},
    )


@application_config_router.post("/channels/{channel_id}/deactivate")
async def deactivate_channel(
    channel_id: str,
    req: Request,
) -> ResponseModel[dict[str, Any]]:
    handler = req.app.state.db_handler
    row = await set_channel_status(handler, channel_id, "inactive")
    if row is None:
        raise HTTPException(status_code=404, detail="channel not found")
    return ResponseModel(
        code=200,
        message="success",
        data={"channel_id": row["channel_id"], "status": row["status"]},
    )


@application_config_router.delete("/channels/{channel_id}")
async def unregister_channel(channel_id: str, req: Request) -> ResponseModel[None]:
    handler = req.app.state.db_handler
    deleted = await delete_channel_config_record(handler, channel_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="channel not found")
    return ResponseModel(code=200, message="success")


# Backward compatibility alias
router = application_config_router
