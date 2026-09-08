# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""租户级装配产物缓存(单飞 + TTL), 消除并发新 session 的重复装配.

实测(第三轮 [AgentPerf] 打点, 30 并发新 session):
- ``_load_enterprise_config`` 每次全量查 Gateway DB 4-6 次(无缓存), 单条净
  耗时 ~15ms, 并发排队放大到 1-3s;
- ``SkillWhitelistSynchronizer.sync`` 持 per-skills-dir 串行锁 + 锁内查 DB,
  并发同样放大到 1-3s。

缓存放 AgentManager 级(= 租户级, 随其 LRU 逐出一起消亡):
- enterprise_config: 按 (group_id, bot_id, user_id) 路由键缓存;
- skill_sync 结果: 按(白名单签名)缓存, 签名未变直接复用, 变更后自然重建。

并发语义: per-key 单飞 —— 第一个 miss 者构建, 后来者等待并复用(double-check),
避免并发 N 个新 session 各自全量查库。

失效: reload_agents_config / refresh_all_enabled_skills_from_db 钩子主动清空,
TTL 1 小时兜底直接改库不走钩子的变更。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Generic, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class _Entry(Generic[T]):
    value: T
    created_at: float


Builder = Callable[[], Awaitable[T]]


def routing_cache_key(request: Any) -> tuple[str, str, str]:
    """从请求提取企业配置路由键 (group_id, bot_id, user_id).

    与 manager_ws_client.core.enterprise_config.loader 的
    ``_routing_field_sources``(params → metadata → metadata.query)优先级保持一致。
    """
    params = getattr(request, "params", None)
    metadata = getattr(request, "metadata", None)

    sources: list[Any] = []
    if isinstance(params, dict):
        sources.append(params)
    if isinstance(metadata, dict):
        sources.append(metadata)
        query = metadata.get("query")
        if isinstance(query, dict):
            sources.append(query)

    def _pick(field: str) -> str:
        for src in sources:
            if isinstance(src, dict):
                value = src.get(field)
                if isinstance(value, (list, tuple)):
                    value = value[0] if value else None
                if value is not None and str(value).strip():
                    return str(value).strip()
        return ""

    return (_pick("group_id"), _pick("bot_id"), _pick("user_id"))


def skill_whitelist_signature(workspace_dir: str, skill_config: Any) -> str:
    """白名单签名: workspace + 条目(id/source/version)集合, 签名未变即无需同步."""
    items = getattr(skill_config, "items_with_source", None) or []
    parts = sorted(
        "{}|{}|{}".format(
            str(getattr(item, "id", "") or ""),
            str(getattr(item, "source", "") or ""),
            str(getattr(item, "version", "") or ""),
        )
        for item in items
    )
    raw = f"{workspace_dir}||" + ";".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


class TenantAssemblyCache:
    """AgentManager(租户)级装配产物缓存: per-key 单飞 + TTL."""

    def __init__(self, ttl_seconds: float = 3600.0) -> None:
        self._ttl = ttl_seconds
        self._entries: dict[tuple, _Entry] = {}
        self._key_locks: dict[tuple, asyncio.Lock] = {}
        self._meta_lock = asyncio.Lock()

    async def _lock_for(self, key: tuple) -> asyncio.Lock:
        async with self._meta_lock:
            lock = self._key_locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._key_locks[key] = lock
            return lock

    def _fresh(self, key: tuple) -> _Entry | None:
        entry = self._entries.get(key)
        if entry is not None and time.monotonic() - entry.created_at <= self._ttl:
            return entry
        return None

    async def get_or_build(self, slot: str, key: Any, builder: Builder) -> tuple[Any, bool]:
        """按 (slot, key) 取缓存; miss 时单飞构建。返回 (value, cache_hit)."""
        cache_key = (slot, key)
        entry = self._fresh(cache_key)
        if entry is not None:
            return entry.value, True
        lock = await self._lock_for(cache_key)
        async with lock:
            entry = self._fresh(cache_key)
            if entry is not None:
                return entry.value, True
            value = await builder()
            self._entries[cache_key] = _Entry(value, time.monotonic())
            logger.info(
                "[AgentPerf] assembly cache: slot=%s key=%s built(后续请求将命中)", slot, key
            )
            return value, False

    async def get_enterprise_config(
        self, routing_key: tuple[str, str, str], builder: Builder
    ) -> tuple[Any, bool]:
        return await self.get_or_build("ent_cfg", routing_key, builder)

    async def get_skill_sync(self, signature: str, builder: Builder) -> tuple[Any, bool]:
        return await self.get_or_build("skill_sync", signature, builder)

    def invalidate(self) -> None:
        """配置 reload/技能热更时清空全部装配缓存."""
        self._entries.clear()
        self._key_locks.clear()
        logger.info("[AgentPerf] assembly cache: invalidated")
