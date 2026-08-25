# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

POLL_TABLES: tuple[str, ...] = (
    "channel_config",
    "logging_config",
    "log_masking_rule",
)
_POLL_TABLES = frozenset(POLL_TABLES)


def _normalize_updated_at(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        normalized = text.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(normalized)
        except ValueError:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def row_key(table: str, row: dict[str, Any]) -> str:
    if table == "channel_config":
        return str(row.get("channel_id") or "").strip()
    if table == "log_masking_rule":
        return str(row.get("rule_id") or "").strip()
    return str(row.get("jiuwenclaw_id") or row.get("id") or "default").strip()


def row_stamp(row: dict[str, Any]) -> str:
    ts = _normalize_updated_at(row.get("updated_at"))
    return ts.isoformat() if ts is not None else ""


def row_snapshot(table: str, rows: list[dict[str, Any]]) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for row in rows:
        key = row_key(table, row)
        if not key:
            continue
        snapshot[key] = row_stamp(row)
    return snapshot


def _row_to_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    out: dict[str, Any] = {}
    for key in ("id", "jiuwenclaw_id", "updated_at", "created_at", "status"):
        if hasattr(row, key):
            out[key] = getattr(row, key)
    if hasattr(row, "__dict__"):
        for key, value in vars(row).items():
            if not key.startswith("_"):
                out.setdefault(key, value)
    return out


async def _list_records_via_ee_handler(
    table: str,
    jiuwenclaw_id: str,
) -> list[dict[str, Any]] | None:
    try:
        from jiuwenswarm.infrastructure.module_importer import import_manager_ws_client_module

        db_mod = import_manager_ws_client_module("infrastructure.db")
        ensure_db_handler = getattr(db_mod, "ensure_db_handler", None)
        if ensure_db_handler is None:
            return None
        handler = await ensure_db_handler()
        rows = await handler.list_records(table, {"jiuwenclaw_id": jiuwenclaw_id})
        return [_row_to_dict(row) for row in rows]
    except Exception as exc:  # noqa: BLE001
        logger.debug("[ConfigPoll] EE DB handler unavailable for %s: %s", table, exc)
        return None


async def list_table_records(table: str, jiuwenclaw_id: str) -> list[dict[str, Any]]:
    if table not in _POLL_TABLES:
        logger.warning("[ConfigPoll] unsupported table: %s", table)
        return []
    jid = str(jiuwenclaw_id or "").strip()
    if not jid:
        return []

    ee_rows = await _list_records_via_ee_handler(table, jid)
    if ee_rows is not None:
        return ee_rows

    from jiuwenswarm.server.runtime.enterprise_config import gateway_db

    return await gateway_db.list_records(table, filters={"jiuwenclaw_id": jid})
