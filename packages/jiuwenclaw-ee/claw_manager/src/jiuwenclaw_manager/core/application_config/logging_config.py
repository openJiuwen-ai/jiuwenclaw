# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved
"""Logging 配置业务逻辑：数据库操作 + Gateway 推送。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.manager_ws_server.server import push_config_op

_LOGGING_CONFIG_TABLE = "logging_config"

_VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "NOTSET"})


def _format_ts(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _validate_level(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip().upper()
    if normalized not in _VALID_LOG_LEVELS:
        raise ValueError(f"invalid {field_name}: {value!r} (valid: {sorted(_VALID_LOG_LEVELS)})")
    return normalized


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
        "created_at": _format_ts(getattr(obj, "created_at", None)),
        "updated_at": _format_ts(getattr(obj, "updated_at", None)),
    }


async def push_logging_config_op(
    jiuwenclaw_id: str,
    levels: dict[str, Any],
) -> dict[str, Any]:
    """推送 logging 配置变更（``config.logging_config``），返回 config.ack payload。"""
    return await push_config_op(jiuwenclaw_id, {"logging_config": levels})


class LoggingConfigService:
    """Logging 配置服务类：封装数据库操作和 Gateway 推送逻辑。"""

    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def get(self, jiuwenclaw_id: str) -> dict[str, Any] | None:
        existing = await self._handler.get(
            _LOGGING_CONFIG_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id},
        )
        if existing is None:
            return None
        return _row_to_dict(existing)

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
    ) -> dict[str, Any]:
        from jiuwenclaw_manager.infrastructure.utils import utc_now

        level = _validate_level(level, "level") or "INFO"
        console_level = _validate_level(console_level, "console_level")
        gateway = _validate_level(gateway, "gateway")
        channel = _validate_level(channel, "channel")
        agent_server = _validate_level(agent_server, "agent_server")
        full = _validate_level(full, "full")

        now = utc_now()
        existing = await self._handler.get(
            _LOGGING_CONFIG_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id},
        )

        if existing is not None:
            update_data: dict[str, Any] = {
                "level": level,
                "updated_at": now,
            }
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
                _LOGGING_CONFIG_TABLE,
                {"jiuwenclaw_id": jiuwenclaw_id},
                update_data,
            )
            if updated is None:
                raise ValueError("failed to update logging config")

            result = _row_to_dict(updated)
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
            created = await self._handler.create(_LOGGING_CONFIG_TABLE, row_data)
            if created is None:
                raise ValueError("failed to create logging config")

            result = _row_to_dict(created)

        try:
            await push_logging_config_op(
                jiuwenclaw_id,
                {
                    "op": "upsert",
                    "level": result["level"],
                    "console_level": result.get("console_level"),
                    "gateway": result.get("gateway"),
                    "channel": result.get("channel"),
                    "agent_server": result.get("agent_server"),
                    "full": result.get("full"),
                },
            )
        except Exception as exc:
            raise ValueError(f"failed to sync to gateway: {exc}") from exc

        return result

    async def delete(self, jiuwenclaw_id: str) -> None:
        existing = await self._handler.get(
            _LOGGING_CONFIG_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id},
        )
        if existing is None:
            raise ValueError("logging config not found")

        try:
            await push_logging_config_op(
                jiuwenclaw_id,
                {"op": "delete"},
            )
        except Exception as exc:
            raise ValueError(f"failed to sync to gateway: {exc}") from exc

        deleted = await self._handler.delete(
            _LOGGING_CONFIG_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id},
        )
        if not deleted:
            raise ValueError("failed to delete logging config")
