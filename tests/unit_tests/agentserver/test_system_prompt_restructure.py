from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection, SystemPromptBuilder

from jiuwenclaw.agentserver.deep_agent.interface_deep import JiuWenClawDeepAdapter
from jiuwenclaw.agentserver.deep_agent.prompt_builder import build_identity_prompt
from jiuwenclaw.agentserver.deep_agent.rails.runtime_prompt_rail import RuntimePromptRail


class _TestableJiuWenClawDeepAdapter(JiuWenClawDeepAdapter):
    def set_workspace_dir(self, workspace_dir: str) -> None:
        self._workspace_dir = workspace_dir

    def build_configured_subagents(
        self,
        model: Model,
        config: dict,
        config_base: dict | None = None,
    ):
        return self._build_configured_subagents(model, config, config_base)


def test_build_identity_prompt_contains_identity_section_only():
    prompt = build_identity_prompt(mode="agent", language="zh", channel="web")

    assert "# 你的家" in prompt
    assert "# 消息说明" not in prompt


@pytest.mark.asyncio
async def test_runtime_time_section_participates_in_priority_order():
    builder = SystemPromptBuilder(language="cn")
    builder.add_section(PromptSection(name="identity", content={"cn": "identity"}, priority=10))
    builder.add_section(PromptSection(name="tools", content={"cn": "# 可用工具"}, priority=30))
    builder.add_section(PromptSection(name="workspace", content={"cn": "# 工作空间"}, priority=70))

    runtime_rail = RuntimePromptRail(
        language="cn",
        channel="web",
        agent_name="main_agent",
        model_name="test-model",
    )
    runtime_rail.init(SimpleNamespace(system_prompt_builder=builder))

    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    await runtime_rail.before_model_call(ctx)

    prompt = builder.build()
    ordered_markers = [
        "identity",
        "# 可用工具",
        "# 工作空间",
        "# 当前日期与时间",
        "# 运行时",
    ]
    positions = [prompt.index(marker) for marker in ordered_markers]
    assert positions == sorted(positions)
    assert "运行时：agent=main_agent | model=test-model | channel=web | language=cn" in prompt


def test_resolve_skill_mode_accepts_all_and_auto_list():
    assert JiuWenClawDeepAdapter._resolve_skill_mode({"skill_mode": "all"}) == "all"
    assert JiuWenClawDeepAdapter._resolve_skill_mode({"skill_mode": "auto_list"}) == "auto_list"
    assert JiuWenClawDeepAdapter._resolve_skill_mode({"skill_mode": "invalid"}) == "all"


def test_build_configured_subagents_includes_optional_browser_and_configured_code_research():
    adapter = _TestableJiuWenClawDeepAdapter()
    adapter.set_workspace_dir("/tmp/jiuwenclaw-workspace")
    model = object()
    config = {
        "max_iterations": 9,
        "subagents": {
            "code_agent": {"enabled": True, "max_iterations": 5},
            "research_agent": {"enabled": True},
            "browser_agent": {"max_iterations": 7},
        },
    }

    with (
        patch.object(adapter, "_resolve_runtime_language", return_value="cn"),
        patch.object(adapter, "_browser_runtime_enabled", return_value=True),
        patch(
            "jiuwenclaw.agentserver.deep_agent.interface_deep.build_code_agent_config",
            return_value="code_spec",
        ) as mock_code,
        patch(
            "jiuwenclaw.agentserver.deep_agent.interface_deep.build_research_agent_config",
            return_value="research_spec",
        ) as mock_research,
        patch(
            "jiuwenclaw.agentserver.deep_agent.interface_deep.build_browser_agent_config",
            return_value="browser_spec",
        ) as mock_browser,
    ):
        subagents = adapter.build_configured_subagents(model, config)

    assert subagents == ["code_spec", "research_spec", "browser_spec"]
    mock_code.assert_called_once_with(
        model,
        workspace="/tmp/jiuwenclaw-workspace",
        language="cn",
        max_iterations=5,
    )
    mock_research.assert_called_once_with(
        model,
        workspace="/tmp/jiuwenclaw-workspace",
        language="cn",
        max_iterations=9,
    )
    mock_browser.assert_called_once_with(
        model,
        workspace="/tmp/jiuwenclaw-workspace",
        language="cn",
        max_iterations=7,
    )


def test_build_configured_subagents_omits_code_research_without_explicit_enable():
    adapter = _TestableJiuWenClawDeepAdapter()
    adapter.set_workspace_dir("/tmp/jiuwenclaw-workspace")
    model = object()
    config = {"max_iterations": 9}

    with (
        patch.object(adapter, "_resolve_runtime_language", return_value="cn"),
        patch.object(adapter, "_browser_runtime_enabled", return_value=True),
        patch(
            "jiuwenclaw.agentserver.deep_agent.interface_deep.build_code_agent_config",
            return_value="code_spec",
        ) as mock_code,
        patch(
            "jiuwenclaw.agentserver.deep_agent.interface_deep.build_research_agent_config",
            return_value="research_spec",
        ) as mock_research,
        patch(
            "jiuwenclaw.agentserver.deep_agent.interface_deep.build_browser_agent_config",
            return_value="browser_spec",
        ) as mock_browser,
    ):
        subagents = adapter.build_configured_subagents(model, config)

    assert subagents == ["browser_spec"]
    mock_code.assert_not_called()
    mock_research.assert_not_called()
    mock_browser.assert_called_once()
