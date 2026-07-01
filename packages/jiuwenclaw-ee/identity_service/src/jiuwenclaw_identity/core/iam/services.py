"""身份目录管理：组织 / 用户 / 成员 的 CRUD 与批量（权威库=身份库）。

bot / 可见性 / 模板等平台配置不在此（留在 claw_manager 管理库）。
DBHandler.get/list_records 返回记录对象（属性访问），统一用 ``_g`` 读取。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_identity.core.auth.password import hash_password
from jiuwenclaw_identity.infrastructure.logger import get_logger
from jiuwenclaw_identity.infrastructure.utils import (
    iso_datetime,
    new_uuid4,
    strip_optional,
    utc_now,
)
from jiuwenclaw_identity.models.identity_models import (
    APP_USER_TABLE_DEF,
    AUTH_IDENTITY_TABLE_DEF,
    AUTH_SESSION_TABLE_DEF,
    NO_ORG_GROUP_ID,
    ORG_TABLE_DEF,
    USER_ORG_MEMBERSHIP_TABLE_DEF,
)

_log = get_logger(__name__)

_APP_USER = APP_USER_TABLE_DEF.table_name
_AUTH_IDENTITY = AUTH_IDENTITY_TABLE_DEF.table_name
_AUTH_SESSION = AUTH_SESSION_TABLE_DEF.table_name
_ORG = ORG_TABLE_DEF.table_name
_MEMBERSHIP = USER_ORG_MEMBERSHIP_TABLE_DEF.table_name
_LOCAL = "local"
_CAP = 100_000


def _g(row: Any, key: str, default: Any = None) -> Any:
    return getattr(row, key, default)


async def _delete_where(
    handler: DBHandler, table: str, filters: dict[str, Any], pk_field: str
) -> None:
    rows = await handler.list_records(table, filters, limit=_CAP, offset=0)
    for r in rows:
        await handler.delete(table, {pk_field: _g(r, pk_field)})


@dataclass(frozen=True)
class _PageOpts:
    """分页/排序选项（把分页相关参数收进具名对象，避免函数参数过多）。"""
    page: int = 1
    page_size: int = 50
    order_by: list[tuple[str, bool]] | None = None


async def _paginate(
    handler: DBHandler, table: str, filters: dict[str, Any], row_out, opts: _PageOpts,
) -> dict[str, Any]:
    page = max(opts.page, 1)
    page_size = min(max(opts.page_size, 1), 200)
    offset = (page - 1) * page_size
    rows = await handler.list_records(
        table, filters, limit=page_size, offset=offset, order_by=opts.order_by,
    )
    total = await handler.count_records(table, filters)
    return {"items": [row_out(r) for r in rows], "total": total, "page": page, "page_size": page_size}


# ======================= 组织 =======================
def _org_out(row: Any) -> dict[str, Any]:
    return {
        "group_id": _g(row, "group_id"),
        "name": _g(row, "name"),
        "status": _g(row, "status"),
        "created_at": iso_datetime(_g(row, "created_at")),
        "updated_at": iso_datetime(_g(row, "updated_at")),
    }


class OrgService:
    def __init__(self, handler: DBHandler) -> None:
        self._h = handler

    async def list(self, page: int = 1, page_size: int = 50) -> dict[str, Any]:
        return await _paginate(self._h, _ORG, {}, _org_out, _PageOpts(page, page_size, [("created_at", False)]))

    async def get(self, group_id: str) -> dict[str, Any] | None:
        row = await self._h.get(_ORG, {"group_id": group_id})
        return _org_out(row) if row is not None else None

    async def create(self, group_id: str | None, name: str) -> dict[str, Any]:
        gid = strip_optional(group_id) or new_uuid4()
        if await self._h.get(_ORG, {"group_id": gid}) is not None:
            raise ValueError(f"org already exists: {gid}")
        now = utc_now()
        await self._h.create(
            _ORG,
            {"group_id": gid, "name": name.strip(), "status": "active", "created_at": now, "updated_at": now},
        )
        _log.info("[IAM] org.create", group_id=gid, name=name.strip())
        return _org_out(await self._h.get(_ORG, {"group_id": gid}))

    async def update(self, group_id: str, name: str | None, status: str | None) -> dict[str, Any] | None:
        if await self._h.get(_ORG, {"group_id": group_id}) is None:
            return None
        updates: dict[str, Any] = {"updated_at": utc_now()}
        if name is not None:
            updates["name"] = name.strip()
        if status is not None:
            updates["status"] = status.strip()
        await self._h.update(_ORG, {"group_id": group_id}, updates)
        return _org_out(await self._h.get(_ORG, {"group_id": group_id}))

    async def delete(self, group_id: str) -> bool:
        if group_id == NO_ORG_GROUP_ID:
            raise ValueError("cannot delete the reserved '无组织' org")
        if await self._h.get(_ORG, {"group_id": group_id}) is None:
            return False
        await _delete_where(self._h, _MEMBERSHIP, {"group_id": group_id}, "id")
        # bot 可见性（org scope）在管理库,由 claw_manager 侧自行清理（跨库,不在此处理）。
        await self._h.delete(_ORG, {"group_id": group_id})
        _log.info("[IAM] org.delete", group_id=group_id)
        return True

    async def list_members(self, group_id: str) -> list[dict[str, Any]]:
        """列出某组织的用户；``__none__`` = 无任何组织绑定的用户。"""
        if group_id == NO_ORG_GROUP_ID:
            users = await self._h.list_records(_APP_USER, {}, limit=_CAP, offset=0)
            out: list[dict[str, Any]] = []
            for u in users:
                mem = await self._h.list_records(_MEMBERSHIP, {"user_id": _g(u, "user_id")}, limit=1, offset=0)
                if not mem:
                    out.append(_user_out(u))
            return out
        rows = await self._h.list_records(_MEMBERSHIP, {"group_id": group_id}, limit=_CAP, offset=0)
        members: list[dict[str, Any]] = []
        for r in rows:
            u = await self._h.get(_APP_USER, {"user_id": _g(r, "user_id")})
            if u is not None:
                members.append(_user_out(u))
        return members

    async def add_members(self, group_id: str, user_ids: list[str]) -> list[str]:
        if group_id == NO_ORG_GROUP_ID:
            raise ValueError("cannot add members to the no-org group")
        if await self._h.get(_ORG, {"group_id": group_id}) is None:
            raise ValueError(f"org not found: {group_id}")
        added: list[str] = []
        now = utc_now()
        for raw in user_ids:
            uid = strip_optional(raw)
            if not uid:
                continue
            if await self._h.get(_APP_USER, {"user_id": uid}) is None:
                raise ValueError(f"user not found: {uid}")
            exists = await self._h.list_records(_MEMBERSHIP, {"user_id": uid, "group_id": group_id}, limit=1, offset=0)
            if exists:
                continue
            await self._h.create(_MEMBERSHIP, {"user_id": uid, "group_id": group_id, "created_at": now})
            added.append(uid)
        _log.info("[IAM] org.add_members", group_id=group_id, added=added)
        return added

    async def remove_member(self, group_id: str, user_id: str) -> bool:
        if group_id == NO_ORG_GROUP_ID:
            raise ValueError("cannot remove members from the no-org group")
        await _delete_where(self._h, _MEMBERSHIP, {"user_id": user_id, "group_id": group_id}, "id")
        _log.info("[IAM] org.remove_member", group_id=group_id, user_id=user_id)
        return True


# ======================= 用户 =======================
def _user_out(row: Any) -> dict[str, Any]:
    return {
        "user_id": _g(row, "user_id"),
        "display_name": _g(row, "display_name"),
        "is_admin": bool(_g(row, "is_admin")),
        "status": _g(row, "status"),
        "created_at": iso_datetime(_g(row, "created_at")),
        "updated_at": iso_datetime(_g(row, "updated_at")),
    }


class UserService:
    def __init__(self, handler: DBHandler) -> None:
        self._h = handler

    async def list(self, page: int = 1, page_size: int = 50) -> dict[str, Any]:
        return await _paginate(self._h, _APP_USER, {}, _user_out, _PageOpts(page, page_size, [("created_at", False)]))

    async def get(self, user_id: str) -> dict[str, Any] | None:
        row = await self._h.get(_APP_USER, {"user_id": user_id})
        if row is None:
            return None
        out = _user_out(row)
        out["group_ids"] = await self.list_org_ids(user_id)
        return out

    async def create(
        self, *, user_id: str | None, display_name: str, is_admin: bool, username: str, password: str
    ) -> dict[str, Any]:
        username = username.strip()
        uid = strip_optional(user_id) or username
        if await self._h.get(_APP_USER, {"user_id": uid}) is not None:
            raise ValueError(f"user already exists: {uid}")
        dup = await self._h.list_records(
            _AUTH_IDENTITY, {"provider": _LOCAL, "external_subject": username}, limit=1, offset=0,
        )
        if dup:
            raise ValueError(f"username already taken: {username}")
        now = utc_now()
        await self._h.create(
            _APP_USER,
            {"user_id": uid, "display_name": display_name.strip(), "is_admin": is_admin,
             "status": "active", "created_at": now, "updated_at": now},
        )
        await self._h.create(
            _AUTH_IDENTITY,
            {"user_id": uid, "provider": _LOCAL, "external_subject": username,
             "credential": hash_password(password), "created_at": now, "updated_at": now},
        )
        _log.info("[IAM] user.create", user_id=uid, username=username, is_admin=is_admin)
        created = await self.get(uid)
        if created is None:  # pragma: no cover - 刚写入必存在
            raise RuntimeError(f"user just created but missing: {uid}")
        return created

    async def update(
        self, user_id: str, *, display_name: str | None, is_admin: bool | None,
        status: str | None, password: str | None,
    ) -> dict[str, Any] | None:
        if await self._h.get(_APP_USER, {"user_id": user_id}) is None:
            return None
        now = utc_now()
        updates: dict[str, Any] = {"updated_at": now}
        if display_name is not None:
            updates["display_name"] = display_name.strip()
        if is_admin is not None:
            updates["is_admin"] = is_admin
        if status is not None:
            updates["status"] = status.strip()
        await self._h.update(_APP_USER, {"user_id": user_id}, updates)
        if password:
            await self._set_local_password(user_id, password, now)
        _log.info("[IAM] user.update", user_id=user_id,
                  fields=[k for k in updates if k != "updated_at"], password_changed=bool(password))
        return await self.get(user_id)

    async def _set_local_password(self, user_id: str, password: str, now: Any) -> None:
        rows = await self._h.list_records(_AUTH_IDENTITY, {"provider": _LOCAL, "user_id": user_id}, limit=1, offset=0)
        cred = hash_password(password)
        if rows:
            await self._h.update(_AUTH_IDENTITY, {"id": _g(rows[0], "id")}, {"credential": cred, "updated_at": now})
        else:
            await self._h.create(
                _AUTH_IDENTITY,
                {"user_id": user_id, "provider": _LOCAL, "external_subject": user_id,
                 "credential": cred, "created_at": now, "updated_at": now},
            )

    async def delete(self, user_id: str) -> bool:
        if await self._h.get(_APP_USER, {"user_id": user_id}) is None:
            return False
        await _delete_where(self._h, _AUTH_IDENTITY, {"user_id": user_id}, "id")
        await _delete_where(self._h, _AUTH_SESSION, {"user_id": user_id}, "refresh_token")
        await _delete_where(self._h, _MEMBERSHIP, {"user_id": user_id}, "id")
        await self._h.delete(_APP_USER, {"user_id": user_id})
        _log.info("[IAM] user.delete", user_id=user_id)
        return True

    async def list_org_ids(self, user_id: str) -> list[str]:
        rows = await self._h.list_records(_MEMBERSHIP, {"user_id": user_id}, limit=_CAP, offset=0)
        return [str(_g(r, "group_id")) for r in rows]

    async def set_orgs(self, user_id: str, group_ids: list[str]) -> list[str]:
        """整体覆盖该用户的组织绑定（批量）。校验组织存在。"""
        if await self._h.get(_APP_USER, {"user_id": user_id}) is None:
            raise ValueError(f"user not found: {user_id}")
        target: set[str] = set()
        for gid in group_ids:
            g = strip_optional(gid)
            if not g:
                continue
            if await self._h.get(_ORG, {"group_id": g}) is None:
                raise ValueError(f"org not found: {g}")
            target.add(g)
        current = set(await self.list_org_ids(user_id))
        now = utc_now()
        for gid in target - current:
            await self._h.create(_MEMBERSHIP, {"user_id": user_id, "group_id": gid, "created_at": now})
        for gid in current - target:
            await _delete_where(self._h, _MEMBERSHIP, {"user_id": user_id, "group_id": gid}, "id")
        _log.info("[IAM] user.set_orgs", user_id=user_id,
                  added=sorted(target - current), removed=sorted(current - target))
        return sorted(target)
