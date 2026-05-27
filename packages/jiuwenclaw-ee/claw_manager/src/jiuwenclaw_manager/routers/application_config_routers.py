"""应用配置 API：channel 注册与管理（针对特定 Gateway 实例的配置）。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from openjiuwen_runtime.foundation.db.handler import DBHandler
from pydantic import BaseModel, Field

from jiuwenclaw_manager.core.application_config.channel_config import ChannelConfigService
from jiuwenclaw_manager.infrastructure.db import get_db_handler
from jiuwenclaw_manager.schemas.common_schemas import ResponseModel

application_config_router = APIRouter()


def _channel_config_svc(handler: DBHandler) -> ChannelConfigService:
    return ChannelConfigService(handler)


class ChannelRegisterRequest(BaseModel):
    channel_id: str = Field(..., max_length=64)
    channel_name: str = Field(..., max_length=128)
    channel_type: str = Field(..., max_length=32)
    bot_id: str = Field(..., max_length=64)
    config: dict | None = Field(default=None)
    status: str = Field(..., max_length=32)


class ChannelDeactivateRequest(BaseModel):
    graceful: bool | None = Field(default=True)
    timeout: int | None = Field(default=30)


@application_config_router.post(
    "/{jiuwenclaw_id}/channels", response_model=ResponseModel
)
async def register_channel(
    jiuwenclaw_id: str,
    body: ChannelRegisterRequest,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _channel_config_svc(handler)
    try:
        data = await svc.register(
            jiuwenclaw_id=jiuwenclaw_id,
            channel_id=body.channel_id,
            channel_name=body.channel_name,
            channel_type=body.channel_type,
            bot_id=body.bot_id,
            config=body.config,
            status=body.status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@application_config_router.get(
    "/{jiuwenclaw_id}/channels", response_model=ResponseModel
)
async def list_channels(
    jiuwenclaw_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    channel_type: str | None = Query(default=None, max_length=32),
    status: str | None = Query(default=None, max_length=32),
):
    svc = _channel_config_svc(handler)
    try:
        data = await svc.list(
            jiuwenclaw_id=jiuwenclaw_id,
            channel_type=channel_type,
            status=status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@application_config_router.post(
    "/{jiuwenclaw_id}/channels/{channel_id}/activate", response_model=ResponseModel
)
async def activate_channel(
    jiuwenclaw_id: str,
    channel_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _channel_config_svc(handler)
    try:
        data = await svc.activate(jiuwenclaw_id=jiuwenclaw_id, channel_id=channel_id)
    except ValueError as exc:
        if "not found" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@application_config_router.post(
    "/{jiuwenclaw_id}/channels/{channel_id}/deactivate", response_model=ResponseModel
)
async def deactivate_channel(
    jiuwenclaw_id: str,
    channel_id: str,
    body: ChannelDeactivateRequest,  # ✅ 直接声明，FastAPI 自动解析
    handler: Annotated[DBHandler, Depends(get_db_handler)],  # ✅ 标准依赖注入
):
    svc = _channel_config_svc(handler)
    graceful = body.graceful if body else True
    timeout = body.timeout if body else 30
    try:
        data = await svc.deactivate(
            jiuwenclaw_id=jiuwenclaw_id,
            channel_id=channel_id,
            graceful=graceful,
            timeout=timeout,
        )
    except ValueError as exc:
        if "not found" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@application_config_router.delete(
    "/{jiuwenclaw_id}/channels/{channel_id}", response_model=ResponseModel
)
async def delete_channel(
    jiuwenclaw_id: str,
    channel_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _channel_config_svc(handler)
    try:
        await svc.delete(jiuwenclaw_id=jiuwenclaw_id, channel_id=channel_id)
    except ValueError as exc:
        if "not found" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success")
