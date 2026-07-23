# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_builtin_memory_init_uses_env_agent_id() -> None:
    """Builtin memory INDEX_CACHE key must use env_agent_id (same as acquire)."""
    pytest.importorskip("openjiuwen.harness.rails.context_engineering_rail")

    from jiuwenclaw.agentserver.deep_agent.interface_deep import JiuWenClawDeepAdapter

    adapter = object.__new__(JiuWenClawDeepAdapter)
    adapter._env_service_id = "sid1"
    adapter._env_agent_id = "aid1"
    adapter._service_id = "ignored"
    adapter._agent_id = "ignored"
    adapter._session_id = "sess1"
    adapter._workspace_dir = "/ws/tenant_a"
    adapter._memory_cache_fingerprint = "fp1"
    adapter._instance = SimpleNamespace(system_prompt_builder=None, tool_manager=None)
    adapter._qualified_memory_tool_ids = []
    adapter._fallback_card_suffix = None

    assert adapter._env_ns_ids() == ("sid1", "aid1")

    with patch(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.init_memory_manager_async",
        new=AsyncMock(),
    ) as init_mock, patch(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.get_decorated_tools",
        return_value=[],
    ), patch.object(
        adapter, "_resolve_agent_card_id", return_value="card1"
    ):
        await adapter._init_builtin_memory_manager("builtin", {})
        init_mock.assert_awaited_once()
        assert init_mock.await_args.kwargs["agent_id"] == "aid1"
        assert init_mock.await_args.kwargs["workspace_dir"] == "/ws/tenant_a"
