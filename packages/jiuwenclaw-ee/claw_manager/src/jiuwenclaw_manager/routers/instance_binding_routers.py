"""实例(gateway) ↔ 用户/组织/bot 绑定管理 API（claw_manager 侧，admin 守卫）。

设计:目录(bot/用户/组织)保持全局,"谁能用哪个实例、bot 在哪个实例对谁可见"落在
带 jiuwenclaw_id 的关系表(user_gateway / org_gateway / bot_visibility)。
- ``/instances/{jid}/users`` · ``/instances/{jid}/orgs``：某实例花名册的列/加/删。
- ``/instances/{jid}/bots``：某实例已配 bot（含可见范围）/设可见范围/整体移出。
- ``/user-gateways`` · ``/org-gateways``：反查一批实体各自绑了哪些实例（所属实例列,防 N+1）。
用户/组织的目录 CRUD 仍在认证服务(jiuwenclaw_identity, /idp);此处只管绑定关系。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.core.iam import BotService, org_gateway_service, user_gateway_service
from jiuwenclaw_manager.infrastructure.db import get_db_handler
from jiuwenclaw_manager.infrastructure.jiuwenclaw_id import validate_jiuwenclaw_id
from jiuwenclaw_manager.routers.deps import require_admin
from jiuwenclaw_manager.schemas.common_schemas import ResponseModel
from jiuwenclaw_manager.schemas.iam_schemas import InstanceBindBody, InstanceBotVisibilityBody

_Handler = Annotated[DBHandler, Depends(get_db_handler)]


def _ok(data: Any = None) -> ResponseModel:
    return ResponseModel(code=200, message="success", data=data)


async def _jid(handler: DBHandler, jiuwenclaw_id: str) -> str:
    """校验实例存在并返回规范化 id；不存在 → 404。"""
    try:
        return await validate_jiuwenclaw_id(handler, jiuwenclaw_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# 挂在 /instances 前缀下（与 application_config / config_effective_policy 一致）。
binding_router = APIRouter(dependencies=[Depends(require_admin)])


# ---------- 用户 ↔ 实例 ----------
@binding_router.get("/{jiuwenclaw_id}/users", response_model=ResponseModel)
async def list_instance_users(jiuwenclaw_id: str, handler: _Handler):
    jid = await _jid(handler, jiuwenclaw_id)
    return _ok({"user_ids": await user_gateway_service(handler).list_members(jid)})


@binding_router.post("/{jiuwenclaw_id}/users", response_model=ResponseModel)
async def bind_instance_users(jiuwenclaw_id: str, body: InstanceBindBody, handler: _Handler):
    jid = await _jid(handler, jiuwenclaw_id)
    return _ok(await user_gateway_service(handler).bind(jid, body.ids))


@binding_router.delete("/{jiuwenclaw_id}/users", response_model=ResponseModel)
async def unbind_instance_users(jiuwenclaw_id: str, body: InstanceBindBody, handler: _Handler):
    jid = await _jid(handler, jiuwenclaw_id)
    return _ok(await user_gateway_service(handler).unbind(jid, body.ids))


# ---------- 组织 ↔ 实例 ----------
@binding_router.get("/{jiuwenclaw_id}/orgs", response_model=ResponseModel)
async def list_instance_orgs(jiuwenclaw_id: str, handler: _Handler):
    jid = await _jid(handler, jiuwenclaw_id)
    return _ok({"group_ids": await org_gateway_service(handler).list_members(jid)})


@binding_router.post("/{jiuwenclaw_id}/orgs", response_model=ResponseModel)
async def bind_instance_orgs(jiuwenclaw_id: str, body: InstanceBindBody, handler: _Handler):
    jid = await _jid(handler, jiuwenclaw_id)
    return _ok(await org_gateway_service(handler).bind(jid, body.ids))


@binding_router.delete("/{jiuwenclaw_id}/orgs", response_model=ResponseModel)
async def unbind_instance_orgs(jiuwenclaw_id: str, body: InstanceBindBody, handler: _Handler):
    jid = await _jid(handler, jiuwenclaw_id)
    return _ok(await org_gateway_service(handler).unbind(jid, body.ids))


# ---------- bot ↔ 实例（可见范围） ----------
@binding_router.get("/{jiuwenclaw_id}/bots", response_model=ResponseModel)
async def list_instance_bots(jiuwenclaw_id: str, handler: _Handler):
    jid = await _jid(handler, jiuwenclaw_id)
    return _ok({"bots": await BotService(handler).list_instance_bots(jid)})


@binding_router.put("/{jiuwenclaw_id}/bots/{bot_id}/visibility", response_model=ResponseModel)
async def set_instance_bot_visibility(
    jiuwenclaw_id: str, bot_id: str, body: InstanceBotVisibilityBody, handler: _Handler
):
    jid = await _jid(handler, jiuwenclaw_id)
    try:
        scopes = [s.model_dump() for s in body.scopes]
        return _ok({"visibility": await BotService(handler).set_visibility(jid, bot_id, scopes)})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@binding_router.delete("/{jiuwenclaw_id}/bots/{bot_id}", response_model=ResponseModel)
async def remove_instance_bot(jiuwenclaw_id: str, bot_id: str, handler: _Handler):
    jid = await _jid(handler, jiuwenclaw_id)
    removed = await BotService(handler).remove_from_instance(jid, bot_id)
    return _ok({"removed": removed})


# 反查（挂在 /v1 根，不带 /instances 前缀）：一批实体各绑了哪些实例。
gateway_lookup_router = APIRouter(dependencies=[Depends(require_admin)])


# 用逗号分隔的单参数（而非重复 query 参数）：对各种反代/网关都稳(重复参数易被折叠)。
@gateway_lookup_router.get("/user-gateways", response_model=ResponseModel)
async def user_gateways(handler: _Handler, user_ids: str = Query(default="")):
    ids = [x for x in user_ids.split(",") if x.strip()]
    return _ok({"bindings": await user_gateway_service(handler).list_instances_for(ids)})


@gateway_lookup_router.get("/org-gateways", response_model=ResponseModel)
async def org_gateways(handler: _Handler, group_ids: str = Query(default="")):
    ids = [x for x in group_ids.split(",") if x.strip()]
    return _ok({"bindings": await org_gateway_service(handler).list_instances_for(ids)})


@gateway_lookup_router.get("/bot-gateways", response_model=ResponseModel)
async def bot_gateways(handler: _Handler, bot_ids: str = Query(default="")):
    ids = [x for x in bot_ids.split(",") if x.strip()]
    return _ok({"bindings": await BotService(handler).list_instances_for(ids)})
