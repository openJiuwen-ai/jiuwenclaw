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
    ORG_GATEWAY_TABLE_DEF,
    USER_GATEWAY_TABLE_DEF,
)

_log = get_logger(__name__)

_BOT = BOT_TABLE_DEF.table_name
_BOT_VIS = BOT_VISIBILITY_TABLE_DEF.table_name
_USER_GW = USER_GATEWAY_TABLE_DEF.table_name
_ORG_GW = ORG_GATEWAY_TABLE_DEF.table_name
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
        "jiuwenclaw_id": _g(row, "jiuwenclaw_id"),
        "bot_id": _g(row, "bot_id"),
        "scope_type": _g(row, "scope_type"),
        "scope_id": _g(row, "scope_id"),
    }


async def _ensure_binding(handler: DBHandler, table: str, jiuwenclaw_id: str, id_field: str, entity_id: str) -> None:
    """幂等确保 (jiuwenclaw_id, entity) 绑定存在（bot 授权 org/user 时自动补绑,防"可见但进不来"）。"""
    exists = await handler.get(table, {"jiuwenclaw_id": jiuwenclaw_id, id_field: entity_id})
    if exists is None:
        await handler.create(
            table, {"jiuwenclaw_id": jiuwenclaw_id, id_field: entity_id, "created_at": utc_now()}
        )


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

    async def list_visibility(self, bot_id: str, jiuwenclaw_id: str | None = None) -> list[dict[str, Any]]:
        """某 bot 的可见性行；给了 jiuwenclaw_id 则只看该实例,否则跨所有实例。"""
        filters: dict[str, Any] = {"bot_id": bot_id}
        if jiuwenclaw_id:
            filters["jiuwenclaw_id"] = jiuwenclaw_id
        rows = await self._h.list_records(_BOT_VIS, filters, limit=_CAP, offset=0)
        return [_vis_out(r) for r in rows]

    async def set_visibility(
        self, jiuwenclaw_id: str, bot_id: str, scopes: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """整体覆盖某 bot **在某实例上** 的可见性（批量）。空 scopes = 把该 bot 移出该实例。

        scope_id 引用身份库的 org/user(跨库,不校验存在性)。授 org/user 范围时**自动补绑**
        对应 org_gateway/user_gateway,杜绝"可见但进不来"。
        """
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
            elif not sid:
                raise ValueError(f"scope_id required for scope_type={stype}")
            normalized.add((stype, sid))
        await _delete_where(self._h, _BOT_VIS, {"jiuwenclaw_id": jiuwenclaw_id, "bot_id": bot_id}, "id")
        now = utc_now()
        for stype, sid in normalized:
            await self._h.create(
                _BOT_VIS,
                {"jiuwenclaw_id": jiuwenclaw_id, "bot_id": bot_id,
                 "scope_type": stype, "scope_id": sid, "created_at": now},
            )
            # 写时一致性：授组织/个人 → 确保其已绑该实例（否则准入门会挡住，看不到）。
            if stype == "org":
                await _ensure_binding(self._h, _ORG_GW, jiuwenclaw_id, "group_id", sid)
            elif stype == "user":
                await _ensure_binding(self._h, _USER_GW, jiuwenclaw_id, "user_id", sid)
        _log.info(
            "[IAM] bot.set_visibility", jiuwenclaw_id=jiuwenclaw_id, bot_id=bot_id,
            scopes=sorted(f"{t}:{i}" for t, i in normalized),
        )
        return await self.list_visibility(bot_id, jiuwenclaw_id)

    async def remove_from_instance(self, jiuwenclaw_id: str, bot_id: str) -> bool:
        """把某 bot 移出某实例：删它在该实例的所有可见行（不动 bot 目录本身）。"""
        rows = await self._h.list_records(
            _BOT_VIS, {"jiuwenclaw_id": jiuwenclaw_id, "bot_id": bot_id}, limit=_CAP, offset=0
        )
        for r in rows:
            await self._h.delete(_BOT_VIS, {"id": _g(r, "id")})
        _log.info("[IAM] bot.remove_from_instance", jiuwenclaw_id=jiuwenclaw_id, bot_id=bot_id, rows=len(rows))
        return len(rows) > 0

    async def list_instance_bots(self, jiuwenclaw_id: str) -> list[dict[str, Any]]:
        """某实例上已配置的 bot 列表（含 bot 目录信息 + 该实例上的可见范围）。"""
        rows = await self._h.list_records(_BOT_VIS, {"jiuwenclaw_id": jiuwenclaw_id}, limit=_CAP, offset=0)
        by_bot: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            by_bot.setdefault(str(_g(r, "bot_id")), []).append(
                {"scope_type": _g(r, "scope_type"), "scope_id": _g(r, "scope_id")}
            )
        out: list[dict[str, Any]] = []
        for bid, scopes in by_bot.items():
            b = await self._h.get(_BOT, {"bot_id": bid})
            if b is None:
                continue
            item = _bot_out(b)
            item["scopes"] = scopes
            out.append(item)
        out.sort(key=lambda x: str(x.get("name") or ""))
        return out

    async def list_instances_for(self, bot_ids: list[str]) -> dict[str, list[str]]:
        """反查：一批 bot 各自在哪些实例上有配置（所属实例列，一次聚合防 N+1）。"""
        out: dict[str, list[str]] = {str(b).strip(): [] for b in bot_ids if str(b).strip()}
        if not out:
            return out
        for r in await self._h.list_records(_BOT_VIS, {}, limit=_CAP, offset=0):
            bid = str(_g(r, "bot_id"))
            jid = str(_g(r, "jiuwenclaw_id"))
            if bid in out and jid not in out[bid]:
                out[bid].append(jid)
        return out


# ======================= 用户 ↔ 实例(gateway) 绑定 =======================
class GatewayBindingService:
    """user_gateway / org_gateway 通用绑定服务（表 + 实体 id 字段参数化）。"""

    def __init__(self, handler: DBHandler, table: str, id_field: str) -> None:
        self._h = handler
        self._table = table
        self._id = id_field

    async def list_members(self, jiuwenclaw_id: str) -> list[str]:
        """某实例花名册：绑定到该实例的实体 id 列表。"""
        rows = await self._h.list_records(self._table, {"jiuwenclaw_id": jiuwenclaw_id}, limit=_CAP, offset=0)
        return [str(_g(r, self._id)) for r in rows]

    async def bind(self, jiuwenclaw_id: str, entity_ids: list[str]) -> dict[str, Any]:
        """批量绑定（幂等：已存在的跳过）。用于"添加到实例"与批量导入补绑。"""
        added, skipped = [], []
        for raw in entity_ids:
            eid = str(raw).strip()
            if not eid:
                continue
            if await self._h.get(self._table, {"jiuwenclaw_id": jiuwenclaw_id, self._id: eid}) is not None:
                skipped.append(eid)
                continue
            await _ensure_binding(self._h, self._table, jiuwenclaw_id, self._id, eid)
            added.append(eid)
        _log.info("[IAM] gateway.bind", table=self._table, jiuwenclaw_id=jiuwenclaw_id,
                  added=len(added), skipped=len(skipped))
        return {"added": added, "skipped": skipped}

    async def unbind(self, jiuwenclaw_id: str, entity_ids: list[str]) -> dict[str, Any]:
        """批量解绑（只删绑定关系,不删实体本身）。"""
        removed = []
        for raw in entity_ids:
            eid = str(raw).strip()
            if not eid:
                continue
            rows = await self._h.list_records(
                self._table, {"jiuwenclaw_id": jiuwenclaw_id, self._id: eid}, limit=_CAP, offset=0
            )
            for r in rows:
                await self._h.delete(self._table, {"id": _g(r, "id")})
            if rows:
                removed.append(eid)
        _log.info("[IAM] gateway.unbind", table=self._table, jiuwenclaw_id=jiuwenclaw_id, removed=len(removed))
        return {"removed": removed}

    async def list_instances_for(self, entity_ids: list[str]) -> dict[str, list[str]]:
        """反查：一批实体各自绑定的实例列表（所属实例列/编辑弹窗多选,一次聚合防 N+1）。"""
        out: dict[str, list[str]] = {str(e).strip(): [] for e in entity_ids if str(e).strip()}
        if not out:
            return out
        for r in await self._h.list_records(self._table, {}, limit=_CAP, offset=0):
            eid = str(_g(r, self._id))
            if eid in out:
                out[eid].append(str(_g(r, "jiuwenclaw_id")))
        return out


def user_gateway_service(handler: DBHandler) -> GatewayBindingService:
    return GatewayBindingService(handler, _USER_GW, "user_id")


def org_gateway_service(handler: DBHandler) -> GatewayBindingService:
    return GatewayBindingService(handler, _ORG_GW, "group_id")


# ======================= 用户态(当前登录用户自己看的) =======================
class MeService:
    """某实例 + 某组织上下文下,当前用户可见的 bot。groups 由 JWT claims 传入(不查身份库)。"""

    def __init__(self, handler: DBHandler) -> None:
        self._h = handler

    async def _provisioned(self, jiuwenclaw_id: str, user_id: str, member_groups: set[str]) -> bool:
        """准入门：用户直绑该实例,或其所属某组织绑了该实例。"""
        if await self._h.get(_USER_GW, {"jiuwenclaw_id": jiuwenclaw_id, "user_id": user_id}) is not None:
            return True
        for gid in member_groups:
            if await self._h.get(_ORG_GW, {"jiuwenclaw_id": jiuwenclaw_id, "group_id": gid}) is not None:
                return True
        return False

    async def list_visible_bots(
        self, user_id: str, group_id: str, groups: list[str] | None = None,
        jiuwenclaw_id: str = "",
    ) -> list[dict[str, Any]]:
        """当前实例(jiuwenclaw_id)上,可见 bot = global ∪ (org=该组织且本人是其成员) ∪ (user=本人)。

        先过**准入门**(未绑该实例直接返回空)；成员判定用 claims 的 ``groups``(不查身份库)。
        无 jiuwenclaw_id(如本实例未配置)时返回空,避免跨实例泄露。
        """
        if not jiuwenclaw_id:
            return []
        member_groups = set(groups or [])
        if not await self._provisioned(jiuwenclaw_id, user_id, member_groups):
            return []
        base = {"jiuwenclaw_id": jiuwenclaw_id}
        bot_ids: set[str] = set()
        for r in await self._h.list_records(_BOT_VIS, {**base, "scope_type": "global"}, limit=_CAP, offset=0):
            bot_ids.add(str(_g(r, "bot_id")))
        for r in await self._h.list_records(
            _BOT_VIS, {**base, "scope_type": "user", "scope_id": user_id}, limit=_CAP, offset=0
        ):
            bot_ids.add(str(_g(r, "bot_id")))
        # org 范围:仅当本人确属该组织(或无组织上下文)才纳入,避免越权
        if group_id and (group_id == NO_ORG_GROUP_ID or group_id in member_groups):
            for r in await self._h.list_records(
                _BOT_VIS, {**base, "scope_type": "org", "scope_id": group_id}, limit=_CAP, offset=0
            ):
                bot_ids.add(str(_g(r, "bot_id")))
        out: list[dict[str, Any]] = []
        for bid in bot_ids:
            b = await self._h.get(_BOT, {"bot_id": bid})
            if b is not None and str(_g(b, "status")) == "active":
                out.append(_bot_out(b))
        return out
