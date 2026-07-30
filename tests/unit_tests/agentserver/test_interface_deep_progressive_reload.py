# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""ProgressiveToolRail hot-reload lifecycle tests."""

# pylint: disable=protected-access

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from jiuwenclaw.agentserver.deep_agent.interface_deep import JiuWenClawDeepAdapter


class _ProgressiveRailReloadHarness(JiuWenClawDeepAdapter):
    """Minimal adapter surface for ProgressiveToolRail reload tests."""

    @classmethod
    def for_test(cls) -> "_ProgressiveRailReloadHarness":
        adapter = cls.__new__(cls)
        adapter._instance = MagicMock()
        adapter._instance.unregister_rail = AsyncMock()
        adapter._skill_evolution_rail = None
        adapter._skill_rail = None
        adapter._context_engineering_rail = None
        adapter._memory_rail = None
        adapter._lsp_rail = None
        adapter._avatar_rail = None
        adapter._permission_rail = None
        adapter._progressive_tool_rail = None
        adapter._disabled_tools_rail = None
        adapter._skill_credential_injection_rail = None
        adapter._pip_isolation_rail = None
        adapter._model = MagicMock()
        adapter._context_engineering_rail_mode = None
        adapter._context_engine_config_fp = None
        adapter._last_runtime_mode = "agent.plan"

        adapter._build_skill_rail = MagicMock(return_value=None)
        adapter._build_progressive_tool_rail = MagicMock(return_value=None)
        adapter._build_disabled_tools_rail = MagicMock(return_value=None)
        adapter._build_skill_credential_injection_rail = MagicMock(return_value=None)
        adapter._build_skill_evolution_rail = MagicMock(return_value=None)
        adapter._filesystem_rail_enabled_for_profile = MagicMock(return_value=True)
        adapter._update_permission_rail = MagicMock()
        adapter._skill_include_harness_fs_tools = MagicMock(return_value=False)
        adapter._skill_include_skill_body_tools = MagicMock(return_value=False)
        adapter._context_engine_config_fingerprint = MagicMock(return_value="fp")
        return adapter

    async def get_current_agent_rails(
        self, config: dict[str, Any]
    ) -> tuple[list[Any], Any | None]:
        return await self._get_current_agent_rails(config, {"react": config})


def _react_config(*, enabled: bool) -> dict[str, Any]:
    return {
        "evolution": {"enabled": False},
        "tool_lazy_load": {"enabled": enabled},
    }


@pytest.mark.asyncio
async def test_reload_stages_progressive_rail_for_unregister_when_disabled():
    adapter = _ProgressiveRailReloadHarness.for_test()
    old_rail = MagicMock(name="old-progressive-tool-rail")
    adapter._progressive_tool_rail = old_rail

    rails, rail_to_unregister = await adapter.get_current_agent_rails(
        _react_config(enabled=False)
    )

    adapter._instance.unregister_rail.assert_not_awaited()
    assert adapter._progressive_tool_rail is old_rail
    assert rail_to_unregister is old_rail
    assert old_rail not in rails


@pytest.mark.asyncio
async def test_reload_has_no_progressive_unregister_when_already_disabled():
    adapter = _ProgressiveRailReloadHarness.for_test()

    rails, rail_to_unregister = await adapter.get_current_agent_rails(
        _react_config(enabled=False)
    )

    adapter._instance.unregister_rail.assert_not_awaited()
    assert rail_to_unregister is None
    assert rails == []


@pytest.mark.asyncio
async def test_reload_replaces_progressive_rail_when_still_enabled():
    adapter = _ProgressiveRailReloadHarness.for_test()
    old_rail = MagicMock(name="old-progressive-tool-rail")
    new_rail = MagicMock(name="new-progressive-tool-rail")
    adapter._progressive_tool_rail = old_rail
    adapter._build_progressive_tool_rail.return_value = new_rail

    rails, rail_to_unregister = await adapter.get_current_agent_rails(
        _react_config(enabled=True)
    )

    adapter._instance.unregister_rail.assert_not_awaited()
    assert rail_to_unregister is None
    assert adapter._progressive_tool_rail is new_rail
    assert new_rail in rails
    assert old_rail not in rails
