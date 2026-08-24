# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Logging 配置：写入 Gateway 本地库并热更新日志级别。"""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenswarm.common.utils import apply_logging_config_payload

from ...infrastructure.utils import format_ts, utc_now
from ...models.application_config_models import LOGGING_CONFIG_TABLE_DEF

_TABLE = LOGGING_CONFIG_TABLE_DEF.table_name
logger = logging.getLogger(__name__)


def _row_to_dict(obj: Any) -> dict[str, Any]:
    return {
        "id": getattr(obj, "id"),
        "jiuwenclaw_id": getattr(obj, "jiuwenclaw_id"),
        "level": getattr(obj, "level", "INFO"),
        "console_level": getattr(obj, "console_level", None),
        "gateway": getattr(obj, "gateway", None),
        "channel": getattr(obj, "channel", None),
        "agent_server": getattr(obj, "agent_server", None),
        "full": getattr(obj, "full", None),
        "created_at": format_ts(getattr(obj, "created_at", None)),
        "updated_at": format_ts(getattr(obj, "updated_at", None)),
    }


def _apply_log_levels(payload: dict[str, Any]) -> None:
    apply_logging_config_payload(payload)
    logger.info(
        "[ManagerConfigReceiver] logging_config hot-reload level=%s console=%s gateway=%s "
        "channel=%s agent_server=%s full=%s",
        payload.get("level"),
        payload.get("console_level"),
        payload.get("gateway"),
        payload.get("channel"),
        payload.get("agent_server"),
        payload.get("full"),
    )


class LoggingConfigService:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def upsert(
        self,
        jiuwenclaw_id: str,
        *,
        level: str = "INFO",
        console_level: str | None = None,
        gateway: str | None = None,
        channel: str | None = None,
        agent_server: str | None = None,
        full: str | None = None,
        **_extra: Any,
    ) -> dict[str, Any] | None:
        existing = await self._handler.get(_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})
        now = utc_now()

        if existing is not None:
            update_data: dict[str, Any] = {"level": level, "updated_at": now}
            if console_level is not None:
                update_data["console_level"] = console_level
            if gateway is not None:
                update_data["gateway"] = gateway
            if channel is not None:
                update_data["channel"] = channel
            if agent_server is not None:
                update_data["agent_server"] = agent_server
            if full is not None:
                update_data["full"] = full
            updated = await self._handler.update(
                _TABLE,
                {"jiuwenclaw_id": jiuwenclaw_id},
                update_data,
            )
            result = _row_to_dict(updated) if updated else None
        else:
            row_data = {
                "jiuwenclaw_id": jiuwenclaw_id,
                "level": level,
                "console_level": console_level,
                "gateway": gateway,
                "channel": channel,
                "agent_server": agent_server,
                "full": full,
                "created_at": now,
                "updated_at": now,
            }
            created = await self._handler.create(_TABLE, row_data)
            result = _row_to_dict(created) if created else None

        _apply_log_levels(
            {
                "level": level,
                "console_level": console_level,
                "gateway": gateway,
                "channel": channel,
                "agent_server": agent_server,
                "full": full,
            }
        )
        return result

    async def delete(self, jiuwenclaw_id: str) -> None:
        await self._handler.delete(_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})
        apply_logging_config_payload({"op": "delete"})
        logger.info(
            "[ManagerConfigReceiver] logging_config deleted jiuwenclaw_id=%s",
            jiuwenclaw_id,
        )
