# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved
"""embed 配置业务逻辑：数据库操作 + Gateway 推送。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.manager_ws_server.server import push_config_op

_EMBED_CONFIG_TABLE = "embed_config"


def _format_ts(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _row_to_dict(obj: Any) -> dict[str, Any]:
    return {
        "id": getattr(obj, "id"),
        "jiuwenclaw_id": getattr(obj, "jiuwenclaw_id"),
        "embed_api_key": getattr(obj, "embed_api_key", ""),
        "embed_base_url": getattr(obj, "embed_base_url", ""),
        "embed_model": getattr(obj, "embed_model", ""),
        "created_at": _format_ts(getattr(obj, "created_at", None)),
        "updated_at": _format_ts(getattr(obj, "updated_at", None)),
    }


async def push_embed_config_op(
    jiuwenclaw_id: str,
    op: str,
    *,
    embed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """推送 embed 配置变更（``config.embed_config``），返回 config.ack payload。"""
    payload: dict[str, Any] = {"op": op}
    if embed is not None:
        payload["embed"] = embed
    return await push_config_op(jiuwenclaw_id, {"embed_config": payload})


class EmbedConfigService:
    """Embed 配置服务类：封装数据库操作和 Gateway 推送逻辑。"""

    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def upsert(
        self,
        jiuwenclaw_id: str,
        embed_api_key: str,
        embed_base_url: str,
        embed_model: str,
    ) -> dict[str, Any]:
        """创建或更新 Embed 配置。"""
        from jiuwenclaw_manager.infrastructure.utils import utc_now

        embed_api_key = embed_api_key.strip()
        embed_base_url = embed_base_url.strip()
        embed_model = embed_model.strip()

        existing = await self._handler.get(
            _EMBED_CONFIG_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id},
        )

        now = utc_now()
        if existing is not None:
            update_data: dict[str, Any] = {
                "updated_at": now,
            }
            if embed_api_key != "":
                update_data["embed_api_key"] = embed_api_key
            if embed_base_url != "":
                update_data["embed_base_url"] = embed_base_url
            if embed_model != "":
                update_data["embed_model"] = embed_model
            updated = await self._handler.update(
                _EMBED_CONFIG_TABLE,
                {"jiuwenclaw_id": jiuwenclaw_id},
                update_data,
            )
            if updated is None:
                raise ValueError("failed to update embed config")

            result = _row_to_dict(updated)
        else:
            row_data = {
                "jiuwenclaw_id": jiuwenclaw_id,
                "embed_api_key": embed_api_key,
                "embed_base_url": embed_base_url,
                "embed_model": embed_model,
                "created_at": now,
                "updated_at": now,
            }

            created = await self._handler.create(_EMBED_CONFIG_TABLE, row_data)
            if created is None:
                raise ValueError("failed to create embed config")

            result = _row_to_dict(created)

        try:
            await push_embed_config_op(
                jiuwenclaw_id,
                "upsert",
                embed={
                    "embed_api_key": embed_api_key,
                    "embed_base_url": embed_base_url,
                    "embed_model": embed_model,
                    "updated_at": _format_ts(now),
                },
            )
        except Exception as exc:
            raise ValueError(f"failed to sync to gateway: {exc}") from exc
        return result

    async def get(
        self,
        jiuwenclaw_id: str,
    ) -> dict[str, Any]:
        """获取 Embed 配置。"""
        existing = await self._handler.get(
            _EMBED_CONFIG_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id},
        )
        if existing is None:
            raise ValueError("embed config not found")

        return _row_to_dict(existing)

    async def delete(self, jiuwenclaw_id: str) -> None:
        """删除 Embed 配置。"""
        existing = await self._handler.get(
            _EMBED_CONFIG_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id},
        )
        if existing is None:
            raise ValueError("embed config not found")

        try:
            await push_embed_config_op(jiuwenclaw_id, "delete")
        except Exception as exc:
            raise ValueError(f"failed to sync to gateway: {exc}") from exc

        deleted = await self._handler.delete(
            _EMBED_CONFIG_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id},
        )
        if not deleted:
            raise ValueError("failed to delete embed config")