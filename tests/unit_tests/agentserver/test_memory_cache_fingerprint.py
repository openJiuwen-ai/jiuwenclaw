# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenclaw.agentserver.memory.cache_registry import (
    acquire_memory_cache_session,
    bind_memory_cache_fingerprint,
    build_memory_cache_key,
    clear_memory_cache_registry,
    get_bound_memory_cache_fingerprint,
    get_memory_cache_ref_count,
    release_memory_cache_session,
    reset_memory_cache_fingerprint,
)
from jiuwenclaw.agentserver.memory.manager import INDEX_CACHE, build_index_cache_key
from jiuwenclaw.agentserver.reload_result import memory_cache_fingerprint


@pytest.fixture(autouse=True)
def _reset_registry():
    INDEX_CACHE.clear()
    clear_memory_cache_registry()
    yield
    INDEX_CACHE.clear()
    clear_memory_cache_registry()


def test_memory_cache_fingerprint_changes_with_embed():
    embed = {"embed_model": "m", "embed_api_key": "k", "embed_base_url": "u"}
    old = {"memory": {"engine": "builtin"}, "embed": {**embed, "embed_model": "a"}}
    new = {"memory": {"engine": "builtin"}, "embed": {**embed, "embed_model": "b"}}
    assert memory_cache_fingerprint(old) != memory_cache_fingerprint(new)


def test_memory_cache_fingerprint_changes_with_engine():
    embed = {"embed_model": "m", "embed_api_key": "k", "embed_base_url": "u"}
    local = {"memory": {"engine": "builtin"}, "embed": embed}
    wiki = {"memory": {"engine": "both"}, "embed": embed}
    assert memory_cache_fingerprint(local) != memory_cache_fingerprint(wiki)


@pytest.mark.asyncio
async def test_acquire_same_fingerprint_increments_ref():
    fp = "abc123"
    await acquire_memory_cache_session("s1", "default", "/ws", fp)
    await acquire_memory_cache_session("s2", "default", "/ws", fp)
    cache_key = build_memory_cache_key("default", "/ws", fp)
    assert get_memory_cache_ref_count(cache_key) == 2
    assert len(INDEX_CACHE) == 0


@pytest.mark.asyncio
async def test_release_closes_manager_when_last_ref():
    fp = "abc123"
    cache_key = build_index_cache_key("default", "/ws", fp)
    manager = MagicMock()
    manager.closed = False
    manager.close = AsyncMock()
    INDEX_CACHE[cache_key] = manager

    await acquire_memory_cache_session("s1", "default", "/ws", fp)
    await release_memory_cache_session("s1")

    assert cache_key not in INDEX_CACHE
    manager.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_different_fingerprints_coexist():
    fp1 = "1111111111111111"
    fp2 = "2222222222222222"
    key1 = build_index_cache_key("default", "/ws", fp1)
    key2 = build_index_cache_key("default", "/ws", fp2)

    mgr1 = MagicMock()
    mgr1.closed = False
    mgr1.close = AsyncMock()
    mgr2 = MagicMock()
    mgr2.closed = False
    mgr2.close = AsyncMock()
    INDEX_CACHE[key1] = mgr1
    INDEX_CACHE[key2] = mgr2

    await acquire_memory_cache_session("s1", "default", "/ws", fp1)
    await acquire_memory_cache_session("s2", "default", "/ws", fp2)

    await release_memory_cache_session("s1")
    assert key1 not in INDEX_CACHE
    assert key2 in INDEX_CACHE

    await release_memory_cache_session("s2")
    assert key2 not in INDEX_CACHE


def test_contextvar_bind_and_reset():
    token = bind_memory_cache_fingerprint("fp-test")
    try:
        assert get_bound_memory_cache_fingerprint() == "fp-test"
    finally:
        reset_memory_cache_fingerprint(token)
    assert get_bound_memory_cache_fingerprint() is None


@pytest.mark.asyncio
async def test_acquire_switches_fingerprint_for_same_session():
    fp1 = "aaaaaaaaaaaaaaaa"
    fp2 = "bbbbbbbbbbbbbbbb"
    key1 = build_index_cache_key("default", "/ws", fp1)
    key2 = build_index_cache_key("default", "/ws", fp2)
    mgr1 = MagicMock()
    mgr1.closed = False
    mgr1.close = AsyncMock()
    mgr2 = MagicMock()
    mgr2.closed = False
    mgr2.close = AsyncMock()
    INDEX_CACHE[key1] = mgr1
    INDEX_CACHE[key2] = mgr2

    await acquire_memory_cache_session("s1", "default", "/ws", fp1)
    await acquire_memory_cache_session("s1", "default", "/ws", fp2)

    assert key1 not in INDEX_CACHE
    assert get_memory_cache_ref_count(key2) == 1


@pytest.mark.asyncio
async def test_concurrent_acquire_same_session_is_consistent():
    import asyncio

    fps = [f"fp{i:016d}" for i in range(10)]
    for fp in fps:
        cache_key = build_index_cache_key("default", "/ws", fp)
        manager = MagicMock()
        manager.closed = False
        manager.close = AsyncMock()
        INDEX_CACHE[cache_key] = manager

    await asyncio.gather(
        *[
            acquire_memory_cache_session("s1", "default", "/ws", fp)
            for fp in fps
        ]
    )

    active = [
        build_memory_cache_key("default", "/ws", fp)
        for fp in fps
        if get_memory_cache_ref_count(build_memory_cache_key("default", "/ws", fp)) > 0
    ]
    assert len(active) == 1
    assert get_memory_cache_ref_count(active[0]) == 1


@pytest.mark.asyncio
async def test_clear_memory_manager_cache_closes_unbound_entries():
    from jiuwenclaw.agentserver.memory.manager import clear_memory_manager_cache

    cache_key = build_index_cache_key("default", "/ws", "fp-orphan")
    manager = MagicMock()
    manager.closed = False
    manager.close = AsyncMock()
    manager.workspace_dir = "/ws"
    manager.cache_key = cache_key
    INDEX_CACHE[cache_key] = manager

    await clear_memory_manager_cache()

    assert cache_key not in INDEX_CACHE
    manager.close.assert_awaited_once()
