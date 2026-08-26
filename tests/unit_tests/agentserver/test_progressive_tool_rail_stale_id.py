# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Regression tests for stale deferred-tool-id handling in ProgressiveToolRail.

Covers the failure where a request-scoped MCP (e.g. OfficeClaw MCP) rebinds
the same tool name to a new id while ``_cached_deferred_tool_infos`` still
holds the previous card. Without the fixes, ``invoke_tool`` would look up
``Runner.resource_mgr`` with the stale id and fail with
``无法获取工具 '<name>' 的实例。``.
"""

# pylint: disable=protected-access

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from openjiuwen.core.runner import Runner

from jiuwenswarm.agents.harness.common.rails.progressive_tool_rail import (
    ProgressiveToolRail,
)
from jiuwenswarm.agents.harness.common.tools.invoke_tool_tool import (
    InvokeToolInput,
)


@pytest.mark.asyncio
async def test_invoke_retries_with_refreshed_id_when_cached_id_is_stale():
    """When get_tool(stale_id) returns None, the rail refreshes the cache
    and retries with the new id from the refreshed card."""

    old_card = SimpleNamespace(
        name="office_claw_register_scheduled_task",
        id="office-claw-request-stale-hash.office-claw.office_claw_register_scheduled_task",
        description="d",
        input_params={},
        properties={},
    )
    new_card = SimpleNamespace(
        name="office_claw_register_scheduled_task",
        id="office-claw-request-fresh-hash.office-claw.office_claw_register_scheduled_task",
        description="d",
        input_params={},
        properties={"resilience": {"timeout_s": None}},
    )

    target = SimpleNamespace(invoke=AsyncMock(return_value={"ok": True}))
    rail = ProgressiveToolRail(eager_tools=["tools_search", "invoke_tool"])
    rail._cached_deferred_tool_infos = [old_card]

    async def _fake_refresh(_agent=None):
        rail._cached_deferred_tool_infos = [new_card]

    def _get_tool_side_effect(tool_id, session=None):
        if tool_id == old_card.id:
            return None
        if tool_id == new_card.id:
            return target
        return None

    with patch.object(
        Runner.resource_mgr, "get_tool", side_effect=_get_tool_side_effect
    ), patch.object(
        rail, "_refresh_deferred_tool_cache", new=_fake_refresh
    ):
        result = await rail._invoke_target_tool(
            None,
            InvokeToolInput(
                tool_name="office_claw_register_scheduled_task",
                arguments={"templateId": "reminder"},
            ),
        )

    assert result == {
        "success": True,
        "tool_name": "office_claw_register_scheduled_task",
        "result": {"ok": True},
    }
    target.invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_invoke_returns_failure_when_refreshed_id_also_missing():
    """If both the stale id and the refreshed id fail to resolve in
    resource_mgr, the rail still returns the documented error (now with
    diagnostic logging). The refresh must be attempted exactly once."""

    stale_card = SimpleNamespace(
        name="ghost_tool",
        id="ghost-stale-id",
        description="d",
        input_params={},
        properties={},
    )
    rail = ProgressiveToolRail(eager_tools=["tools_search", "invoke_tool"])
    rail._cached_deferred_tool_infos = [stale_card]

    refresh_calls = 0

    async def _fake_refresh(_agent=None):
        nonlocal refresh_calls
        refresh_calls += 1
        # Cache stays the same — no fresh id available.
        rail._cached_deferred_tool_infos = [stale_card]

    with patch.object(
        Runner.resource_mgr, "get_tool", return_value=None
    ), patch.object(
        rail, "_refresh_deferred_tool_cache", new=_fake_refresh
    ):
        result = await rail._invoke_target_tool(
            None,
            InvokeToolInput(tool_name="ghost_tool", arguments={}),
        )

    assert result == {
        "success": False,
        "error": "无法获取工具 'ghost_tool' 的实例。",
        "tool_name": "ghost_tool",
    }
    # The retry must trigger exactly one cache refresh — no infinite loop.
    assert refresh_calls == 1


@pytest.mark.asyncio
async def test_invoke_does_not_retry_when_get_tool_succeeds_first_time():
    """The retry path must be skipped entirely when the cached id still
    resolves in resource_mgr — no spurious cache refresh."""

    card = SimpleNamespace(
        name="healthy_tool",
        id="healthy-id",
        description="d",
        input_params={},
        properties={"resilience": {"timeout_s": None}},
    )
    target = SimpleNamespace(invoke=AsyncMock(return_value="ok"))
    rail = ProgressiveToolRail(eager_tools=["tools_search", "invoke_tool"])
    rail._cached_deferred_tool_infos = [card]

    refresh_calls = 0

    async def _counting_refresh(_agent=None):
        nonlocal refresh_calls
        refresh_calls += 1

    with patch.object(Runner.resource_mgr, "get_tool", return_value=target), patch.object(
        rail, "_refresh_deferred_tool_cache", new=_counting_refresh
    ):
        result = await rail._invoke_target_tool(
            None,
            InvokeToolInput(tool_name="healthy_tool", arguments={}),
        )

    assert result == {"success": True, "tool_name": "healthy_tool", "result": "ok"}
    assert refresh_calls == 0


@pytest.mark.asyncio
async def test_staleness_check_detects_same_name_different_id_rebind():
    """_refresh_deferred_tool_cache_if_stale must trigger a refresh when
    ability_manager returns cards with the same names but different ids
    (e.g. after a request-scoped MCP rebind)."""

    rail = ProgressiveToolRail(eager_tools=["tools_search", "invoke_tool"])
    agent = SimpleNamespace(ability_manager=SimpleNamespace())
    # Bind both _runtime_agent and _deep_agent to the same object so that
    # _resolve_runtime_agent() does not invalidate the cache mid-check.
    rail._runtime_agent = agent
    rail._deep_agent = agent

    old_card = SimpleNamespace(
        name="office_claw_register_scheduled_task",
        id="office-claw-request-old-hash.office-claw.office_claw_register_scheduled_task",
    )
    new_card = SimpleNamespace(
        name="office_claw_register_scheduled_task",
        id="office-claw-request-new-hash.office-claw.office_claw_register_scheduled_task",
    )
    rail._cached_all_tool_infos = [old_card]
    rail._cached_deferred_tool_infos = [old_card]

    refresh_calls = 0

    async def _counting_refresh(agent=None):
        nonlocal refresh_calls
        refresh_calls += 1
        rail._cached_all_tool_infos = [new_card]
        rail._cached_deferred_tool_infos = [new_card]

    with patch.object(
        rail, "_get_all_tool_infos", new=AsyncMock(return_value=[new_card])
    ), patch.object(
        rail, "_refresh_deferred_tool_cache", new=_counting_refresh
    ):
        await rail._refresh_deferred_tool_cache_if_stale()

    assert refresh_calls == 1
    assert rail._cached_all_tool_infos == [new_card]


@pytest.mark.asyncio
async def test_staleness_check_skips_refresh_when_ids_unchanged():
    """If the live tools have the same ids as the cached tools, no refresh
    should be triggered — the id-set comparison must not produce false
    positives on every iteration."""

    rail = ProgressiveToolRail(eager_tools=["tools_search", "invoke_tool"])
    agent = SimpleNamespace(ability_manager=SimpleNamespace())
    # Bind both agents to the same object so _resolve_runtime_agent() does
    # not invalidate the cache before the staleness comparison runs.
    rail._runtime_agent = agent
    rail._deep_agent = agent

    card = SimpleNamespace(
        name="stable_tool",
        id="stable-id",
    )
    rail._cached_all_tool_infos = [card]
    rail._cached_deferred_tool_infos = [card]

    refresh_calls = 0

    async def _counting_refresh(agent=None):
        nonlocal refresh_calls
        refresh_calls += 1

    with patch.object(
        rail, "_get_all_tool_infos", new=AsyncMock(return_value=[card])
    ), patch.object(
        rail, "_refresh_deferred_tool_cache", new=_counting_refresh
    ):
        await rail._refresh_deferred_tool_cache_if_stale()

    assert refresh_calls == 0


@pytest.mark.asyncio
async def test_invoke_falls_back_to_active_office_claw_tool_id():
    """Problem 172: cache/AM still points at a concurrent request's cleaned-up
    id, but this request's owned tool id remains in resource_mgr and is bound
    via ``bind_active_office_claw_mcp_tools``."""

    from jiuwenswarm.common.mcp_config import bind_active_office_claw_mcp_tools

    foreign_dead = (
        "office-claw-request-other.office-claw.office_claw_register_scheduled_task"
    )
    owned_live = (
        "office-claw-request-mine.office-claw.office_claw_register_scheduled_task"
    )
    stale_card = SimpleNamespace(
        name="office_claw_register_scheduled_task",
        id=foreign_dead,
        description="d",
        input_params={},
        properties={"resilience": {"timeout_s": None}},
    )
    target = SimpleNamespace(invoke=AsyncMock(return_value={"ok": True}))
    rail = ProgressiveToolRail(eager_tools=["tools_search", "invoke_tool"])
    rail._cached_deferred_tool_infos = [stale_card]

    async def _fake_refresh(_agent=None):
        # AbilityManager still polluted with the foreign id after refresh.
        rail._cached_deferred_tool_infos = [stale_card]

    def _get_tool_side_effect(tool_id, session=None):
        if tool_id == foreign_dead:
            return None
        if tool_id == owned_live:
            return target
        return None

    with bind_active_office_claw_mcp_tools([owned_live]), patch.object(
        Runner.resource_mgr, "get_tool", side_effect=_get_tool_side_effect
    ), patch.object(rail, "_refresh_deferred_tool_cache", new=_fake_refresh):
        result = await rail._invoke_target_tool(
            None,
            InvokeToolInput(
                tool_name="office_claw_register_scheduled_task",
                arguments={"templateId": "reminder"},
            ),
        )

    assert result["success"] is True
    assert result["tool_name"] == "office_claw_register_scheduled_task"
    target.invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_search_skips_dead_instance_without_active_fallback():
    """tools_search must not advertise a tool whose instance is gone and
    which has no active OfficeClaw binding for this request."""

    from jiuwenswarm.agents.harness.common.tools.tools_search_tool import (
        ToolsSearchInput,
    )

    dead_card = SimpleNamespace(
        name="office_claw_preview_scheduled_task",
        id="office-claw-request-dead.office-claw.office_claw_preview_scheduled_task",
        description="preview",
        input_params={"type": "object"},
    )
    rail = ProgressiveToolRail(eager_tools=["tools_search", "invoke_tool"])
    rail._cached_deferred_tool_infos = [dead_card]

    with patch.object(Runner.resource_mgr, "get_tool", return_value=None):
        result = await rail._search_tools(
            None,
            ToolsSearchInput(tool_name="office_claw_preview_scheduled_task"),
        )

    assert result["success"] is False
    assert result["matches"] == []
    assert "实例不可用" in result["message"]


def test_resolve_active_office_claw_tool_id_maps_short_name():
    from jiuwenswarm.common.mcp_config import (
        bind_active_office_claw_mcp_tools,
        resolve_active_office_claw_tool_id,
    )

    owned = "office-claw-request-abc.office-claw.office_claw_preview_scheduled_task"
    with bind_active_office_claw_mcp_tools([owned]):
        assert (
            resolve_active_office_claw_tool_id("office_claw_preview_scheduled_task")
            == owned
        )
        assert resolve_active_office_claw_tool_id("missing_tool") is None

    assert resolve_active_office_claw_tool_id("office_claw_preview_scheduled_task") is None
