# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
import time

import pytest

from jiuwenclaw.agentserver.tools.web_search.content_cache import (
    CacheEntry,
    WebContentCache,
    get_default_cache,
    normalize_url,
    parse_update_time,
    reset_default_cache_for_tests,
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
    cache = WebContentCache(max_entries=4)
    await cache.put(CacheEntry(url="https://example.com/a", content="hello"))
    entry = await cache.get("https://example.com/a")
    assert entry is not None
    assert entry.content == "hello"


@pytest.mark.asyncio
async def test_cache_get_miss_returns_none():
    cache = WebContentCache()
    assert await cache.get("https://nope.example.com") is None


@pytest.mark.asyncio
async def test_cache_lru_eviction():
    cache = WebContentCache(max_entries=2)
    await cache.put(CacheEntry(url="https://a.com", content="a"))
    await cache.put(CacheEntry(url="https://b.com", content="b"))
    # access a to make it recently used
    await cache.get("https://a.com")
    await cache.put(CacheEntry(url="https://c.com", content="c"))
    # b should be evicted (oldest)
    assert await cache.get("https://b.com") is None
    assert await cache.get("https://a.com") is not None
    assert await cache.get("https://c.com") is not None


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
    await cache.put(
        CacheEntry(url="https://a.com", content="good content")
    )
    await cache.put(
        CacheEntry(url="https://a.com", content="")
    )
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
    cache = WebContentCache(max_entries=100)

    async def write(i):
        await cache.put(CacheEntry(url=f"https://a.com/{i}", content=f"c{i}"))

    await asyncio.gather(*[write(i) for i in range(20)])
    assert len(cache) == 20
    entry = await cache.get("https://a.com/10")
    assert entry is not None and entry.content == "c10"


def test_get_default_cache_singleton():
    reset_default_cache_for_tests()
    c1 = get_default_cache()
    c2 = get_default_cache()
    assert c1 is c2
    reset_default_cache_for_tests()
