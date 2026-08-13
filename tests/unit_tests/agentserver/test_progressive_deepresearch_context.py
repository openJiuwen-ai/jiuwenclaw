# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.ability_manager import AbilityManager

from jiuwenswarm.agents.harness.common.rails.progressive_tool_rail import (
    ProgressiveToolRail,
)
from jiuwenswarm.agents.harness.common.tools.deepresearch import tools as dt
from jiuwenswarm.agents.harness.common.tools.invoke_tool_tool import (
    InvokeToolInput,
    InvokeToolTool,
)
from jiuwenswarm.common.local_env_config import (
    clear_agent_env_ns,
    get_bound_agent_env_ns,
    get_task_env_overlay,
    replace_active_env,
)


@pytest.mark.asyncio
async def test_deferred_deepresearch_rebinds_trusted_adapter_context():
    service_id = "progressive-test-service"
    agent_id = "office"
    shared_root = "/trusted/office-claw-skills"
    output_dir = "/trusted/agent-workspace/projects"
    replace_active_env(
        {"JIUWENSWARM_SHARED_SKILLS_DIRS": shared_root},
        service_id=service_id,
        agent_id=agent_id,
    )
    target = AsyncMock()

    async def _invoke(_arguments, **_kwargs):
        assert get_task_env_overlay() == {
            "JIUWENSWARM_SHARED_SKILLS_DIRS": shared_root
        }
        assert dt._get_route() == {
            "request_id": "request",
            "channel_id": "officeclaw",
            "session_id": "session",
            "service_id": service_id,
            "agent_id": agent_id,
        }
        assert dt._get_effective_request_output_dir() == Path(output_dir)
        return "ok"

    target.invoke.side_effect = _invoke
    card = SimpleNamespace(name="deepresearch_stream", id="deepresearch-tool-id")
    rail = ProgressiveToolRail(
        eager_tools=["tools_search", "invoke_tool"],
        deepresearch_context_provider=lambda: {
            "request_id": "request",
            "channel_id": "officeclaw",
            "session_id": "session",
            "service_id": service_id,
            "agent_id": agent_id,
            "output_dir": output_dir,
        },
    )
    rail._cached_deferred_tool_infos = [card]

    try:
        with patch.object(Runner.resource_mgr, "get_tool", return_value=target):
            result = await rail._invoke_target_tool(
                None,
                InvokeToolInput(
                    tool_name="deepresearch_stream",
                    arguments={"action": "start", "query": "q"},
                ),
            )
        assert result == {
            "success": True,
            "tool_name": "deepresearch_stream",
            "result": "ok",
        }
        assert get_task_env_overlay() is None
        assert dt._get_route() == {
            "request_id": "",
            "channel_id": "",
            "session_id": "",
            "service_id": "default",
            "agent_id": "default",
        }
    finally:
        clear_agent_env_ns(service_id, agent_id)


def test_unregistered_deepresearch_prefix_does_not_receive_tenant_context():
    provider = Mock(
        return_value={
            "request_id": "request",
            "channel_id": "officeclaw",
            "session_id": "session",
            "service_id": "default",
            "agent_id": "default",
        }
    )
    rail = ProgressiveToolRail(
        eager_tools=["tools_search", "invoke_tool"],
        deepresearch_context_provider=provider,
    )

    with rail._bind_deepresearch_context("deepresearch_probe"):
        assert get_task_env_overlay() is None

    provider.assert_not_called()


def test_deepresearch_context_binding_rolls_back_partial_setup():
    before = get_bound_agent_env_ns()
    rail = ProgressiveToolRail(
        eager_tools=["tools_search", "invoke_tool"],
        deepresearch_context_provider=lambda: {
            "service_id": "progressive-test-service",
            "agent_id": "office",
        },
    )

    with patch(
        "jiuwenswarm.common.local_env_config.build_effective_env_overlay",
        side_effect=RuntimeError("overlay failed"),
    ):
        with pytest.raises(RuntimeError, match="overlay failed"):
            with rail._bind_deepresearch_context("deepresearch_stream"):
                pass

    assert get_bound_agent_env_ns() == before


def test_invoke_tool_defers_outer_timeout_to_target_policy():
    tool = InvokeToolTool(AsyncMock())
    assert tool.card.properties["resilience"]["timeout_s"] is None


@pytest.mark.asyncio
async def test_deferred_target_keeps_its_own_timeout_policy():
    async def _slow_invoke(_arguments, **_kwargs):
        await asyncio.sleep(0.05)
        return "late"

    target = SimpleNamespace(invoke=_slow_invoke)
    card = SimpleNamespace(
        name="ordinary_deferred",
        id="ordinary-tool-id",
        properties={"resilience": {"timeout_s": 0.01}},
    )
    rail = ProgressiveToolRail(eager_tools=["tools_search", "invoke_tool"])
    rail._cached_deferred_tool_infos = [card]

    with patch.object(Runner.resource_mgr, "get_tool", return_value=target):
        result = await rail._invoke_target_tool(
            None,
            InvokeToolInput(tool_name="ordinary_deferred", arguments={}),
        )

    assert result == {
        "success": False,
        "error": "Tool 'ordinary_deferred' timed out after 0.01s",
        "tool_name": "ordinary_deferred",
    }


@pytest.mark.asyncio
async def test_deferred_target_preserves_its_own_timeout_error():
    async def _invoke_with_internal_timeout(_arguments, **_kwargs):
        raise TimeoutError("backend request timed out")

    target = SimpleNamespace(invoke=_invoke_with_internal_timeout)
    card = SimpleNamespace(
        name="ordinary_deferred",
        id="ordinary-tool-id",
        properties={"resilience": {"timeout_s": None}},
    )
    rail = ProgressiveToolRail(eager_tools=["tools_search", "invoke_tool"])
    rail._cached_deferred_tool_infos = [card]

    with patch.object(Runner.resource_mgr, "get_tool", return_value=target):
        result = await rail._invoke_target_tool(
            None,
            InvokeToolInput(tool_name="ordinary_deferred", arguments={}),
        )

    assert result == {
        "success": False,
        "error": "backend request timed out",
        "tool_name": "ordinary_deferred",
    }


@pytest.mark.asyncio
async def test_deferred_tool_lookup_keeps_default_timeout_boundary():
    async def _slow_list_tool_info():
        await asyncio.sleep(0.05)

    ability_manager = SimpleNamespace(
        list=lambda: [],
        list_tool_info=_slow_list_tool_info,
    )
    rail = ProgressiveToolRail(eager_tools=["tools_search", "invoke_tool"])
    rail._runtime_agent = SimpleNamespace(ability_manager=ability_manager)

    with patch.object(AbilityManager, "_resolve_call_timeout", return_value=0.01):
        result = await rail._invoke_target_tool(
            None,
            InvokeToolInput(tool_name="missing_deferred", arguments={}),
        )

    assert result == {
        "success": False,
        "error": "Tool lookup for 'missing_deferred' timed out after 0.01s",
        "tool_name": "missing_deferred",
    }
