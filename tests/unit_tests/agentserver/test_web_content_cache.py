# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
import time

import pytest

from jiuwenclaw.agentserver.tools.web_search.content_cache import (
    AgentCacheRegistry,
    CacheEntry,
    WebContentCache,
    get_agent_cache_registry,
    normalize_url,
    parse_update_time,
    reset_registry_for_tests,
)


def test_normalize_url_strips_fragment_and_lowercases_host():
    assert normalize_url("HTTPS://Example.COM/Path#frag") == "https://example.com/Path"
    assert normalize_url("https://example.com/") == "https://example.com/"
    assert normalize_url("https://example.com/a/b/") == "https://example.com/a/b"
    assert normalize_url("") == ""


def test_normalize_url_keeps_query():
    assert normalize_url("https://example.com/a?x=1#f") == "https://example.com/a?x=1"


@pytest.mark.asyncio
async def test_cache_put_and_get_roundtrip():
    cache = WebContentCache()
    await cache.put(CacheEntry(url="https://example.com/a", content="hello"))
    entry = await cache.get("https://example.com/a")
    assert entry is not None
    assert entry.content == "hello"


@pytest.mark.asyncio
async def test_cache_get_miss_returns_none():
    cache = WebContentCache()
    assert await cache.get("https://nope.example.com") is None


@pytest.mark.asyncio
async def test_cache_put_preserves_update_time_when_new_is_none():
    cache = WebContentCache()
    await cache.put(
        CacheEntry(url="https://a.com", content="old", update_time=1234567890.0)
    )
    await cache.put(
        CacheEntry(url="https://a.com", content="new", update_time=None)
    )
    entry = await cache.get("https://a.com")
    assert entry is not None
    assert entry.content == "new"
    assert entry.update_time == 1234567890.0


@pytest.mark.asyncio
async def test_cache_put_does_not_overwrite_with_empty_content():
    cache = WebContentCache()
    await cache.put(CacheEntry(url="https://a.com", content="good content"))
    await cache.put(CacheEntry(url="https://a.com", content=""))
    entry = await cache.get("https://a.com")
    assert entry is not None
    assert entry.content == "good content"


def test_parse_update_time_iso8601():
    import datetime as _dt

    expected = _dt.datetime.fromisoformat("2026-08-18T10:00:00").timestamp()
    assert abs(parse_update_time("2026-08-18T10:00:00") - expected) < 1.0


def test_parse_update_time_epoch_int():
    assert parse_update_time(1787023625) == 1787023625.0


def test_parse_update_time_invalid_returns_none():
    assert parse_update_time(None) is None
    assert parse_update_time("") is None
    assert parse_update_time("not-a-date") is None


@pytest.mark.asyncio
async def test_cache_concurrent_writes():
    cache = WebContentCache()

    async def write(i):
        await cache.put(CacheEntry(url=f"https://a.com/{i}", content=f"c{i}"))

    await asyncio.gather(*[write(i) for i in range(20)])
    assert len(cache) == 20
    entry = await cache.get("https://a.com/10")
    assert entry is not None and entry.content == "c10"


# ── AgentCacheRegistry tests ──


@pytest.mark.asyncio
async def test_registry_returns_per_agent_cache():
    reset_registry_for_tests()
    registry = get_agent_cache_registry()
    c_a = await registry.get_cache("agent_a")
    c_b = await registry.get_cache("agent_b")
    assert c_a is not c_b
    await c_a.put(CacheEntry(url="https://a.com", content="a"))
    entry_a = await c_a.get("https://a.com")
    entry_b = await c_b.get("https://a.com")
    assert entry_a is not None and entry_a.content == "a"
    assert entry_b is None
    reset_registry_for_tests()


@pytest.mark.asyncio
async def test_registry_same_agent_returns_same_cache():
    reset_registry_for_tests()
    registry = get_agent_cache_registry()
    c1 = await registry.get_cache("agent_a")
    c2 = await registry.get_cache("agent_a")
    assert c1 is c2
    reset_registry_for_tests()


@pytest.mark.asyncio
async def test_registry_evicts_lru_agent_when_capacity_exceeded():
    reset_registry_for_tests()
    registry = AgentCacheRegistry(max_agents=2)
    await registry.get_cache("a1")
    await registry.get_cache("a2")
    # a1 is oldest; accessing a2 makes a1 the LRU candidate
    await registry.get_cache("a2")
    # adding a3 should evict a1
    await registry.get_cache("a3")
    stats = registry.stats()
    assert "a1" not in stats["agent_ids"]
    assert "a2" in stats["agent_ids"]
    assert "a3" in stats["agent_ids"]


@pytest.mark.asyncio
async def test_registry_cleans_inactive_agents():
    registry = AgentCacheRegistry(timeout_seconds=0)
    await registry.get_cache("a1")
    assert len(registry._caches) == 1
    await asyncio.sleep(0.01)
    # accessing a2 triggers cleanup of a1 (timed out)
    await registry.get_cache("a2")
    assert "a1" not in registry._caches
    assert "a2" in registry._caches


@pytest.mark.asyncio
async def test_registry_default_agent_id():
    registry = AgentCacheRegistry()
    c = await registry.get_cache("")
    assert c is not None
    assert "default" in registry._caches
