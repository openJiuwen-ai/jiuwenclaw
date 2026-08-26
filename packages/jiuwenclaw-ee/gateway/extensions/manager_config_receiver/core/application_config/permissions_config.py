# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Permissions 配置：写入 Gateway 本地库并热更新权限引擎。"""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from ...infrastructure.utils import format_ts, utc_now
from ...models.application_config_models import PERMISSIONS_CONFIG_TABLE_DEF

_TABLE = PERMISSIONS_CONFIG_TABLE_DEF.table_name
logger = logging.getLogger(__name__)


def _row_to_dict(obj: Any) -> dict[str, Any]:
    body = getattr(obj, "body", None)
    return {
        "id": getattr(obj, "id"),
        "jiuwenclaw_id": getattr(obj, "jiuwenclaw_id"),
        "body": dict(body) if isinstance(body, dict) else body,
        "source": getattr(obj, "source", "manager"),
        "revision": getattr(obj, "revision", 1),
        "created_at": format_ts(getattr(obj, "created_at", None)),
        "updated_at": format_ts(getattr(obj, "updated_at", None)),
    }


def _apply_permissions(body: dict[str, Any] | None, *, op: str) -> None:
    """热更新权限引擎（与 logging_config._apply_log_levels 同模式：不再读 GDB）。"""
    from jiuwenswarm.agents.harness.common.rails.permissions.config_loader import (
        apply_permissions_config_payload,
    )

    if op == "delete":
        apply_permissions_config_payload({"op": "delete"})
        return
    apply_permissions_config_payload({"body": body} if isinstance(body, dict) else None)


class PermissionsConfigService:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def upsert(
        self,
        jiuwenclaw_id: str,
        *,
        body: dict[str, Any] | None = None,
        source: str = "manager",
        **_extra: Any,
    ) -> dict[str, Any] | None:
        # body 可能在顶层，或业务字段本身就是 body
        if body is None and isinstance(_extra.get("body"), dict):
            body = _extra["body"]
        if body is None:
            # 摊平：除 source 外整包当作 body
            cand = {k: v for k, v in _extra.items() if k != "source"}
            if cand:
                body = cand
        if not isinstance(body, dict):
            raise ValueError("permissions_config.body must be an object for upsert")

        now = utc_now()
        existing = await self._handler.get(_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})
        if existing is not None:
            update_data: dict[str, Any] = {
                "body": body,
                "source": str(source or "manager"),
                "updated_at": now,
                "revision": int(getattr(existing, "revision", 1) or 1) + 1,
            }
            updated = await self._handler.update(
                _TABLE,
                {"jiuwenclaw_id": jiuwenclaw_id},
                update_data,
            )
            result = _row_to_dict(updated) if updated else None
        else:
            row_data = {
                "jiuwenclaw_id": jiuwenclaw_id,
                "body": body,
                "source": str(source or "manager"),
                "revision": 1,
                "created_at": now,
                "updated_at": now,
            }
            created = await self._handler.create(_TABLE, row_data)
            result = _row_to_dict(created) if created else None

        _apply_permissions(body, op="upsert")
        logger.info(
            "[ManagerConfigReceiver] permissions_config hot-reload upsert revision=%s",
            (result or {}).get("revision"),
        )
        return result

    async def delete(self, jiuwenclaw_id: str) -> None:
        await self._handler.delete(_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})
        _apply_permissions(None, op="delete")
        logger.info(
            "[ManagerConfigReceiver] permissions_config deleted jiuwenclaw_id=%s",
            jiuwenclaw_id,
        )
