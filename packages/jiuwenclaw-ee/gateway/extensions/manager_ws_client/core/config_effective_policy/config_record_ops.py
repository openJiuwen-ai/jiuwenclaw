# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""配置生效策略 / 映射记录的 CRUD 与 bulk sync 公共逻辑。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from ...infrastructure.utils import get_jiuwenclaw_id, utc_now

_LIST_ALL_CAP = 10_000

BuildRowFn = Callable[[dict[str, Any], str, Any], dict[str, Any]]
BeforeCreateFn = Callable[[DBHandler, str, dict[str, Any]], Awaitable[None]]
UpdateRecordFn = Callable[[DBHandler, int, Any], Awaitable[dict[str, Any] | None]]


async def get_row_for_instance(
    handler: DBHandler,
    table: str,
    row_id: int,
) -> Any | None:
    jiuwenclaw_id = get_jiuwenclaw_id()
    if not jiuwenclaw_id:
        return None
    row = await handler.get(table, {"id": row_id})
    if row is None:
        return None
    if getattr(row, "jiuwenclaw_id", None) != jiuwenclaw_id:
        return None
    return row


async def delete_record_for_instance(
    handler: DBHandler,
    table: str,
    row_id: int,
) -> bool:
    existing = await get_row_for_instance(handler, table, row_id)
    if existing is None:
        return False
    return await handler.delete(table, {"id": row_id})


async def create_record(
    handler: DBHandler,
    table: str,
    row_data: dict[str, Any],
    *,
    section: str,
    entity: str = "policy",
) -> dict[str, Any]:
    created = await handler.create(table, row_data)
    new_id = int(getattr(created, "id", 0) or 0)
    if new_id < 1:
        raise ValueError(f"{section}.create: database did not return {entity} id")
    return {"id": new_id}


async def apply_create_from_row_builder(
    handler: DBHandler,
    table: str,
    *,
    section: str,
    jiuwenclaw_id: str,
    record: Any,
    build_row: BuildRowFn,
    record_label: str = "policy",
    entity: str = "policy",
    before_create: BeforeCreateFn | None = None,
) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ValueError(f"{section}.create requires {record_label} object")
    if before_create is not None:
        await before_create(handler, jiuwenclaw_id, record)
    row_data = build_row(record, jiuwenclaw_id, utc_now())
    return await create_record(
        handler,
        table,
        row_data,
        section=section,
        entity=entity,
    )


async def apply_update_by_id(
    handler: DBHandler,
    *,
    section: str,
    row_id: Any,
    updates: Any,
    update_record: UpdateRecordFn,
    not_found_message: str,
) -> None:
    if row_id is None:
        raise ValueError(f"{section}.update requires id")
    if not isinstance(updates, dict) or not updates:
        raise ValueError(f"{section}.update requires non-empty updates")
    row = await update_record(handler, int(row_id), updates)
    if row is None:
        raise ValueError(not_found_message)


async def apply_delete_by_id(
    handler: DBHandler,
    *,
    section: str,
    table: str,
    row_id: Any,
) -> None:
    if row_id is None:
        raise ValueError(f"{section}.delete requires id")
    await delete_record_for_instance(handler, table, int(row_id))


async def sync_records_by_policy_id(
    handler: DBHandler,
    table: str,
    records: list[dict[str, Any]],
    *,
    jiuwenclaw_id: str,
    build_row: BuildRowFn,
) -> dict[str, int]:
    """按 ``policy_id`` upsert，并删除本地多余行。"""
    incoming_ids: set[str] = set()
    synced = 0
    now = utc_now()
    for item in records:
        if not isinstance(item, dict):
            raise ValueError(f"{table}.sync records must be objects")
        policy_id = str(item.get("policy_id") or "").strip()
        if not policy_id:
            raise ValueError(f"{table}.sync record missing policy_id")
        incoming_ids.add(policy_id)

        existing_rows = await handler.list_records(
            table,
            {"jiuwenclaw_id": jiuwenclaw_id, "policy_id": policy_id},
            limit=1,
            offset=0,
        )
        existing = existing_rows[0] if existing_rows else None
        row_data = build_row(item, jiuwenclaw_id, now)
        if existing is None:
            raw_id = item.get("id")
            if raw_id is not None:
                row_data["id"] = int(raw_id)
            await handler.create(table, row_data)
        else:
            created_at = getattr(existing, "created_at", None)
            if created_at is not None:
                row_data["created_at"] = created_at
            updates = {
                k: v
                for k, v in row_data.items()
                if k not in ("jiuwenclaw_id", "policy_id", "id")
            }
            updates["updated_at"] = utc_now()
            await handler.update(table, {"id": existing.id}, updates)
        synced += 1

    deleted = 0
    existing_rows = await handler.list_records(
        table,
        {"jiuwenclaw_id": jiuwenclaw_id},
        limit=_LIST_ALL_CAP,
        offset=0,
    )
    for row in existing_rows:
        pid = str(getattr(row, "policy_id", "") or "")
        if pid and pid not in incoming_ids:
            if await handler.delete(table, {"id": row.id}):
                deleted += 1

    return {"synced_count": synced, "deleted_count": deleted}
