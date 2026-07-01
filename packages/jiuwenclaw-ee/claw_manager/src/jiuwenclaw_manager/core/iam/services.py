"""IAM 业务逻辑（claw_manager 侧，管理库）：bot CRUD/可见性 + 当前用户可见 bot 解析。

身份/组织/用户/成员已迁至独立认证服务(jiuwenclaw_identity)；此处只剩**平台配置**侧的
bot 与可见性。用户的组织成员（groups）从 JWT claims 传入，不跨库查身份表。
DBHandler.get/list_records 返回记录对象（属性访问），统一用 ``_g``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.infrastructure.logger import get_logger
from jiuwenclaw_manager.infrastructure.utils import (
    iso_datetime,
    new_uuid4,
    strip_optional,
    utc_now,
)
from jiuwenclaw_manager.models.iam_models import (
    BOT_TABLE_DEF,
    BOT_VISIBILITY_TABLE_DEF,
    NO_ORG_GROUP_ID,
)

_log = get_logger(__name__)

_BOT = BOT_TABLE_DEF.table_name
_BOT_VIS = BOT_VISIBILITY_TABLE_DEF.table_name
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
    rows = await handler.list_records(table, filters, limit=page_size, offset=offset, order_by=opts.order_by)
    total = await handler.count_records(table, filters)
    return {"items": [row_out(r) for r in rows], "total": total, "page": page, "page_size": page_size}


# ======================= bot =======================
def _bot_out(row: Any) -> dict[str, Any]:
    return {
        "bot_id": _g(row, "bot_id"),
        "name": _g(row, "name"),
        "description": _g(row, "description"),
        "status": _g(row, "status"),
        "created_at": iso_datetime(_g(row, "created_at")),
        "updated_at": iso_datetime(_g(row, "updated_at")),
    }


def _vis_out(row: Any) -> dict[str, Any]:
    return {
        "id": _g(row, "id"),
        "bot_id": _g(row, "bot_id"),
        "scope_type": _g(row, "scope_type"),
        "scope_id": _g(row, "scope_id"),
    }


class BotService:
    def __init__(self, handler: DBHandler) -> None:
        self._h = handler

    async def list(self, page: int = 1, page_size: int = 50) -> dict[str, Any]:
        return await _paginate(self._h, _BOT, {}, _bot_out, _PageOpts(page, page_size, [("created_at", False)]))

    async def get(self, bot_id: str) -> dict[str, Any] | None:
        row = await self._h.get(_BOT, {"bot_id": bot_id})
        if row is None:
            return None
        out = _bot_out(row)
        out["visibility"] = await self.list_visibility(bot_id)
        return out

    async def create(self, bot_id: str | None, name: str, description: str | None) -> dict[str, Any]:
        bid = strip_optional(bot_id) or new_uuid4()
        if await self._h.get(_BOT, {"bot_id": bid}) is not None:
            raise ValueError(f"bot already exists: {bid}")
        now = utc_now()
        await self._h.create(
            _BOT,
            {"bot_id": bid, "name": name.strip(), "description": strip_optional(description),
             "status": "active", "created_at": now, "updated_at": now},
        )
        _log.info("[IAM] bot.create", bot_id=bid, name=name.strip())
        created = await self.get(bid)
        if created is None:  # pragma: no cover - 刚写入必存在
            raise RuntimeError(f"bot just created but missing: {bid}")
        return created

    async def update(
        self, bot_id: str, *, name: str | None, description: str | None, status: str | None
    ) -> dict[str, Any] | None:
        if await self._h.get(_BOT, {"bot_id": bot_id}) is None:
            return None
        updates: dict[str, Any] = {"updated_at": utc_now()}
        if name is not None:
            updates["name"] = name.strip()
        if description is not None:
            updates["description"] = strip_optional(description)
        if status is not None:
            updates["status"] = status.strip()
        await self._h.update(_BOT, {"bot_id": bot_id}, updates)
        return await self.get(bot_id)

    async def delete(self, bot_id: str) -> bool:
        if await self._h.get(_BOT, {"bot_id": bot_id}) is None:
            return False
        await _delete_where(self._h, _BOT_VIS, {"bot_id": bot_id}, "id")
        await self._h.delete(_BOT, {"bot_id": bot_id})
        _log.info("[IAM] bot.delete", bot_id=bot_id)
        return True

    async def list_visibility(self, bot_id: str) -> list[dict[str, Any]]:
        rows = await self._h.list_records(_BOT_VIS, {"bot_id": bot_id}, limit=_CAP, offset=0)
        return [_vis_out(r) for r in rows]

    async def set_visibility(self, bot_id: str, scopes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """整体覆盖某 bot 的可见性（批量）。scope_id 引用身份库的 org/user(跨库,不在此校验存在性)。"""
        if await self._h.get(_BOT, {"bot_id": bot_id}) is None:
            raise ValueError(f"bot not found: {bot_id}")
        normalized: set[tuple[str, str]] = set()
        for s in scopes:
            stype = str(s.get("scope_type"))
            sid = strip_optional(s.get("scope_id")) or ""
            if stype == "global":
                sid = ""
            elif stype not in ("org", "user"):
                raise ValueError(f"invalid scope_type: {stype}")
            normalized.add((stype, sid))
        await _delete_where(self._h, _BOT_VIS, {"bot_id": bot_id}, "id")
        now = utc_now()
        for stype, sid in normalized:
            await self._h.create(
                _BOT_VIS,
                {"bot_id": bot_id, "scope_type": stype, "scope_id": sid, "created_at": now},
            )
        _log.info("[IAM] bot.set_visibility", bot_id=bot_id, scopes=sorted(f"{t}:{i}" for t, i in normalized))
        return await self.list_visibility(bot_id)


# ======================= 用户态(当前登录用户自己看的) =======================
class MeService:
    """某组织上下文下,当前用户可见的 bot。用户的 groups 由 JWT claims 传入(不查身份库)。"""

    def __init__(self, handler: DBHandler) -> None:
        self._h = handler

    async def list_visible_bots(
        self, user_id: str, group_id: str, groups: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """可见 bot = global ∪ (org=该组织且本人是其成员) ∪ (user=本人)。

        成员判定用 claims 的 ``groups``（资源服务器不查身份库）。
        """
        member_groups = set(groups or [])
        bot_ids: set[str] = set()
        for r in await self._h.list_records(_BOT_VIS, {"scope_type": "global"}, limit=_CAP, offset=0):
            bot_ids.add(str(_g(r, "bot_id")))
        for r in await self._h.list_records(
            _BOT_VIS, {"scope_type": "user", "scope_id": user_id}, limit=_CAP, offset=0
        ):
            bot_ids.add(str(_g(r, "bot_id")))
        # org 范围:仅当本人确属该组织(或无组织上下文)才纳入,避免越权
        if group_id and (group_id == NO_ORG_GROUP_ID or group_id in member_groups):
            for r in await self._h.list_records(
                _BOT_VIS, {"scope_type": "org", "scope_id": group_id}, limit=_CAP, offset=0
            ):
                bot_ids.add(str(_g(r, "bot_id")))
        out: list[dict[str, Any]] = []
        for bid in bot_ids:
            b = await self._h.get(_BOT, {"bot_id": bid})
            if b is not None and str(_g(b, "status")) == "active":
                out.append(_bot_out(b))
        return out
