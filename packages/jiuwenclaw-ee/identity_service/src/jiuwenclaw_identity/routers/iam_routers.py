"""身份目录管理 API：组织 / 用户 / 成员（全部 admin 守卫，校验 JWT 的 is_admin claim）。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_identity.core.iam import OrgService, UserService
from jiuwenclaw_identity.infrastructure.db import get_db_handler
from jiuwenclaw_identity.routers.deps import require_admin
from jiuwenclaw_identity.schemas.iam_schemas import (
    AddMembersBody,
    OrgCreateBody,
    OrgUpdateBody,
    SetMembershipBody,
    UserCreateBody,
    UsersBatchCreateBody,
    UserUpdateBody,
)

_Handler = Annotated[DBHandler, Depends(get_db_handler)]


def _bad(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


# ============== 组织（admin） ==============
org_router = APIRouter(dependencies=[Depends(require_admin)])


@org_router.get("/")
async def list_orgs(handler: _Handler, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
    return await OrgService(handler).list(page, page_size)


@org_router.post("/")
async def create_org(body: OrgCreateBody, handler: _Handler):
    try:
        return await OrgService(handler).create(body.group_id, body.name)
    except ValueError as e:
        raise _bad(e) from e


@org_router.get("/{group_id}")
async def get_org(group_id: str, handler: _Handler):
    org = await OrgService(handler).get(group_id)
    if org is None:
        raise HTTPException(status_code=404, detail="org not found")
    return org


@org_router.patch("/{group_id}")
async def update_org(group_id: str, body: OrgUpdateBody, handler: _Handler):
    org = await OrgService(handler).update(group_id, body.name, body.status)
    if org is None:
        raise HTTPException(status_code=404, detail="org not found")
    return org


@org_router.get("/{group_id}/members")
async def list_org_members(group_id: str, handler: _Handler):
    return {"users": await OrgService(handler).list_members(group_id)}


@org_router.post("/{group_id}/members")
async def add_org_members(group_id: str, body: AddMembersBody, handler: _Handler):
    try:
        return {"added": await OrgService(handler).add_members(group_id, body.user_ids)}
    except ValueError as e:
        raise _bad(e) from e


@org_router.delete("/{group_id}/members/{user_id}")
async def remove_org_member(group_id: str, user_id: str, handler: _Handler):
    try:
        await OrgService(handler).remove_member(group_id, user_id)
    except ValueError as e:
        raise _bad(e) from e
    return {"removed": True}


@org_router.delete("/{group_id}")
async def delete_org(group_id: str, handler: _Handler):
    try:
        ok = await OrgService(handler).delete(group_id)
    except ValueError as e:
        raise _bad(e) from e
    if not ok:
        raise HTTPException(status_code=404, detail="org not found")
    return {"deleted": True}


# ============== 用户（admin） ==============
user_router = APIRouter(dependencies=[Depends(require_admin)])


@user_router.get("/")
async def list_users(handler: _Handler, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
    return await UserService(handler).list(page, page_size)


@user_router.post("/")
async def create_user(body: UserCreateBody, handler: _Handler):
    try:
        return await UserService(handler).create(
            user_id=body.user_id, display_name=body.display_name, is_admin=body.is_admin,
            username=body.username, password=body.password,
        )
    except ValueError as e:
        raise _bad(e) from e


@user_router.post("/batch")
async def batch_create_users(body: UsersBatchCreateBody, handler: _Handler):
    """批量新建用户（前端解析 Excel/CSV 后提交 JSON）。

    逐行处理、部分成功：建用户失败(重名/缺项)只标记该行 error；组织无效或设置报错
    仅忽略该组织并归入无组织(告警),绝不使建用户失败。
    """
    usvc = UserService(handler)
    # 一次性载入有效组织，内存校验（id 与名称都可匹配）
    orgs_page = await OrgService(handler).list(1, 200)
    orgs = orgs_page.get("items", []) if isinstance(orgs_page, dict) else []
    valid_ids = {org["group_id"] for org in orgs}
    name_to_id = {org["name"]: org["group_id"] for org in orgs if org.get("name")}

    results: list[dict[str, Any]] = []
    ok_count = 0
    for idx, item in enumerate(body.users):
        row = idx + 1
        try:
            created = await usvc.create(
                user_id=None, display_name=(item.display_name or item.username).strip(),
                is_admin=item.is_admin, username=item.username, password=item.password,
            )
        except ValueError as exc:
            results.append({"row": row, "username": item.username, "ok": False, "error": str(exc)})
            continue
        uid = created["user_id"]
        warnings: list[str] = []
        resolved: list[str] = []
        for token in item.orgs:
            tok = str(token).strip()
            if not tok:
                continue
            gid = tok if tok in valid_ids else name_to_id.get(tok)
            if gid:
                resolved.append(gid)
            else:
                warnings.append(f"组织 '{tok}' 不存在，已忽略")
        if resolved:
            try:
                await usvc.set_orgs(uid, resolved)
            except ValueError as exc:  # 极端情况：仍归入无组织，不失败
                warnings.append(f"设置组织失败，已归入无组织：{exc}")
        ok_count += 1
        results.append({"row": row, "username": item.username, "ok": True, "user_id": uid, "warnings": warnings})

    return {
        "summary": {"total": len(body.users), "ok": ok_count, "failed": len(body.users) - ok_count},
        "results": results,
    }


@user_router.get("/{user_id}")
async def get_user(user_id: str, handler: _Handler):
    user = await UserService(handler).get(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    return user


@user_router.patch("/{user_id}")
async def update_user(user_id: str, body: UserUpdateBody, handler: _Handler):
    user = await UserService(handler).update(
        user_id, display_name=body.display_name, is_admin=body.is_admin,
        status=body.status, password=body.password,
    )
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    return user


@user_router.delete("/{user_id}")
async def delete_user(user_id: str, handler: _Handler):
    ok = await UserService(handler).delete(user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="user not found")
    return {"deleted": True}


@user_router.get("/{user_id}/orgs")
async def list_user_orgs(user_id: str, handler: _Handler):
    return {"group_ids": await UserService(handler).list_org_ids(user_id)}


@user_router.put("/{user_id}/orgs")
async def set_user_orgs(user_id: str, body: SetMembershipBody, handler: _Handler):
    try:
        return {"group_ids": await UserService(handler).set_orgs(user_id, body.group_ids)}
    except ValueError as e:
        raise _bad(e) from e
