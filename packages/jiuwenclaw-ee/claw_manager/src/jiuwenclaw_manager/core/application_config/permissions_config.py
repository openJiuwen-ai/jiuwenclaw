# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Permissions 配置业务逻辑：数据库操作 + Gateway 推送。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.manager_ws_server.server import push_config_op

_PERMISSIONS_CONFIG_TABLE = "permissions_config"


def _format_ts(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _row_to_dict(obj: Any) -> dict[str, Any]:
    body = getattr(obj, "body", None)
    return {
        "id": getattr(obj, "id"),
        "jiuwenclaw_id": getattr(obj, "jiuwenclaw_id"),
        "body": dict(body) if isinstance(body, dict) else body,
        "source": getattr(obj, "source", "manager"),
        "revision": getattr(obj, "revision", 1),
        "created_at": _format_ts(getattr(obj, "created_at", None)),
        "updated_at": _format_ts(getattr(obj, "updated_at", None)),
    }


async def push_permissions_config_op(
    jiuwenclaw_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """推送 permissions 配置变更（``config.permissions_config``）。"""
    return await push_config_op(jiuwenclaw_id, {"permissions_config": payload})


class PermissionsConfigService:
    """Permissions 配置服务：封装数据库操作和 Gateway 推送。"""

    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def get(self, jiuwenclaw_id: str) -> dict[str, Any] | None:
        existing = await self._handler.get(
            _PERMISSIONS_CONFIG_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id},
        )
        if existing is None:
            return None
        return _row_to_dict(existing)

    async def upsert(
        self,
        jiuwenclaw_id: str,
        *,
        body: dict[str, Any],
        source: str = "manager",
    ) -> dict[str, Any]:
        from jiuwenclaw_manager.infrastructure.utils import utc_now

        if not isinstance(body, dict):
            raise ValueError("body must be an object")

        now = utc_now()
        existing = await self._handler.get(
            _PERMISSIONS_CONFIG_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id},
        )

        if existing is not None:
            update_data: dict[str, Any] = {
                "body": body,
                "source": source,
                "updated_at": now,
                "revision": int(getattr(existing, "revision", 1) or 1) + 1,
            }
            updated = await self._handler.update(
                _PERMISSIONS_CONFIG_TABLE,
                {"jiuwenclaw_id": jiuwenclaw_id},
                update_data,
            )
            if updated is None:
                raise ValueError("failed to update permissions config")
            result = _row_to_dict(updated)
        else:
            row_data = {
                "jiuwenclaw_id": jiuwenclaw_id,
                "body": body,
                "source": source,
                "revision": 1,
                "created_at": now,
                "updated_at": now,
            }
            created = await self._handler.create(_PERMISSIONS_CONFIG_TABLE, row_data)
            if created is None:
                raise ValueError("failed to create permissions config")
            result = _row_to_dict(created)

        try:
            await push_permissions_config_op(
                jiuwenclaw_id,
                {"op": "upsert", "body": result.get("body")},
            )
        except Exception as exc:
            raise ValueError(f"failed to sync to gateway: {exc}") from exc

        return result

    async def delete(self, jiuwenclaw_id: str) -> None:
        existing = await self._handler.get(
            _PERMISSIONS_CONFIG_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id},
        )
        if existing is None:
            raise ValueError("permissions config not found")

        try:
            await push_permissions_config_op(jiuwenclaw_id, {"op": "delete"})
        except Exception as exc:
            raise ValueError(f"failed to sync to gateway: {exc}") from exc

        deleted = await self._handler.delete(
            _PERMISSIONS_CONFIG_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id},
        )
        if not deleted:
            raise ValueError("failed to delete permissions config")
