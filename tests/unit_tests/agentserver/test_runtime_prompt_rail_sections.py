# coding: utf-8
"""Tests for RuntimePromptRail sections gating (team-mode time-only usage)."""
import asyncio
import unittest
from unittest.mock import MagicMock

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts.builder import SystemPromptBuilder

from jiuwenclaw.agentserver.deep_agent.rails.runtime_prompt_rail import RuntimePromptRail


class TestSectionsGating(unittest.TestCase):

    def test_time_only_with_builder_injects_date_no_builder_sections(self):
        rail = RuntimePromptRail(language="cn", sections=("time",))
        builder = SystemPromptBuilder(language="cn")
        rail.system_prompt_builder = builder
        ctx = AgentCallbackContext(agent=MagicMock(system_prompt_builder=builder))
        ctx.extra = {}
        asyncio.run(rail.before_model_call(ctx))

        entries = ctx.extra["environment_context"]
        assert len(entries) == 1
        assert entries[0]["source"] == "time_rail"
        assert "当前日期" in entries[0]["content"]

        built = builder.build()
        assert "# 运行时" not in built
        assert "# 你的家" not in built

    def test_time_only_without_builder_still_injects_date(self):
        """团队成员 agent 无 system_prompt_builder 时也必须注入日期（无早退）。"""
        rail = RuntimePromptRail(language="cn", sections=("time",))
        ctx = AgentCallbackContext(agent=MagicMock(spec=[]))
        ctx.extra = {}
        asyncio.run(rail.before_model_call(ctx))
        assert len(ctx.extra["environment_context"]) == 1
        assert "当前年份" in ctx.extra["environment_context"][0]["content"]

    def test_default_sections_unchanged(self):
        """默认 sections=None：time + runtime + workspace 全部注入（普通模式回归）。"""
        rail = RuntimePromptRail(language="cn")
        builder = SystemPromptBuilder(language="cn")
        rail.system_prompt_builder = builder
        ctx = AgentCallbackContext(agent=MagicMock(system_prompt_builder=builder))
        ctx.extra = {}
        asyncio.run(rail.before_model_call(ctx))

        assert ctx.extra["environment_context"][0]["source"] == "time_rail"
        built = builder.build()
        assert "# 运行时" in built
        assert "# 你的家" in built

    def test_sections_list_accepted(self):
        rail = RuntimePromptRail(language="en", sections=["time"])
        ctx = AgentCallbackContext(agent=MagicMock(spec=[]))
        ctx.extra = {}
        asyncio.run(rail.before_model_call(ctx))
        assert "Current date" in ctx.extra["environment_context"][0]["content"]

    def test_time_only_without_builder_no_warning(self):
        """time-only 无 builder 属正常用法，不应刷 warning。"""
        rail = RuntimePromptRail(language="cn", sections=("time",))
        ctx = AgentCallbackContext(agent=MagicMock(spec=[]))
        ctx.extra = {}
        with self.assertNoLogs(
            "jiuwenclaw.agentserver.deep_agent.rails.runtime_prompt_rail", level="WARNING"
        ):
            asyncio.run(rail.before_model_call(ctx))
        assert len(ctx.extra["environment_context"]) == 1


if __name__ == "__main__":
    unittest.main()
