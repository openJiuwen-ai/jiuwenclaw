# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved
"""memory 配置业务逻辑：数据库操作 + Gateway 推送。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.manager_ws_server.server import push_config_op

_MEMORY_CONFIG_TABLE = "memory_config"

_VALID_ENGINES = {"builtin", "external", "both", "none"}
_VALID_MODES = {"local", "cloud"}


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


def _validate_memory_body(body: dict[str, Any]) -> None:
    if not isinstance(body, dict):
        raise ValueError("body must be an object")
    engine = body.get("engine")
    if engine is not None:
        value = str(engine).strip().lower()
        if value and value not in _VALID_ENGINES:
            raise ValueError(f"invalid memory.engine: {engine!r}")
    mode = body.get("mode")
    if mode is not None:
        value = str(mode).strip().lower()
        if value and value not in _VALID_MODES:
            raise ValueError(f"invalid memory.mode: {mode!r}")
    external = body.get("external")
    if external is not None and not isinstance(external, dict):
        raise ValueError("memory.external must be an object")
    forbidden = body.get("forbidden_memory_definition")
    if forbidden is not None and not isinstance(forbidden, dict):
        raise ValueError("memory.forbidden_memory_definition must be an object")


async def push_memory_config_op(
    jiuwenclaw_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """推送 memory 配置变更（``config.memory_config``）。"""
    return await push_config_op(jiuwenclaw_id, {"memory_config": payload})


class MemoryConfigService:
    """Memory 配置服务：封装数据库操作和 Gateway 推送。"""

    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def get(self, jiuwenclaw_id: str) -> dict[str, Any] | None:
        existing = await self._handler.get(
            _MEMORY_CONFIG_TABLE,
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

        _validate_memory_body(body)
        now = utc_now()
        existing = await self._handler.get(
            _MEMORY_CONFIG_TABLE,
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
                _MEMORY_CONFIG_TABLE,
                {"jiuwenclaw_id": jiuwenclaw_id},
                update_data,
            )
            if updated is None:
                raise ValueError("failed to update memory config")
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
            created = await self._handler.create(_MEMORY_CONFIG_TABLE, row_data)
            if created is None:
                raise ValueError("failed to create memory config")
            result = _row_to_dict(created)

        try:
            await push_memory_config_op(
                jiuwenclaw_id,
                {"op": "upsert", "body": result.get("body")},
            )
        except Exception as exc:
            raise ValueError(f"failed to sync to gateway: {exc}") from exc

        return result

    async def delete(self, jiuwenclaw_id: str) -> None:
        existing = await self._handler.get(
            _MEMORY_CONFIG_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id},
        )
        if existing is None:
            raise ValueError("memory config not found")

        try:
            await push_memory_config_op(jiuwenclaw_id, {"op": "delete"})
        except Exception as exc:
            raise ValueError(f"failed to sync to gateway: {exc}") from exc

        deleted = await self._handler.delete(
            _MEMORY_CONFIG_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id},
        )
        if not deleted:
            raise ValueError("failed to delete memory config")
