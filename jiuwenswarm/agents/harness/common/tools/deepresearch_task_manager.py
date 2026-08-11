# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Per-tenant DeepResearch task manager pool (stub until full migration)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class DeepResearchTaskManager:
    """Placeholder per-tenant DeepResearch manager."""

    def __init__(self, *, service_id: str = "default", agent_id: str = "default"):
        self.service_id = (service_id or "default").strip() or "default"
        self.agent_id = (agent_id or "default").strip() or "default"

    async def shutdown(self) -> None:
        """Release background tasks when the tenant is evicted."""
        return None


class DeepResearchTaskManagerPool:
    """Process-level pool of per-tenant DeepResearchTaskManager instances."""

    _managers: dict[tuple[str, str], DeepResearchTaskManager] = {}
    _lock = asyncio.Lock()

    @classmethod
    def _normalize_tenant(cls, scope: Any) -> tuple[str, str]:
        tenant = getattr(scope, "tenant", None)
        if callable(tenant):
            sid, aid = tenant()
            return (
                (str(sid or "default").strip() or "default"),
                (str(aid or "default").strip() or "default"),
            )
        if isinstance(scope, (tuple, list)) and len(scope) >= 2:
            return (
                (str(scope[0] or "default").strip() or "default"),
                (str(scope[1] or "default").strip() or "default"),
            )
        sid = getattr(scope, "service_id", None)
        aid = getattr(scope, "agent_id", None)
        if sid is not None or aid is not None:
            return (
                (str(sid or "default").strip() or "default"),
                (str(aid or "default").strip() or "default"),
            )
        return ("default", "default")

    @classmethod
    async def get_or_create(cls, scope: Any) -> DeepResearchTaskManager:
        key = cls._normalize_tenant(scope)
        async with cls._lock:
            mgr = cls._managers.get(key)
            if mgr is None:
                mgr = DeepResearchTaskManager(service_id=key[0], agent_id=key[1])
                cls._managers[key] = mgr
            return mgr

    @classmethod
    async def remove(cls, service_id: str, agent_id: str) -> bool:
        key = (
            (str(service_id or "default").strip() or "default"),
            (str(agent_id or "default").strip() or "default"),
        )
        async with cls._lock:
            mgr = cls._managers.pop(key, None)
        if mgr is None:
            return False
        try:
            await mgr.shutdown()
        except Exception:
            logger.warning(
                "[DeepResearchTaskManagerPool] shutdown failed tenant=(%s,%s)",
                key[0],
                key[1],
                exc_info=True,
            )
        return True

    @classmethod
    def get_or_create_sync(cls, scope: Any) -> DeepResearchTaskManager:
        """Sync helper; prefer ``get_or_create`` in async code."""
        key = cls._normalize_tenant(scope)
        mgr = cls._managers.get(key)
        if mgr is None:
            mgr = DeepResearchTaskManager(service_id=key[0], agent_id=key[1])
            cls._managers[key] = mgr
        return mgr

    @classmethod
    def reset_for_tests(cls) -> None:
        cls._managers.clear()


def get_deepresearch_manager(scope: Any) -> DeepResearchTaskManager:
    """Sync helper; prefer ``DeepResearchTaskManagerPool.get_or_create`` in async code."""
    return DeepResearchTaskManagerPool.get_or_create_sync(scope)


__all__ = [
    "DeepResearchTaskManager",
    "DeepResearchTaskManagerPool",
    "get_deepresearch_manager",
]
