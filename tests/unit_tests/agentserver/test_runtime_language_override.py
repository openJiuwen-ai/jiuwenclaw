# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the language-source split between scaffolding and user-visible UI.

Background: ``_resolve_runtime_language`` used to drive both the system-prompt
scaffolding (via ``system_prompt_builder.language`` set by
``_update_prompt_for_mode``) and the user-visible rails/tools
(``StructuredAskUserRail`` / ``CircuitBreakerRail`` / ``WorkAgentModeRail``
/ ``WebPaidSearchTool`` etc.). Hardcoding it to ``"en"`` kept scaffolding
English but also forced user-visible UI elements to English for zh users.

Fix: ``_resolve_runtime_language`` still returns ``"en"`` (so scaffolding
stays English), but the user-visible rails/tools read
``_resolve_output_language`` directly so they follow ``preferred_language``.
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest

from jiuwenswarm.server.runtime.agent_adapter import interface_code as code_module
from jiuwenswarm.server.runtime.agent_adapter import interface_deep as deep_module
from jiuwenswarm.server.runtime.agent_adapter.interface_code import JiuwenSwarmCodeAdapter
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenSwarmDeepAdapter


# ---------------------------------------------------------------------------
# Regression: create_instance must NOT modify _runtime_language_override
# ---------------------------------------------------------------------------


def _patch_deep_create_instance_early_io(stack: ExitStack) -> None:
    """Neutralize I/O on the early-return path of deep ``create_instance``."""
    stack.enter_context(patch.object(JiuWenSwarmDeepAdapter, "set_checkpoint", AsyncMock()))
    stack.enter_context(patch.object(JiuWenSwarmDeepAdapter, "_refresh_multimodal_configs", return_value=None))
    stack.enter_context(patch("jiuwenswarm.server.runtime.agent_adapter.interface_deep.load_dotenv_runtime"))


@pytest.mark.asyncio
async def test_deep_create_instance_does_not_set_runtime_language_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_runtime_language_override`` must stay ``None`` so scaffolding stays English.

    Setting it from ``preferred_language`` here would propagate into
    ``system_prompt_builder.language`` via ``_update_prompt_for_mode`` and
    switch SAFETY / skills sections to Chinese for zh users — a regression
    of the "office scaffolding always English" design.
    """
    monkeypatch.setattr(deep_module, "get_config", lambda: {"preferred_language": "zh"})

    adapter = JiuWenSwarmDeepAdapter()
    with ExitStack() as stack:
        _patch_deep_create_instance_early_io(stack)
        await adapter.create_instance()

    assert adapter._runtime_language_override is None
    assert adapter._resolve_runtime_language() == "en"
    # Output language still follows preferred_language.
    assert adapter._resolve_output_language() == "cn"


@pytest.mark.asyncio
async def test_code_create_instance_does_not_set_runtime_language_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same regression guard for the code adapter."""
    monkeypatch.setattr(code_module, "get_config", lambda: {"preferred_language": "zh"})

    adapter = JiuwenSwarmCodeAdapter()
    with ExitStack() as stack:
        stack.enter_context(patch.object(JiuwenSwarmCodeAdapter, "set_checkpoint", AsyncMock()))
        stack.enter_context(patch.object(JiuwenSwarmCodeAdapter, "_refresh_multimodal_configs", return_value=None))
        await adapter.create_instance()

    assert adapter._runtime_language_override is None
    assert adapter._resolve_runtime_language() == "en"
    assert adapter._resolve_output_language() == "cn"


# ---------------------------------------------------------------------------
# User-visible rails/tools must read _resolve_output_language
# ---------------------------------------------------------------------------


def test_deep_build_structured_ask_user_rail_uses_output_language() -> None:
    """StructuredAskUserRail tool-card description + schema field text must
    follow ``preferred_language`` so zh users get Chinese button labels /
    form labels from the LLM."""
    adapter = JiuWenSwarmDeepAdapter()
    sentinel = "cn-test"
    with patch.object(adapter, "_resolve_output_language", return_value=sentinel), \
         patch("jiuwenswarm.server.runtime.agent_adapter.interface_deep.StructuredAskUserRail") as mock_rail:
        adapter._build_structured_ask_user_rail()

    mock_rail.assert_called_once_with(language=sentinel)


def test_deep_build_work_agent_mode_rail_uses_output_language() -> None:
    """Plan-mode rules note is user-visible (rendered in plan mode UI); must
    follow ``preferred_language``."""
    adapter = JiuWenSwarmDeepAdapter()
    sentinel = "cn-test"
    # WorkAgentModeRail is imported lazily inside the builder, so patch the
    # source module rather than interface_deep's namespace.
    with patch.object(adapter, "_resolve_output_language", return_value=sentinel), \
         patch("jiuwenswarm.agents.harness.work.rails.work_agent_mode_rail.WorkAgentModeRail") as mock_rail:
        adapter._build_work_agent_mode_rail()

    mock_rail.assert_called_once_with(language=sentinel)


