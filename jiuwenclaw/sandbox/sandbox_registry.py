from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from jiuwenclaw.gateway.db.mysql import SANDBOX_REGISTRY_TABLE, MysqlUtil
from jiuwenclaw.sandbox.claw_api_key import get_claw_api_key

logger = logging.getLogger(__name__)


def _format_created_at(created_at: float | None) -> str:
    ts = created_at if created_at is not None else datetime.now(timezone.utc).timestamp()
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    created = row.get("created_at")
    if isinstance(created, datetime):
        created_value = created.astimezone(timezone.utc).isoformat()
    else:
        created_value = str(created) if created is not None else ""
    return {
        "sandbox_id": str(row.get("sandbox_id") or ""),
        "api_key": str(row.get("api_key") or ""),
        "created_at": created_value,
    }


def register_sandbox_record(
    sandbox_id: str,
    *,
    created_at: float | None = None,
) -> dict[str, str]:
    """向数据库写入沙箱 API Key、sandbox_id 与创建时间。"""
    sid = str(sandbox_id or "").strip()
    if not sid:
        raise ValueError("sandbox_id is required")

    api_key = get_claw_api_key()
    created_value = _format_created_at(created_at)
    table = SANDBOX_REGISTRY_TABLE
    sql = (
        f"INSERT INTO {table} (sandbox_id, api_key, created_at) "
        "VALUES (%s, %s, %s)"
    )
    MysqlUtil.execute(sql, (sid, api_key, created_value))
    logger.info("registered sandbox record: sandbox_id=%s", sid)
    return {
        "sandbox_id": sid,
        "api_key": api_key,
        "created_at": created_value,
    }


def fetch_sandbox_records(sandbox_id: str | None = None) -> list[dict[str, str]]:
    """从数据库查询沙箱信息；未传 sandbox_id 时返回全部记录。"""
    table = SANDBOX_REGISTRY_TABLE
    if sandbox_id:
        sid = str(sandbox_id).strip()
        if not sid:
            raise ValueError("sandbox_id is required")
        sql = (
            f"SELECT sandbox_id, api_key, created_at FROM {table} "
            "WHERE sandbox_id = %s ORDER BY created_at DESC"
        )
        rows = MysqlUtil.execute(sql, (sid,))
    else:
        sql = f"SELECT sandbox_id, api_key, created_at FROM {table} ORDER BY created_at DESC"
        rows = MysqlUtil.execute(sql)
    return [_normalize_row(row) for row in rows]


async def register_sandbox_record_async(
    sandbox_id: str,
    *,
    created_at: float | None = None,
) -> dict[str, str]:
    return await asyncio.to_thread(
        register_sandbox_record,
        sandbox_id,
        created_at=created_at,
    )


async def fetch_sandbox_records_async(sandbox_id: str | None = None) -> list[dict[str, str]]:
    return await asyncio.to_thread(fetch_sandbox_records, sandbox_id)
