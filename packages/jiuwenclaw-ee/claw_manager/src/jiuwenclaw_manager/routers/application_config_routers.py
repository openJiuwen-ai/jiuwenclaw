"""应用配置 API：channel 注册与管理、logging 配置、embed 配置（针对特定 Gateway 实例的配置）。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from openjiuwen_runtime.foundation.db.handler import DBHandler
from pydantic import BaseModel, Field

from jiuwenclaw_manager.core.application_config.channel_config import ChannelConfigService
from jiuwenclaw_manager.core.application_config.embed_config import EmbedConfigService

from jiuwenclaw_manager.core.application_config.log_masking_rule import (
    LogMaskingRuleService,
)
from jiuwenclaw_manager.schemas.application_config_schemas import (
    LogMaskingRuleCreateBody,
    LogMaskingRuleUpdateBody,
)

from jiuwenclaw_manager.core.application_config.logging_config import LoggingConfigService

from jiuwenclaw_manager.infrastructure.db import get_db_handler
from jiuwenclaw_manager.schemas.common_schemas import ResponseModel

application_config_router = APIRouter()


def _channel_config_svc(handler: DBHandler) -> ChannelConfigService:
    return ChannelConfigService(handler)


def _embed_config_svc(handler: DBHandler) -> EmbedConfigService:
    return EmbedConfigService(handler)


def _log_masking_rule_svc(handler: DBHandler) -> LogMaskingRuleService:
    return LogMaskingRuleService(handler)


def _logging_config_svc(handler: DBHandler) -> LoggingConfigService:
    return LoggingConfigService(handler)


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


@application_config_router.get(
    "/{jiuwenclaw_id}/log-masking-rules",
    response_model=ResponseModel,
)
async def list_log_masking_rules(
    jiuwenclaw_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    enabled: bool | None = Query(default=None),
):
    svc = _log_masking_rule_svc(handler)
    try:
        data = await svc.list(jiuwenclaw_id, enabled=enabled)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@application_config_router.get(
    "/{jiuwenclaw_id}/log-masking-rules/{rule_id}",
    response_model=ResponseModel,
)
async def get_log_masking_rule(
    jiuwenclaw_id: str,
    rule_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _log_masking_rule_svc(handler)
    try:
        row = await svc.get(jiuwenclaw_id, rule_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="log masking rule not found")
    return ResponseModel(code=200, message="success", data=row.model_dump(mode="json"))


@application_config_router.post(
    "/{jiuwenclaw_id}/log-masking-rules",
    response_model=ResponseModel,
)
async def create_log_masking_rule(
    jiuwenclaw_id: str,
    body: LogMaskingRuleCreateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _log_masking_rule_svc(handler)
    try:
        row = await svc.create(jiuwenclaw_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=row.model_dump(mode="json"))


@application_config_router.patch(
    "/{jiuwenclaw_id}/log-masking-rules/{rule_id}",
    response_model=ResponseModel,
)
async def patch_log_masking_rule(
    jiuwenclaw_id: str,
    rule_id: str,
    body: LogMaskingRuleUpdateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _log_masking_rule_svc(handler)
    try:
        row = await svc.update(jiuwenclaw_id, rule_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="log masking rule not found")
    return ResponseModel(code=200, message="success", data=row.model_dump(mode="json"))


@application_config_router.delete(
    "/{jiuwenclaw_id}/log-masking-rules/{rule_id}",
    response_model=ResponseModel,
)
async def delete_log_masking_rule(
    jiuwenclaw_id: str,
    rule_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _log_masking_rule_svc(handler)
    try:
        await svc.delete(jiuwenclaw_id, rule_id)
    except ValueError as exc:
        if "not found" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success")


class LoggingConfigUpsertRequest(BaseModel):
    level: str = Field(default="INFO", max_length=16)
    console_level: str | None = Field(default=None, max_length=16)
    gateway: str | None = Field(default=None, max_length=16)
    channel: str | None = Field(default=None, max_length=16)
    agent_server: str | None = Field(default=None, max_length=16)
    full: str | None = Field(default=None, max_length=16)


@application_config_router.put(
    "/{jiuwenclaw_id}/logging", response_model=ResponseModel
)
async def upsert_logging_config(
    jiuwenclaw_id: str,
    body: LoggingConfigUpsertRequest,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _logging_config_svc(handler)
    try:
        data = await svc.upsert(
            jiuwenclaw_id=jiuwenclaw_id,
            level=body.level,
            console_level=body.console_level,
            gateway=body.gateway,
            channel=body.channel,
            agent_server=body.agent_server,
            full=body.full,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@application_config_router.get(
    "/{jiuwenclaw_id}/logging", response_model=ResponseModel
)
async def get_logging_config(
    jiuwenclaw_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _logging_config_svc(handler)
    data = await svc.get(jiuwenclaw_id=jiuwenclaw_id)
    if data is None:
        raise HTTPException(status_code=404, detail="logging config not found")
    return ResponseModel(code=200, message="success", data=data)


@application_config_router.delete(
    "/{jiuwenclaw_id}/logging", response_model=ResponseModel
)
async def delete_logging_config(
    jiuwenclaw_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _logging_config_svc(handler)
    try:
        await svc.delete(jiuwenclaw_id=jiuwenclaw_id)
    except ValueError as exc:
        if "not found" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success")


class EmbedUpsertRequest(BaseModel):
    embed_api_key: str = Field(default="", max_length=512)
    embed_base_url: str = Field(default="", max_length=1024)
    embed_model: str = Field(default="", max_length=128)


@application_config_router.put(
    "/{jiuwenclaw_id}/embed", response_model=ResponseModel
)
async def upsert_embed(
    jiuwenclaw_id: str,
    body: EmbedUpsertRequest,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _embed_config_svc(handler)
    try:
        data = await svc.upsert(
            jiuwenclaw_id=jiuwenclaw_id,
            embed_api_key=body.embed_api_key,
            embed_base_url=body.embed_base_url,
            embed_model=body.embed_model,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@application_config_router.get(
    "/{jiuwenclaw_id}/embed", response_model=ResponseModel
)
async def get_embed(
    jiuwenclaw_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _embed_config_svc(handler)
    try:
        data = await svc.get(jiuwenclaw_id=jiuwenclaw_id)
    except ValueError as exc:
        if "not found" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@application_config_router.delete(
    "/{jiuwenclaw_id}/embed", response_model=ResponseModel
)
async def delete_embed(
    jiuwenclaw_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _embed_config_svc(handler)
    try:
        await svc.delete(jiuwenclaw_id=jiuwenclaw_id)
    except ValueError as exc:
        if "not found" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success")
