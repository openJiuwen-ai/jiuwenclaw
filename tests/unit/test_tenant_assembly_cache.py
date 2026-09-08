# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""TenantAssemblyCache 单测: 单飞 / TTL / 失效 / 白名单签名 / 路由键提取."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from jiuwenclaw.agentserver.deep_agent.tenant_assembly import (
    TenantAssemblyCache,
    routing_cache_key,
    skill_whitelist_signature,
)


@pytest.mark.unit
async def test_singleflight_concurrent_misses_build_once() -> None:
    """并发 N 个 miss: builder 只执行一次, 全部拿到同一结果(单飞)."""
    cache = TenantAssemblyCache(ttl_seconds=60)
    calls = 0

    async def builder() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)  # 模拟查库耗时, 让并发全部堆在锁上
        return f"cfg_{calls}"

    results = await asyncio.gather(
        *(cache.get_enterprise_config(("g", "b", "u"), builder) for _ in range(20))
    )
    values = {value for value, _hit in results}
    hits = [hit for _value, hit in results]

    assert calls == 1, f"builder 应只执行一次, 实际 {calls} 次"
    assert values == {"cfg_1"}
    # 第一个是 miss, 其余 19 个等锁后 double-check 命中
    assert hits.count(False) == 1 and hits.count(True) == 19


@pytest.mark.unit
async def test_ttl_expiry_rebuilds() -> None:
    """TTL 过期后重新构建."""
    cache = TenantAssemblyCache(ttl_seconds=0.05)
    calls = 0

    async def builder() -> int:
        nonlocal calls
        calls += 1
        return calls

    v1, hit1 = await cache.get_or_build("slot", "k", builder)
    await asyncio.sleep(0.08)
    v2, hit2 = await cache.get_or_build("slot", "k", builder)
    assert (hit1, hit2) == (False, False)
    assert (v1, v2) == (1, 2)


@pytest.mark.unit
async def test_invalidate_forces_rebuild() -> None:
    """invalidate 后立即重建."""
    cache = TenantAssemblyCache()

    async def builder() -> str:
        return "value"

    await cache.get_or_build("slot", "k", builder)
    cache.invalidate()
    _value, hit = await cache.get_or_build("slot", "k", builder)
    assert hit is False


@pytest.mark.unit
async def test_different_routing_keys_build_separately() -> None:
    """不同 (group, bot, user) 路由键各自构建(企业配置可能按用户差异化)."""
    cache = TenantAssemblyCache()

    async def builder() -> str:
        return "cfg"

    _v1, hit1 = await cache.get_enterprise_config(("g1", "b", "u"), builder)
    _v2, hit2 = await cache.get_enterprise_config(("g2", "b", "u"), builder)
    assert hit1 is False and hit2 is False

    _v3, hit3 = await cache.get_enterprise_config(("g1", "b", "u"), builder)
    assert hit3 is True


def test_skill_signature_changes_with_items() -> None:
    """白名单条目变化 => 签名变化 => 缓存自然失效."""
    def cfg(items):
        return SimpleNamespace(
            items_with_source=[
                SimpleNamespace(id=i, source=s, version=v) for i, s, v in items
            ]
        )

    sig1 = skill_whitelist_signature("/ws", cfg([("a", "src", "1")]))
    sig_same = skill_whitelist_signature("/ws", cfg([("a", "src", "1")]))
    sig_version = skill_whitelist_signature("/ws", cfg([("a", "src", "2")]))
    sig_dir = skill_whitelist_signature("/ws2", cfg([("a", "src", "1")]))

    assert sig1 == sig_same
    assert sig1 != sig_version
    assert sig1 != sig_dir


def test_routing_cache_key_prefers_params_then_metadata() -> None:
    """路由键提取优先级: params → metadata → metadata.query(与 loader 一致)."""
    request = SimpleNamespace(
        params={"group_id": "g1"},
        metadata={"bot_id": "b1", "query": {"user_id": "u1", "bot_id": "ignored"}},
    )
    assert routing_cache_key(request) == ("g1", "b1", "u1")

    empty = SimpleNamespace(params=None, metadata=None)
    assert routing_cache_key(empty) == ("", "", "")
