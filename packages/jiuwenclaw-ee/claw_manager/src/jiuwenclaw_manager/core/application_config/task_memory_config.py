# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved
"""task_memory 配置业务逻辑：数据库操作 + Gateway 推送。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.manager_ws_server.server import push_config_op

_TASK_MEMORY_CONFIG_TABLE = "task_memory_config"

_VALID_ALGOS = frozenset({"ACE", "ReasoningBank", "ReMe"})


@dataclass
class TaskMemoryUpsertParams:
    enabled: bool = False
    llm_model: str = ""
    embedding_model: str = ""
    api_key: str = ""
    api_base: str = ""
    retrieval_algo: str | None = None
    summary_algo: str | None = None


def _format_ts(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _validate_algo(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if normalized and normalized not in _VALID_ALGOS:
        raise ValueError(f"invalid {field_name}: {value!r} (valid: {sorted(_VALID_ALGOS)})")
    return normalized or None


def _row_to_dict(obj: Any) -> dict[str, Any]:
    return {
        "id": getattr(obj, "id"),
        "jiuwenclaw_id": getattr(obj, "jiuwenclaw_id"),
        "enabled": getattr(obj, "enabled", False),
        "llm_model": getattr(obj, "llm_model", ""),
        "embedding_model": getattr(obj, "embedding_model", ""),
        "api_key": getattr(obj, "api_key", ""),
        "api_base": getattr(obj, "api_base", ""),
        "retrieval_algo": getattr(obj, "retrieval_algo", ""),
        "summary_algo": getattr(obj, "summary_algo", ""),
        "created_at": _format_ts(getattr(obj, "created_at", None)),
        "updated_at": _format_ts(getattr(obj, "updated_at", None)),
    }


async def push_task_memory_config_op(
    jiuwenclaw_id: str,
    op: str,
    *,
    task_memory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """推送 task_memory 配置变更（``config.task_memory_config``），返回 config.ack payload。"""
    payload: dict[str, Any] = {"op": op}
    if task_memory is not None:
        payload["task_memory"] = task_memory
    return await push_config_op(jiuwenclaw_id, {"task_memory_config": payload})


class TaskMemoryConfigService:
    """TaskMemory 配置服务类：封装数据库操作和 Gateway 推送逻辑。"""

    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def upsert(
        self,
        jiuwenclaw_id: str,
        params: TaskMemoryUpsertParams,
    ) -> dict[str, Any]:
        """创建或更新 TaskMemory 配置。"""
        from jiuwenclaw_manager.infrastructure.utils import utc_now

        enabled = params.enabled
        llm_model = params.llm_model.strip()
        embedding_model = params.embedding_model.strip()
        api_key = params.api_key.strip()
        api_base = params.api_base.strip()
        retrieval_algo = _validate_algo(params.retrieval_algo, "retrieval_algo")
        summary_algo = _validate_algo(params.summary_algo, "summary_algo")

        existing = await self._handler.get(
            _TASK_MEMORY_CONFIG_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id},
        )

        now = utc_now()
        if existing is not None:
            update_data: dict[str, Any] = {
                "enabled": enabled,
                "updated_at": now,
            }
            if llm_model != "":
                update_data["llm_model"] = llm_model
            if embedding_model != "":
                update_data["embedding_model"] = embedding_model
            if api_key != "":
                update_data["api_key"] = api_key
            if api_base != "":
                update_data["api_base"] = api_base
            if retrieval_algo is not None:
                update_data["retrieval_algo"] = retrieval_algo
            if summary_algo is not None:
                update_data["summary_algo"] = summary_algo
            updated = await self._handler.update(
                _TASK_MEMORY_CONFIG_TABLE,
                {"jiuwenclaw_id": jiuwenclaw_id},
                update_data,
            )
            if updated is None:
                raise ValueError("failed to update task_memory config")

            result = _row_to_dict(updated)
        else:
            row_data = {
                "jiuwenclaw_id": jiuwenclaw_id,
                "enabled": enabled,
                "llm_model": llm_model,
                "embedding_model": embedding_model,
                "api_key": api_key,
                "api_base": api_base,
                "retrieval_algo": retrieval_algo or "",
                "summary_algo": summary_algo or "",
                "created_at": now,
                "updated_at": now,
            }

            created = await self._handler.create(_TASK_MEMORY_CONFIG_TABLE, row_data)
            if created is None:
                raise ValueError("failed to create task_memory config")

            result = _row_to_dict(created)

        try:
            await push_task_memory_config_op(
                jiuwenclaw_id,
                "upsert",
                task_memory={
                    "enabled": enabled,
                    "llm_model": llm_model,
                    "embedding_model": embedding_model,
                    "api_key": api_key,
                    "api_base": api_base,
                    "retrieval_algo": retrieval_algo or "",
                    "summary_algo": summary_algo or "",
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
        """获取 TaskMemory 配置。"""
        existing = await self._handler.get(
            _TASK_MEMORY_CONFIG_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id},
        )
        if existing is None:
            raise ValueError("task_memory config not found")

        return _row_to_dict(existing)

    async def delete(self, jiuwenclaw_id: str) -> None:
        """删除 TaskMemory 配置。"""
        existing = await self._handler.get(
            _TASK_MEMORY_CONFIG_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id},
        )
        if existing is None:
            raise ValueError("task_memory config not found")

        try:
            await push_task_memory_config_op(jiuwenclaw_id, "delete")
        except Exception as exc:
            raise ValueError(f"failed to sync to gateway: {exc}") from exc

        deleted = await self._handler.delete(
            _TASK_MEMORY_CONFIG_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id},
        )
        if not deleted:
            raise ValueError("failed to delete task_memory config")
