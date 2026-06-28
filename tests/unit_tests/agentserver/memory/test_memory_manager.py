# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import sqlite3
from types import SimpleNamespace

import pytest

from jiuwenswarm.agents.harness.common.memory.manager import (
    EMBEDDING_CACHE_TABLE,
    MemoryIndexManager,
)


@pytest.mark.asyncio
async def test_sync_serializes_concurrent_calls() -> None:
    manager = object.__new__(MemoryIndexManager)
    manager.closed = False
    manager.dirty = False
    manager.settings = SimpleNamespace(sources=())
    manager._sync_lock = asyncio.Lock()

    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    active_calls = 0
    max_active_calls = 0

    async def should_full_reindex() -> bool:
        nonlocal active_calls, max_active_calls
        active_calls += 1
        max_active_calls = max(max_active_calls, active_calls)
        if active_calls == 1:
            first_entered.set()
            await release_first.wait()
        active_calls -= 1
        return False

    manager._should_full_reindex = should_full_reindex

    first = asyncio.create_task(manager.sync(reason="first"))
    await first_entered.wait()
    second = asyncio.create_task(manager.sync(reason="second"))
    await asyncio.sleep(0)
    release_first.set()
    await asyncio.gather(first, second)

    assert max_active_calls == 1


@pytest.mark.asyncio
async def test_get_embedding_leaves_commit_to_outer_transaction() -> None:
    class Provider:
        id = "test-provider"
        model = "test-model"

        async def embed_query(self, text: str) -> list[float]:
            return [1.0, 2.0]

    manager = object.__new__(MemoryIndexManager)
    manager.provider = Provider()
    manager.provider_key = "test-key"
    manager.cache_enabled = True
    manager._event_loop = asyncio.get_running_loop()
    manager.db = sqlite3.connect(":memory:")
    manager.db.execute(
        f"""
        CREATE TABLE {EMBEDDING_CACHE_TABLE} (
            provider TEXT,
            model TEXT,
            provider_key TEXT,
            hash TEXT,
            embedding BLOB,
            dims INTEGER,
            updated_at INTEGER
        )
        """
    )
    manager.db.commit()

    try:
        embedding = await manager._get_embedding("test input")

        assert embedding == [1.0, 2.0]
        assert manager.db.in_transaction
    finally:
        manager.db.rollback()
        manager.db.close()
