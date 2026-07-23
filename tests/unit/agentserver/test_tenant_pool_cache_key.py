# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio

import pytest

from jiuwenclaw.agentserver.tenant_catalog_registry import catalog_cache_key
from jiuwenclaw.utils import AsyncLRUCache


def test_catalog_cache_key_uses_tuple_without_delimiter_collision() -> None:
    a = catalog_cache_key("a_b", "c")
    b = catalog_cache_key("a", "b_c")
    assert a == ("a_b", "c")
    assert b == ("a", "b_c")
    assert a != b


@pytest.mark.asyncio
async def test_async_lru_cache_isolates_tuple_keys() -> None:
    cache = AsyncLRUCache()
    await cache.put(("a_b", "c"), "mgr-a")
    await cache.put(("a", "b_c"), "mgr-b")
    assert await cache.get(("a_b", "c")) == "mgr-a"
    assert await cache.get(("a", "b_c")) == "mgr-b"
    keys = await cache.keys()
    assert ("a_b", "c") in keys
    assert ("a", "b_c") in keys


def test_tenant_pool_build_cache_key_tuple() -> None:
    pytest.importorskip("openjiuwen.harness.rails.context_engineering_rail")
    from jiuwenclaw.agentserver.tenant_agent_pool import TenantAgentPool

    a = TenantAgentPool._build_cache_key("a_b", "c")
    b = TenantAgentPool._build_cache_key("a", "b_c")
    assert a == ("a_b", "c")
    assert b == ("a", "b_c")
    assert a != b
