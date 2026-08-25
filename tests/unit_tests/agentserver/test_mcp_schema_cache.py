# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Correctness tests for the OfficeClaw MCP schema in-process cache.

These do NOT spawn a real Node process; they monkeypatch the uncached
discovery so the cache logic (key, single-flight, deepcopy, generation
invalidation) is exercised deterministically.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from jiuwenswarm.common import mcp_config

_PARAMS = {
    "command": "node",
    "args": ["/repo/packages/mcp-server/dist/collab.js"],
    "cwd": "/repo",
    "env": {"OFFICE_CLAW_MCP_EXCLUDED_TOOLS": "office_claw_list_tasks"},
}


def _make_tool(name: str) -> dict:
    return {"name": name, "description": f"d-{name}", "input_params": {"type": "object"}}


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIUWENSWARM_MCP_SCHEMA_CACHE", "1")
    mcp_config._clear_office_claw_mcp_schema_cache_for_tests()
    yield
    mcp_config._clear_office_claw_mcp_schema_cache_for_tests()


@pytest.mark.asyncio
async def test_second_call_hits_cache_and_returns_equal_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def _discover(params):
        nonlocal calls
        calls += 1
        return [_make_tool("office_claw_post_message")]

    monkeypatch.setattr(mcp_config, "_list_office_claw_mcp_tools_uncached", _discover)

    first = await mcp_config.list_office_claw_mcp_tools(_PARAMS)
    second = await mcp_config.list_office_claw_mcp_tools(_PARAMS)

    assert calls == 1, "second call must hit the cache, not re-discover"
    assert first == second == [_make_tool("office_claw_post_message")]


@pytest.mark.asyncio
async def test_returned_schema_is_deep_copied_so_callers_cannot_mutate_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mcp_config,
        "_list_office_claw_mcp_tools_uncached",
        AsyncMock(return_value=[_make_tool("office_claw_post_message")]),
    )

    first = await mcp_config.list_office_claw_mcp_tools(_PARAMS)
    first[0]["name"] = "MUTATED"

    second = await mcp_config.list_office_claw_mcp_tools(_PARAMS)
    assert second[0]["name"] == "office_claw_post_message", "cache must not be mutated by callers"


@pytest.mark.asyncio
async def test_invalidate_forces_rediscovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def _discover(params):
        nonlocal calls
        calls += 1
        return [_make_tool(f"tool_{calls}")]

    monkeypatch.setattr(mcp_config, "_list_office_claw_mcp_tools_uncached", _discover)

    await mcp_config.list_office_claw_mcp_tools(_PARAMS)
    await mcp_config.list_office_claw_mcp_tools(_PARAMS)
    assert calls == 1

    mcp_config.invalidate_office_claw_mcp_schema_cache()
    await mcp_config.list_office_claw_mcp_tools(_PARAMS)
    assert calls == 2, "invalidate must drop the cache and force a fresh discovery"


@pytest.mark.asyncio
async def test_disabled_flag_falls_back_to_uncached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIUWENSWARM_MCP_SCHEMA_CACHE", "0")
    calls = 0

    async def _discover(params):
        nonlocal calls
        calls += 1
        return [_make_tool("office_claw_post_message")]

    monkeypatch.setattr(mcp_config, "_list_office_claw_mcp_tools_uncached", _discover)

    await mcp_config.list_office_claw_mcp_tools(_PARAMS)
    await mcp_config.list_office_claw_mcp_tools(_PARAMS)
    assert calls == 2, "with cache disabled every call must discover"


@pytest.mark.asyncio
async def test_concurrent_discovery_coalesces_into_one_inflight_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    releases = asyncio.Event()
    calls = 0

    async def _discover(params):
        nonlocal calls
        calls += 1
        started.set()
        await releases.wait()
        return [_make_tool("office_claw_post_message")]

    monkeypatch.setattr(mcp_config, "_list_office_claw_mcp_tools_uncached", _discover)

    t1 = asyncio.create_task(mcp_config.list_office_claw_mcp_tools(_PARAMS))
    await started.wait()
    t2 = asyncio.create_task(mcp_config.list_office_claw_mcp_tools(_PARAMS))

    await asyncio.sleep(0)  # let t2 observe in-flight task
    releases.set()

    r1, r2 = await asyncio.gather(t1, t2)
    assert calls == 1, "concurrent identical discovery must coalesce into one task"
    assert r1 == r2