def test_deep_build_circuit_breaker_rail_uses_output_language(monkeypatch: pytest.MonkeyPatch) -> None:
    """Circuit-breaker error messages (e.g. "工具 X 已重复调用...") are shown
    directly to the user, so they must follow ``preferred_language``."""
    monkeypatch.setattr(deep_module, "get_config", lambda: {"execution_guard": {"circuit_breaker": {"enabled": True}}})
    adapter = JiuWenSwarmDeepAdapter()
    sentinel = "cn-test"
    with patch.object(adapter, "_resolve_output_language", return_value=sentinel), \
         patch("jiuwenswarm.server.runtime.agent_adapter.interface_deep.CircuitBreakerRail") as mock_rail:
        adapter._build_circuit_breaker_rail()

    mock_rail.assert_called_once()
    _args, kwargs = mock_rail.call_args
    assert kwargs.get("language") == sentinel


def test_code_build_structured_ask_user_rail_uses_output_language() -> None:
    """Symmetric to deep adapter."""
    adapter = JiuwenSwarmCodeAdapter()
    sentinel = "cn-test"
    with patch.object(adapter, "_resolve_output_language", return_value=sentinel), \
         patch("jiuwenswarm.server.runtime.agent_adapter.interface_code.StructuredAskUserRail") as mock_rail:
        adapter._build_structured_ask_user_rail()

    mock_rail.assert_called_once_with(language=sentinel)


def test_code_build_paid_search_tool_uses_output_language(monkeypatch: pytest.MonkeyPatch) -> None:
    """WebPaidSearchTool description is user-influencing (LLM sees Chinese
    description → tends to produce Chinese search queries / results)."""
    monkeypatch.setenv("BOCHA_API_KEY", "test-key")
    adapter = JiuwenSwarmCodeAdapter()
    sentinel = "cn-test"
    with patch.object(adapter, "_resolve_output_language", return_value=sentinel), \
         patch("jiuwenswarm.server.runtime.agent_adapter.interface_code.WebPaidSearchTool") as mock_tool:
        adapter._build_paid_search_tool("agent-id")

    mock_tool.assert_called_once()
    _args, kwargs = mock_tool.call_args
    assert kwargs.get("language") == sentinel


def test_code_build_web_free_search_tool_uses_output_language() -> None:
    """WebFreeSearchTool description is user-influencing; symmetric to paid search."""
    adapter = JiuwenSwarmCodeAdapter()
    sentinel = "cn-test"
    with patch.object(adapter, "_resolve_output_language", return_value=sentinel), \
         patch("jiuwenswarm.server.runtime.agent_adapter.interface_code.WebFreeSearchTool") as mock_tool:
        adapter._build_web_free_search_tool("agent-id")

    mock_tool.assert_called_once()
    _args, kwargs = mock_tool.call_args
    assert kwargs.get("language") == sentinel


def test_code_build_web_fetch_webpage_tool_uses_output_language() -> None:
    """WebFetchWebpageTool description is user-influencing; symmetric to paid search."""
    adapter = JiuwenSwarmCodeAdapter()
    sentinel = "cn-test"
    with patch.object(adapter, "_resolve_output_language", return_value=sentinel), \
         patch("jiuwenswarm.server.runtime.agent_adapter.interface_code.WebFetchWebpageTool") as mock_tool:
        adapter._build_web_fetch_webpage_tool("agent-id")

    mock_tool.assert_called_once()
    _args, kwargs = mock_tool.call_args
    assert kwargs.get("language") == sentinel


# ---------------------------------------------------------------------------
# Scaffolding must remain English (system_prompt_builder.language path)
# ---------------------------------------------------------------------------


def test_deep_resolve_runtime_language_defaults_to_en() -> None:
    """``_resolve_runtime_language`` is what feeds ``system_prompt_builder.language``
    via ``_update_prompt_for_mode``. It must default to ``"en"`` so the
    SAFETY / skills / memory / subagent rails' en/cn branch selection stays
    on English for office mode."""
    adapter = JiuWenSwarmDeepAdapter()
    assert adapter._resolve_runtime_language() == "en"


def test_code_resolve_runtime_language_defaults_to_en() -> None:
    adapter = JiuwenSwarmCodeAdapter()
    assert adapter._resolve_runtime_language() == "en"


def test_deep_resolve_prompt_language_is_hardcoded_en() -> None:
    """The scaffolding source-of-truth must remain hardcoded English."""
    assert JiuWenSwarmDeepAdapter._resolve_prompt_language() == "en"


def test_code_resolve_prompt_language_is_hardcoded_en() -> None:
    assert JiuwenSwarmCodeAdapter._resolve_prompt_language() == "en"
