# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Gateway 本地库：企业配置读库（继承 ``Database``，进程内单例 + ``jiuwenclaw_id`` 隔离）。"""

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

_INSTANCE_SCOPED_TABLES = frozenset({
    "config_effective_service_policy",
    "config_effective_agent_policy",
    "config_effective_global_policy",
    "config_default_template_mapping",
    "log_masking_rule",
    "model_template",
    "extension_config_template",
    "skill_whitelist_template",
    "service_config_template",
})

_DEFAULT_RELATIVE_ROOT = Path(__file__).resolve().parents[2]


class GatewayDb(Database):
    """Gateway 企业配置读库；进程内仅一个实例持有连接池，``bind`` 只切换 ``jiuwenclaw_id``。"""

    _current: ClassVar[GatewayDb | None] = None

    def __init__(
        self,
        jiuwenclaw_id: str | None,
        *,
        cfg: Settings | None = None,
        relative_root: Path | None = None,
        _with_connection: bool = False,
    ) -> None:
        if _with_connection:
            super().__init__(cfg=cfg, relative_root=relative_root or _DEFAULT_RELATIVE_ROOT)
        self._jiuwenclaw_id = self._normalize_jiuwenclaw_id(jiuwenclaw_id)

    @staticmethod
    def _normalize_jiuwenclaw_id(jiuwenclaw_id: str | None) -> str | None:
        if jiuwenclaw_id is None:
            return None
        normalized = str(jiuwenclaw_id).strip()
        return normalized or None

    @classmethod
    def _ensure_singleton(cls) -> GatewayDb:
        if cls._current is None:
            cls._current = cls(None, _with_connection=True)
        return cls._current

    @property
    def jiuwenclaw_id(self) -> str | None:
        return self._jiuwenclaw_id

    def set_jiuwenclaw_id(self, jiuwenclaw_id: str | None) -> None:
        """更新实例隔离 ID（不新建连接池）。"""
        normalized = self._normalize_jiuwenclaw_id(jiuwenclaw_id)
        if self._jiuwenclaw_id != normalized:
            self._jiuwenclaw_id = normalized

    def clear_jiuwenclaw_id(self) -> None:
        """清空实例隔离 ID。"""
        self._jiuwenclaw_id = None

    @classmethod
    def bind(cls, jiuwenclaw_id: str | None) -> GatewayDb:
        """设置当前进程 ``jiuwenclaw_id``（不新建连接池）。"""
        db = cls._ensure_singleton()
        db.set_jiuwenclaw_id(jiuwenclaw_id)
        return db

    @classmethod
    def current(cls) -> GatewayDb:
        return cls._ensure_singleton()

    @classmethod
    async def release(cls) -> None:
        """断连/注销时释放连接池，并清空 ``jiuwenclaw_id``。"""
        if cls._current is not None:
            await cls._current.close()
            cls._current.clear_jiuwenclaw_id()

    def apply_instance_scope(self, table: str, filters: dict[str, Any]) -> dict[str, Any]:
        """为策略/映射表查询附加 ``jiuwenclaw_id`` 隔离条件。"""
        query = dict(filters)
        if table not in _INSTANCE_SCOPED_TABLES:
            return query
        if self._jiuwenclaw_id:
            query["jiuwenclaw_id"] = self._jiuwenclaw_id
        return query

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
        """列表查询；策略/映射表自动按 ``jiuwenclaw_id`` 隔离。"""
        if table in _INSTANCE_SCOPED_TABLES and not self._jiuwenclaw_id:
            logger.warning(
                "[enterprise_config] list_records skipped: jiuwenclaw_id not bound for table=%s",
                table,
            )
            return []

        query = self.apply_instance_scope(table, dict(filters or {}))

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
