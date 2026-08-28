# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""配置生效策略 / 映射记录的 CRUD 与 bulk sync 公共逻辑（EnterpriseRecordRepository）。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from jiuwenswarm.gateway.config.enterprise.repository import EnterpriseRecordRepository

from ...infrastructure.utils import utc_now

_LIST_ALL_CAP = 10_000

BuildRowFn = Callable[[dict[str, Any], str, Any], dict[str, Any]]
BeforeCreateFn = Callable[
    [EnterpriseRecordRepository, str, dict[str, Any]], Awaitable[None]
]
UpdateRecordFn = Callable[
    [EnterpriseRecordRepository, int, Any, str], Awaitable[dict[str, Any] | None]
]


async def get_row_for_instance(
    repo: EnterpriseRecordRepository,
    row_id: int,
) -> dict[str, Any] | None:
    return await repo.get_by_row_id(row_id)


async def delete_record_for_instance(
    repo: EnterpriseRecordRepository,
    row_id: int,
) -> bool:
    return await repo.delete_by_row_id(row_id)


async def create_record(
    repo: EnterpriseRecordRepository,
    row_data: dict[str, Any],
    *,
    section: str,
    entity: str = "policy",
) -> dict[str, Any]:
    created = await repo.create(row_data)
    new_id = int(created.get("id", 0) or 0)
    if new_id < 1:
        raise ValueError(f"{section}.create: database did not return {entity} id")
    return {"id": new_id}


async def apply_create_from_row_builder(
    repo: EnterpriseRecordRepository,
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
        await before_create(repo, jiuwenclaw_id, record)
    row_data = build_row(record, jiuwenclaw_id, utc_now())
    return await create_record(
        repo,
        row_data,
        section=section,
        entity=entity,
    )


async def apply_update_by_id(
    repo: EnterpriseRecordRepository,
    *,
    section: str,
    jiuwenclaw_id: str,
    row_id: Any,
    updates: Any,
    update_record: UpdateRecordFn,
    not_found_message: str,
) -> None:
    if row_id is None:
        raise ValueError(f"{section}.update requires id")
    if not isinstance(updates, dict) or not updates:
        raise ValueError(f"{section}.update requires non-empty updates")
    row = await update_record(repo, int(row_id), updates, jiuwenclaw_id)
    if row is None:
        raise ValueError(not_found_message)


async def apply_delete_by_id(
    repo: EnterpriseRecordRepository,
    *,
    section: str,
    row_id: Any,
) -> None:
    if row_id is None:
        raise ValueError(f"{section}.delete requires id")
    await delete_record_for_instance(repo, int(row_id))


async def sync_records_by_policy_id(
    repo: EnterpriseRecordRepository,
    records: list[dict[str, Any]],
    *,
    jiuwenclaw_id: str,
    build_row: BuildRowFn,
) -> dict[str, int]:
    """按 ``policy_id`` upsert，并删除本实例下不在 incoming 集合中的行。"""
    incoming_ids: set[str] = set()
    synced = 0
    now = utc_now()
    table = repo.store_name
    for item in records:
        if not isinstance(item, dict):
            raise ValueError(f"{table}.sync records must be objects")
        policy_id = str(item.get("policy_id") or "").strip()
        if not policy_id:
            raise ValueError(f"{table}.sync record missing policy_id")
        incoming_ids.add(policy_id)

        existing_rows = await repo.list(
            filters={"policy_id": policy_id},
            limit=1,
        )
        existing = existing_rows[0] if existing_rows else None
        row_data = build_row(item, jiuwenclaw_id, now)
        if existing is None:
            raw_id = item.get("id")
            if raw_id is not None:
                row_data["id"] = int(raw_id)
            await repo.create(row_data)
        else:
            created_at = existing.get("created_at")
            if created_at is not None:
                row_data["created_at"] = created_at
            updates = {
                key: value
                for key, value in row_data.items()
                if key not in ("jiuwenclaw_id", "policy_id", "id")
            }
            updates["updated_at"] = utc_now()
            await repo.update({"policy_id": policy_id}, updates)
        synced += 1

    deleted = 0
    existing_rows = await repo.list(limit=_LIST_ALL_CAP)
    for row in existing_rows:
        pid = str(row.get("policy_id") or "")
        if pid and pid not in incoming_ids:
            if await repo.delete(policy_id=pid):
                deleted += 1

    return {"synced_count": synced, "deleted_count": deleted}
