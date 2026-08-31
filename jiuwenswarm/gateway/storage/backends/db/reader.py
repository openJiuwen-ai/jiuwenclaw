# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""Gateway 本地库 facade（企业版）。

经存储屏蔽层入口 ``ensure_db_handler`` 获取 foundation ``DBHandler``，CRUD 调
``list_records / create / update``。每网关独立数据库，查询不加实例行级隔离。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from jiuwenswarm.common.utils import logger
from jiuwenswarm.infrastructure.module_importer import import_manager_config_receiver_module

_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PERMISSIONS_CONFIG_TABLE = "permissions_config"

# foundation ``list_records`` 默认 limit=100，此处取全部配置行。
_LIST_LIMIT = 100_000


# --------------------------------------------------------------------------- #
# 标识符 / 行转换辅助
# --------------------------------------------------------------------------- #
def is_safe_ident(name: str) -> bool:
    return bool(_SAFE_IDENT.fullmatch(name or ""))


def _parse_json_string(value: str) -> Any:
    text = value.strip()
    if not text or text[0] not in "{[":
        return value
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value


def row_to_dict(row: Any) -> dict[str, Any]:
    """DB 行 → dict；字符串 JSON 字段自动 parse。

    兼容 dict / SQLAlchemy ``Row``（``_mapping``）/ foundation ORM 实例
    （``__table__`` + ``vars``，过滤 ``_sa_`` 内部态）。
    """
    if isinstance(row, dict):
        items = dict(row)
    else:
        mapping = getattr(row, "_mapping", None)
        if mapping is not None:
            items = dict(mapping)
        else:
            keys_fn = getattr(type(row), "keys", None)
            if callable(keys_fn):
                items = {k: row[k] for k in keys_fn(row)}
            elif hasattr(row, "__table__"):
                items = {k: v for k, v in vars(row).items() if not k.startswith("_sa_")}
            else:
                items = dict(row)
    out: dict[str, Any] = {}
    for key, value in items.items():
        out[key] = _parse_json_string(value) if isinstance(value, str) else value
    return out


def sort_by_order(rows: list[dict[str, Any]], order_by: str) -> list[dict[str, Any]]:
    """内存排序兜底，支持 ``-field`` / ``field DESC`` 语法。"""
    text = order_by.strip()
    if not text:
        return rows

    parts = text.split(None, 1)
    field = parts[0].strip()
    reverse = False
    if len(parts) > 1:
        reverse = parts[1].strip().upper() == "DESC"
    elif field.startswith("-"):
        reverse = True
        field = field[1:].strip()
    if not field:
        return rows

    def _key(row: dict[str, Any]) -> Any:
        value = row.get(field)
        if value is None:
            return 0
        try:
            return int(value)
        except (TypeError, ValueError):
            return value

    return sorted(rows, key=_key, reverse=reverse)


# --------------------------------------------------------------------------- #
# 存储屏蔽层入口
# --------------------------------------------------------------------------- #
async def _handler() -> Any:
    """经 ``ensure_db_handler`` 获取 foundation ``DBHandler``。"""
    db_mod = import_manager_config_receiver_module("infrastructure.db")
    return await db_mod.ensure_db_handler(log_prefix="gateway_db_reader")


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #
async def list_records(
    table: str,
    query: dict[str, Any] | None = None,
    order_by: str = "",
) -> list[dict[str, Any]]:
    """等值 filters 列表查询（每网关独立 DB，不加实例隔离列）。"""
    if not is_safe_ident(table or ""):
        logger.warning("[gateway_db_reader] invalid table name: %r", table)
        return []

    handler = await _handler()
    try:
        rows = await handler.list_records(
            table,
            filters=dict(query or {}),
            limit=_LIST_LIMIT,
            offset=0,
            order_by=(order_by or None),
        )
        return [row_to_dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.error("[gateway_db_reader] list %s failed: %s", table, exc)
        raise


async def upsert_permissions_config(
    body: dict[str, Any],
    *,
    source: str = "runtime_persist",
) -> None:
    """单例行 upsert ``permissions_config``（每网关独立 DB，无行级实例隔离）。"""
    now = datetime.now(timezone.utc).isoformat()
    body_json = json.dumps(body, ensure_ascii=False)

    handler = await _handler()
    rows = await handler.list_records(PERMISSIONS_CONFIG_TABLE, limit=1, offset=0)
    if rows:
        existing = row_to_dict(rows[0])
        row_id = existing.get("id")
        revision = int(existing.get("revision") or 1) + 1
        await handler.update(
            PERMISSIONS_CONFIG_TABLE,
            {"id": row_id},
            {
                "body": body_json,
                "source": source,
                "revision": revision,
                "updated_at": now,
            },
        )
        return
    await handler.create(
        PERMISSIONS_CONFIG_TABLE,
        {
            "body": body_json,
            "source": source,
            "revision": 1,
            "created_at": now,
            "updated_at": now,
        },
    )


__all__ = [
    "PERMISSIONS_CONFIG_TABLE",
    "is_safe_ident",
    "list_records",
    "row_to_dict",
    "sort_by_order",
    "upsert_permissions_config",
]
