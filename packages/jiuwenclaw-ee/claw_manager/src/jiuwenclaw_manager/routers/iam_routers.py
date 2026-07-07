"""IAM 管理 API（claw_manager 侧）：bot CRUD/可见性(admin 守卫) + 当前用户可见 bot。

身份/组织/用户/成员的管理 API 已迁至认证服务(jiuwenclaw_identity, /v1/users·/v1/orgs)；
此处只保留平台配置侧的 bot 与"我可见的 bot"。鉴权统一走 JWT(deps 验签)。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.core.iam import BotService, MeService
from jiuwenclaw_manager.infrastructure.config import settings
from jiuwenclaw_manager.infrastructure.db import get_db_handler
from jiuwenclaw_manager.routers.deps import get_current_user, require_admin
from jiuwenclaw_manager.schemas.common_schemas import ResponseModel
from jiuwenclaw_manager.schemas.iam_schemas import (
    BotCreateBody,
    BotUpdateBody,
)

_Handler = Annotated[DBHandler, Depends(get_db_handler)]
_CurUser = Annotated[Any, Depends(get_current_user)]


def _ok(data: Any = None) -> ResponseModel:
    return ResponseModel(code=200, message="success", data=data)


def _bad(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


# ======================= 用户态(当前登录用户) =======================
me_router = APIRouter(dependencies=[Depends(get_current_user)])


@me_router.get("/bots", response_model=ResponseModel)
async def my_bots(handler: _Handler, user: _CurUser, group_id: str = Query(...)):
    # 当前实例由本 manager 部署的 JIUWENCLAW_ID 决定（每命名空间一个），前端无需传。
    bots = await MeService(handler).list_visible_bots(
        getattr(user, "user_id"), group_id, getattr(user, "groups", []),
        jiuwenclaw_id=settings.jiuwenclaw_id,
    )
    return _ok({"bots": bots})


# ======================= bot（admin） =======================
bot_router = APIRouter(dependencies=[Depends(require_admin)])


@bot_router.get("/", response_model=ResponseModel)
async def list_bots(handler: _Handler, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
    return _ok(await BotService(handler).list(page=page, page_size=page_size))


@bot_router.post("/", response_model=ResponseModel)
async def create_bot(body: BotCreateBody, handler: _Handler):
    try:
        return _ok(await BotService(handler).create(body.bot_id, body.name, body.description))
    except ValueError as e:
        raise _bad(e) from e


@bot_router.get("/{bot_id}", response_model=ResponseModel)
async def get_bot(bot_id: str, handler: _Handler):
    row = await BotService(handler).get(bot_id)
    if row is None:
        raise HTTPException(status_code=404, detail="bot not found")
    return _ok(row)


@bot_router.patch("/{bot_id}", response_model=ResponseModel)
async def update_bot(bot_id: str, body: BotUpdateBody, handler: _Handler):
    row = await BotService(handler).update(
        bot_id, name=body.name, description=body.description, status=body.status,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="bot not found")
    return _ok(row)


@bot_router.delete("/{bot_id}", response_model=ResponseModel)
async def delete_bot(bot_id: str, handler: _Handler):
    ok = await BotService(handler).delete(bot_id)
    if not ok:
        raise HTTPException(status_code=404, detail="bot not found")
    return _ok({"deleted": True})


@bot_router.get("/{bot_id}/visibility", response_model=ResponseModel)
async def list_bot_visibility(bot_id: str, handler: _Handler):
    """某 bot 跨所有实例的可见性行（只读参考；按实例的增改在 /instances/{jid}/bots）。"""
    return _ok({"visibility": await BotService(handler).list_visibility(bot_id)})
