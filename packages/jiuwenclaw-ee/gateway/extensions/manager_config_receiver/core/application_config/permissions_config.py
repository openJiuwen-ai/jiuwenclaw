# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Permissions 配置：写入 Gateway 本地库并热更新权限引擎。"""

from __future__ import annotations

import logging
from typing import Any

from ...infrastructure.repository_access import require_permissions_repository

logger = logging.getLogger(__name__)


def _document_to_dict(document) -> dict[str, Any]:
    return {
        "body": dict(document.body),
        "source": document.source,
        "revision": document.revision,
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

    async def upsert(
        self,
        *,
        body: dict[str, Any] | None = None,
        source: str = "manager",
        **_extra: Any,
    ) -> dict[str, Any] | None:
        if body is None and isinstance(_extra.get("body"), dict):
            body = _extra["body"]
        if body is None:
            cand = {k: v for k, v in _extra.items() if k != "source"}
            if cand:
                body = cand
        if not isinstance(body, dict):
            raise ValueError("permissions_config.body must be an object for upsert")

        repo = require_permissions_repository()
        saved = await repo.upsert_body(
            body,
            source=str(source or "manager"),
        )
        result = _document_to_dict(saved)
        _apply_permissions(body, op="upsert")
        logger.info(
            "[ManagerConfigReceiver] permissions_config hot-reload upsert revision=%s",
            result.get("revision"),
        )
        return result

    async def delete(self) -> None:
        repo = require_permissions_repository()
        await repo.delete()
        _apply_permissions(None, op="delete")
        logger.info(
            "[ManagerConfigReceiver] permissions_config deleted, reverted to yaml fallback"
        )
