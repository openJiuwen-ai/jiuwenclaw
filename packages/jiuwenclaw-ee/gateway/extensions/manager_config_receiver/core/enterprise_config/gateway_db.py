# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Gateway 本地库：企业配置读库（继承 ``Database``，进程内单例）。

每网关独立数据库，列表查询不再做实例行级隔离。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, ClassVar

from openjiuwen_runtime.foundation.log import get_logger

from ...infrastructure.config import Settings
from ...infrastructure.db import Database
from ...infrastructure.utils import format_ts
from .schemas import SLOT_ENTITY_TABLE, TemplateRefSlot

logger = get_logger(__name__)

_DEFAULT_RELATIVE_ROOT = Path(__file__).resolve().parents[2]


class GatewayDb(Database):
    """Gateway 企业配置读库；进程内仅一个实例持有连接池。"""

    _current: ClassVar[GatewayDb | None] = None

    def __init__(
        self,
        *,
        cfg: Settings | None = None,
        relative_root: Path | None = None,
        _with_connection: bool = False,
    ) -> None:
        if _with_connection:
            super().__init__(cfg=cfg, relative_root=relative_root or _DEFAULT_RELATIVE_ROOT)

    @classmethod
    def _ensure_singleton(cls) -> GatewayDb:
        if cls._current is None:
            cls._current = cls(_with_connection=True)
        return cls._current

    @classmethod
    def bind(cls, *_args: Any, **_kwargs: Any) -> GatewayDb:
        """兼容旧调用：返回进程内单例（忽略历史实例 id 参数）。"""
        return cls._ensure_singleton()

    @classmethod
    def current(cls) -> GatewayDb:
        return cls._ensure_singleton()

    @classmethod
    async def release(cls) -> None:
        """断连/注销时释放连接池。"""
        if cls._current is not None:
            await cls._current.close()
            cls._current = None

    async def fetch_template_by_slot(
        self,
        slot: str,
        template_id: str,
    ) -> dict[str, Any] | None:
        """按 ``template_ref`` 槽位与 ``template_id`` 从 Gateway 库加载一条启用中的模板行。"""
        try:
            slot_key = TemplateRefSlot(slot)
        except ValueError as exc:
            raise ValueError(
                f"unknown template_ref slot {slot!r} "
                f"(known: {[s.value for s in TemplateRefSlot]})"
            ) from exc
        table = SLOT_ENTITY_TABLE[slot_key]
        ref = str(template_id or "").strip()
        if not ref:
            return None
        filters: dict[str, Any] = {"enabled": True, "template_id": ref}
        rows = await self.list_records(table, filters=filters)
        return rows[0] if rows else None

    async def list_records(
        self,
        table: str,
        *,
        filters: dict[str, Any] | None = None,
        order_by: str | list[tuple[str, bool]] = "",
    ) -> list[dict[str, Any]]:
        """列表查询（每网关独立 DB，不加实例隔离列）。"""
        query = dict(filters or {})

        try:
            handler = await self.ensure_ready(log_prefix="enterprise_config")
            rows = await handler.list_records(
                table, query, limit=10_000, offset=0, order_by=order_by,
            )
            return [_row_to_dict(r) for r in rows]
        except Exception as exc:
            logger.warning("[enterprise_config] query %s failed: %s", table, exc)
            return []


def _parse_json_string(value: str) -> Any:
    """若字符串形如 JSON 对象/数组则解析，否则原样返回。"""
    text = value.strip()
    if not text or text[0] not in "{[":
        return value
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value


def _row_to_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        out = dict(row)
    elif hasattr(row, "model_dump"):
        out = row.model_dump(mode="json")
    else:
        field_names = getattr(row, "__dataclass_fields__", None) or getattr(
            row, "__annotations__", None
        )
        if not field_names:
            field_names = vars(row)
        out = {k: getattr(row, k) for k in field_names if not k.startswith("_sa_")}

    out = {k: v for k, v in out.items() if not k.startswith("_sa_")}

    for key, value in list(out.items()):
        if isinstance(value, (datetime, date)):
            out[key] = format_ts(value)
        elif isinstance(value, str):
            parsed = _parse_json_string(value)
            if parsed is not value:
                out[key] = parsed
        elif hasattr(value, "to_dict") and callable(getattr(value, "to_dict")):
            out[key] = value.to_dict()
    return out


__all__ = ("GatewayDb",)
