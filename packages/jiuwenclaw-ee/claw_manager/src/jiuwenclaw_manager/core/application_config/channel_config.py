# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved
"""Channel 配置业务逻辑：数据库操作 + Gateway 推送。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.manager_ws_server import ManagerWsServer
from jiuwenclaw_manager.manager_ws_server.server import push_config_op

_CHANNEL_CONFIG_TABLE = "channel_config"


def _format_ts(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _row_to_dict(obj: Any) -> dict[str, Any]:
    raw_cfg = getattr(obj, "config", None)
    config = dict(raw_cfg) if isinstance(raw_cfg, dict) else raw_cfg
    return {
        "id": getattr(obj, "id"),
        "channel_id": getattr(obj, "channel_id"),
        "channel_name": getattr(obj, "channel_name"),
        "channel_type": getattr(obj, "channel_type"),
        "bot_id": getattr(obj, "bot_id"),
        "config": config,
        "status": str(getattr(obj, "status")),
        "created_at": _format_ts(getattr(obj, "created_at", None)),
        "updated_at": _format_ts(getattr(obj, "updated_at", None)),
    }


async def push_channel_config_op(
    jiuwenclaw_id: str,
    op: str,
    *,
    channel: dict[str, Any] | None = None,
    channel_id: str | None = None,
) -> dict[str, Any]:
    """推送 channel 配置变更（``config.channel_config``），返回 config.ack payload。"""
    payload: dict[str, Any] = {"op": op}
    if channel is not None:
        payload["channel"] = channel
    if channel_id is not None:
        payload["channel_id"] = channel_id
    return await push_config_op(jiuwenclaw_id, {"channel_config": payload})


class ChannelConfigService:
    """Channel 配置服务类：封装数据库操作和 Gateway 推送逻辑。"""

    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def register(
        self,
        jiuwenclaw_id: str,
        channel_id: str,
        channel_name: str,
        channel_type: str,
        bot_id: str,
        config: dict | None = None,
        status: str = "active",
    ) -> dict[str, Any]:
        """注册新的 Channel 配置。"""
        from jiuwenclaw_manager.infrastructure.utils import utc_now

        channel_id = channel_id.strip()
        if not channel_id:
            raise ValueError("channel_id is required")

        existing = await self._handler.get(
            _CHANNEL_CONFIG_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id, "channel_id": channel_id},
        )
        if existing is not None:
            raise ValueError("channel_id already exists")

        now = utc_now()
        row_data = {
            "jiuwenclaw_id": jiuwenclaw_id,
            "channel_id": channel_id,
            "channel_name": channel_name.strip(),
            "channel_type": channel_type.strip(),
            "bot_id": bot_id.strip(),
            "config": config,
            "status": status.strip(),
            "created_at": now,
            "updated_at": now,
        }

        created = await self._handler.create(_CHANNEL_CONFIG_TABLE, row_data)
        if created is None:
            raise ValueError("failed to register channel")

        try:
            await push_channel_config_op(
                jiuwenclaw_id,
                "create",
                channel={
                    "channel_id": channel_id,
                    "channel_name": row_data["channel_name"],
                    "channel_type": row_data["channel_type"],
                    "bot_id": row_data["bot_id"],
                    "config": row_data["config"],
                    "status": row_data["status"],
                    "created_at": _format_ts(now),
                    "updated_at": _format_ts(now),
                },
            )
        except Exception as exc:
            await self._handler.delete(
                _CHANNEL_CONFIG_TABLE,
                {"jiuwenclaw_id": jiuwenclaw_id, "channel_id": channel_id},
            )
            raise ValueError(f"failed to sync to gateway: {exc}") from exc

        return {
            "id": created.id,
            "channel_id": created.channel_id,
            "created_at": _format_ts(created.created_at),
        }

    async def list(
        self,
        jiuwenclaw_id: str,
        channel_type: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """查询 Channel 列表。"""
        filters: dict[str, Any] = {"jiuwenclaw_id": jiuwenclaw_id}
        if channel_type:
            filters["channel_type"] = channel_type.strip()
        if status:
            filters["status"] = status.strip()

        rows = await self._handler.list_records(_CHANNEL_CONFIG_TABLE, filters)
        items = [_row_to_dict(row) for row in rows]

        return {"items": items}

    async def activate(self, jiuwenclaw_id: str, channel_id: str) -> dict[str, Any]:
        """激活 Channel（上线）。"""
        from jiuwenclaw_manager.infrastructure.utils import utc_now

        channel_id = channel_id.strip()
        existing = await self._handler.get(
            _CHANNEL_CONFIG_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id, "channel_id": channel_id},
        )
        if existing is None:
            raise ValueError("channel not found")

        now = utc_now()
        updated = await self._handler.update(
            _CHANNEL_CONFIG_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id, "channel_id": channel_id},
            {"status": "active", "updated_at": now},
        )
        if updated is None:
            raise ValueError("failed to update channel status")

        try:
            await push_channel_config_op(jiuwenclaw_id, "activate", channel_id=channel_id)
        except Exception as exc:
            raise ValueError(f"failed to sync to gateway: {exc}") from exc

        return {"channel_id": channel_id, "status": "active"}

    async def deactivate(
        self,
        jiuwenclaw_id: str,
        channel_id: str,
        graceful: bool = True,
        timeout: int = 30,
    ) -> dict[str, Any]:
        """停用 Channel（下线）。"""
        from jiuwenclaw_manager.infrastructure.utils import utc_now

        channel_id = channel_id.strip()
        existing = await self._handler.get(
            _CHANNEL_CONFIG_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id, "channel_id": channel_id},
        )
        if existing is None:
            raise ValueError("channel not found")

        now = utc_now()
        updated = await self._handler.update(
            _CHANNEL_CONFIG_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id, "channel_id": channel_id},
            {"status": "inactive", "updated_at": now},
        )
        if updated is None:
            raise ValueError("failed to update channel status")

        try:
            await push_channel_config_op(jiuwenclaw_id, "deactivate", channel_id=channel_id)
        except Exception as exc:
            raise ValueError(f"failed to sync to gateway: {exc}") from exc

        return {"channel_id": channel_id, "status": "inactive"}

    async def delete(self, jiuwenclaw_id: str, channel_id: str) -> None:
        """删除 Channel 配置。"""
        channel_id = channel_id.strip()
        existing = await self._handler.get(
            _CHANNEL_CONFIG_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id, "channel_id": channel_id},
        )
        if existing is None:
            raise ValueError("channel not found")

        try:
            await push_channel_config_op(jiuwenclaw_id, "delete", channel_id=channel_id)
        except Exception as exc:
            raise ValueError(f"failed to sync to gateway: {exc}") from exc

        deleted = await self._handler.delete(
            _CHANNEL_CONFIG_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id, "channel_id": channel_id},
        )
        if not deleted:
            raise ValueError("failed to delete channel")
