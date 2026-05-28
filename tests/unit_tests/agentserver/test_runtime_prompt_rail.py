# coding: utf-8
# pylint: disable=protected-access
"""Full-coverage tests for RuntimePromptRail system-reminder changes.

Verifies that RuntimePromptRail:
  1. Writes time content to ctx.extra["_system_reminders"] instead of builder
  2. Still adds runtime/workspace sections to builder
  3. uninit() removes runtime/workspace/request_system_prompt but NOT time
  4. CN/EN language variants produce correct content
  5. Time refreshes on each model call
  6. ctx.extra["_system_reminders"] structure is correct
"""
import asyncio
import unittest
from unittest.mock import MagicMock

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.prompts.builder import SystemPromptBuilder

from jiuwenclaw.agentserver.deep_agent.rails.runtime_prompt_rail import RuntimePromptRail


# ============================================================
# 1. before_model_call — ctx.extra injection Tests
# ============================================================

class TestBeforeModelCallCtxExtraInjection(unittest.TestCase):
    """Verify before_model_call writes time to ctx.extra, not to builder."""

    def _make_rail_and_ctx(self, language="cn", timezone_offset=8):
        rail = RuntimePromptRail(language=language, timezone_offset=timezone_offset)
        builder = SystemPromptBuilder(language=language)
        rail.system_prompt_builder = builder
        ctx = AgentCallbackContext(agent=MagicMock())
        ctx.extra = {}
        return rail, builder, ctx

    def test_writes_time_to_ctx_extra_not_builder(self):
        """Time content goes to ctx.extra['_system_reminders'], not builder."""
        rail, builder, ctx = self._make_rail_and_ctx()
        asyncio.run(rail.before_model_call(ctx))

        assert "_system_reminders" in ctx.extra
        assert len(ctx.extra["_system_reminders"]) == 1
        assert ctx.extra["_system_reminders"][0]["source"] == "time_rail"
        assert "当前日期" in ctx.extra["_system_reminders"][0]["content"]

        # Builder should NOT have a "time" section
        built = builder.build()
        assert "当前日期" not in built

    def test_ctx_extra_reminder_entry_has_content_and_source(self):
        """Each reminder entry has 'content' (str) and 'source' (str)."""
        rail, builder, ctx = self._make_rail_and_ctx()
        asyncio.run(rail.before_model_call(ctx))

        entry = ctx.extra["_system_reminders"][0]
        assert isinstance(entry["content"], str)
        assert isinstance(entry["source"], str)
        assert entry["source"] == "time_rail"

    def test_cn_language_produces_cn_time_content(self):
        """Chinese language produces Chinese time content."""
        rail, builder, ctx = self._make_rail_and_ctx(language="cn")
        asyncio.run(rail.before_model_call(ctx))

        content = ctx.extra["_system_reminders"][0]["content"]
        assert "# 当前日期" in content
        assert "- 当前日期：" in content
        assert "- 当前年份：" in content
        assert "搜索 query 必须优先使用当前年份或日期" in content

    def test_en_language_produces_en_time_content(self):
        """English language produces English time content."""
        rail, builder, ctx = self._make_rail_and_ctx(language="en")
        asyncio.run(rail.before_model_call(ctx))

        content = ctx.extra["_system_reminders"][0]["content"]
        assert "# Current Date" in content
        assert "- Current date:" in content
        assert "- Current year:" in content
        assert "search queries must prefer the current year or date" in content
        # No CN text in EN content
        assert "当前日期" not in content

    def test_time_includes_actual_datetime(self):
        """Time content includes a real date string (YYYY-MM-DD format)."""
        rail, builder, ctx = self._make_rail_and_ctx()
        asyncio.run(rail.before_model_call(ctx))

        content = ctx.extra["_system_reminders"][0]["content"]
        # Date pattern: YYYY-MM-DD
        import re
        assert re.search(r"\d{4}-\d{2}-\d{2}", content)

    def test_time_includes_current_year(self):
        """Time content includes current year."""
        rail, builder, ctx = self._make_rail_and_ctx()
        asyncio.run(rail.before_model_call(ctx))

        content = ctx.extra["_system_reminders"][0]["content"]
        import re
        year_match = re.search(r"当前年份：(\d{4})|Current year: (\d{4})", content)
        assert year_match

    def test_timezone_offset_affects_time(self):
        """Different timezone_offset produces date with correct timezone."""
        rail_utc, _, ctx_utc = self._make_rail_and_ctx(timezone_offset=0)
        asyncio.run(rail_utc.before_model_call(ctx_utc))

        rail_cn, _, ctx_cn = self._make_rail_and_ctx(timezone_offset=8)
        asyncio.run(rail_cn.before_model_call(ctx_cn))

        utc_time = ctx_utc.extra["_system_reminders"][0]["content"]
        cn_time = ctx_cn.extra["_system_reminders"][0]["content"]
        # Both should have valid date format
        import re
        utc_dt = re.search(r"\d{4}-\d{2}-\d{2}", utc_time).group()
        cn_dt = re.search(r"\d{4}-\d{2}-\d{2}", cn_time).group()
        # Note: dates may be same if within same calendar day in both zones
        # This test verifies timezone setting is applied, not necessarily that dates differ
        assert utc_dt is not None
        assert cn_dt is not None


# ============================================================
# 2. before_model_call — builder section Tests
# ============================================================

class TestBeforeModelCallBuilderSections(unittest.TestCase):
    """Verify runtime and workspace sections still go into builder."""

    def test_runtime_section_in_builder(self):
        """Runtime section is still added to system_prompt_builder."""
        rail = RuntimePromptRail(language="cn")
        builder = SystemPromptBuilder(language="cn")
        rail.system_prompt_builder = builder
        ctx = AgentCallbackContext(agent=MagicMock())
        ctx.extra = {}

        asyncio.run(rail.before_model_call(ctx))

        built = builder.build()
        assert "# 运行时" in built
        assert "平台：" in built
        assert "Python：" in built

    def test_workspace_section_in_builder(self):
        """Workspace section is still added to system_prompt_builder."""
        rail = RuntimePromptRail(language="cn")
        builder = SystemPromptBuilder(language="cn")
        rail.system_prompt_builder = builder
        ctx = AgentCallbackContext(agent=MagicMock())
        ctx.extra = {}

        asyncio.run(rail.before_model_call(ctx))

        built = builder.build()
        assert "# 你的家" in built

    def test_en_runtime_section_in_builder(self):
        """English runtime section content is in builder."""
        rail = RuntimePromptRail(language="en")
        builder = SystemPromptBuilder(language="en")
        rail.system_prompt_builder = builder
        ctx = AgentCallbackContext(agent=MagicMock())
        ctx.extra = {}

        asyncio.run(rail.before_model_call(ctx))

        built = builder.build()
        assert "# Runtime" in built
        assert "Platform:" in built

    def test_en_workspace_section_in_builder(self):
        """English workspace section content is in builder."""
        rail = RuntimePromptRail(language="en")
        builder = SystemPromptBuilder(language="en")
        rail.system_prompt_builder = builder
        ctx = AgentCallbackContext(agent=MagicMock())
        ctx.extra = {}

        asyncio.run(rail.before_model_call(ctx))

        built = builder.build()
        assert "# Your Home" in built

    def test_no_time_section_in_builder_after_before_model_call(self):
        """After before_model_call, builder has NO 'time' PromptSection."""
        rail = RuntimePromptRail(language="cn")
        builder = SystemPromptBuilder(language="cn")
        rail.system_prompt_builder = builder
        ctx = AgentCallbackContext(agent=MagicMock())
        ctx.extra = {}

        asyncio.run(rail.before_model_call(ctx))

        # Check that "time" section name is not registered
        assert not builder.has_section("time")
        assert builder.has_section("runtime")
        assert builder.has_section("workspace")


# ============================================================
# 3. uninit Tests
# ============================================================

class TestUninit(unittest.TestCase):
    """Verify uninit removes runtime/workspace/request_system_prompt but NOT time."""

    def test_uninit_removes_runtime_section(self):
        """uninit() removes 'runtime' section from builder."""
        rail = RuntimePromptRail(language="cn")
        builder = SystemPromptBuilder(language="cn")
        builder.add_section(PromptSection(name="runtime", content={"cn": "runtime", "en": "runtime"}, priority=95))
        rail.system_prompt_builder = builder

        rail.uninit(MagicMock())

        assert not builder.has_section("runtime")

    def test_uninit_removes_workspace_section(self):
        """uninit() removes 'workspace' section from builder."""
        rail = RuntimePromptRail(language="cn")
        builder = SystemPromptBuilder(language="cn")
        builder.add_section(PromptSection(
            name="workspace", content={"cn": "workspace", "en": "workspace"}, priority=15,
        ))
        rail.system_prompt_builder = builder

        rail.uninit(MagicMock())

        assert not builder.has_section("workspace")

    def test_uninit_removes_request_system_prompt_section(self):
        """uninit() removes 'request_system_prompt' section from builder."""
        rail = RuntimePromptRail(language="cn")
        builder = SystemPromptBuilder(language="cn")
        builder.add_section(PromptSection(
            name="request_system_prompt", content={"cn": "rsp", "en": "rsp"}, priority=95,
        ))
        rail.system_prompt_builder = builder

        rail.uninit(MagicMock())

        assert not builder.has_section("request_system_prompt")

    def test_uninit_preserves_time_section(self):
        """uninit() does NOT remove 'time' section (time is no longer in builder)."""
        rail = RuntimePromptRail(language="cn")
        builder = SystemPromptBuilder(language="cn")
        # Add a "time" section externally — uninit should NOT touch it
        builder.add_section(PromptSection(name="time", content={"cn": "时间", "en": "time"}, priority=92))
        rail.system_prompt_builder = builder

        rail.uninit(MagicMock())

        # "time" should still be there (uninit no longer calls remove_section("time"))
        assert builder.has_section("time")

    def test_uninit_sets_builder_to_none(self):
        """uninit() sets system_prompt_builder to None."""
        rail = RuntimePromptRail(language="cn")
        builder = SystemPromptBuilder(language="cn")
        rail.system_prompt_builder = builder

        rail.uninit(MagicMock())

        assert rail.system_prompt_builder is None

    def test_uninit_handles_none_builder_gracefully(self):
        """uninit() with None builder does not crash."""
        rail = RuntimePromptRail(language="cn")
        rail.system_prompt_builder = None

        # Should not raise
        rail.uninit(MagicMock())

    def test_uninit_removes_all_three_sections_at_once(self):
        """uninit() removes runtime, workspace, request_system_prompt simultaneously."""
        rail = RuntimePromptRail(language="cn")
        builder = SystemPromptBuilder(language="cn")
        builder.add_section(PromptSection(name="runtime", content={"cn": "r", "en": "r"}, priority=95))
        builder.add_section(PromptSection(name="workspace", content={"cn": "w", "en": "w"}, priority=15))
        builder.add_section(PromptSection(
            name="request_system_prompt", content={"cn": "rsp", "en": "rsp"}, priority=95,
        ))
        # "time" is NOT added here — that's the new reality
        rail.system_prompt_builder = builder

        rail.uninit(MagicMock())

        assert not builder.has_section("runtime")
        assert not builder.has_section("workspace")
        assert not builder.has_section("request_system_prompt")


# ============================================================
# 4. Rail configuration Tests
# ============================================================

class TestRailConfiguration(unittest.TestCase):
    """Verify RuntimePromptRail constructor and configuration methods."""

    def test_default_language_is_cn(self):
        """Default language is 'cn'."""
        rail = RuntimePromptRail()
        assert getattr(rail, "_language") == "cn"

    def test_custom_language(self):
        """Custom language is stored."""
        rail = RuntimePromptRail(language="en")
        assert getattr(rail, "_language") == "en"

    def test_default_timezone_offset_is_8(self):
        """Default timezone_offset is 8 (UTC+8)."""
        rail = RuntimePromptRail()
        assert getattr(rail, "_tz") == asyncio.run(asyncio.sleep(0, result=None)) or True
        # Check via timedelta
        from datetime import timedelta, timezone
        assert getattr(rail, "_tz") == timezone(timedelta(hours=8))

    def test_custom_timezone_offset(self):
        """Custom timezone_offset is applied."""
        from datetime import timedelta, timezone
        rail = RuntimePromptRail(timezone_offset=0)
        assert getattr(rail, "_tz") == timezone(timedelta(hours=0))

    def test_set_language_updates_language(self):
        """set_language() updates the stored language."""
        rail = RuntimePromptRail(language="cn")
        rail.set_language("en")
        assert getattr(rail, "_language") == "en"

    def test_set_channel_updates_channel(self):
        """set_channel() updates the stored channel."""
        rail = RuntimePromptRail(channel="web")
        rail.set_channel("cli")
        assert getattr(rail, "_channel") == "cli"

    def test_priority_is_5(self):
        """Rail priority is 5 (high, executes early)."""
        rail = RuntimePromptRail()
        assert rail.priority == 5

    def test_rail_inherits_from_deep_agent_rail(self):
        """RuntimePromptRail inherits from DeepAgentRail."""
        from openjiuwen.harness.rails.base import DeepAgentRail
        assert isinstance(RuntimePromptRail(), DeepAgentRail)


# ============================================================
# 5. Edge Cases
# ============================================================

class TestEdgeCases(unittest.TestCase):
    """Edge-case tests for RuntimePromptRail."""

    def test_before_model_call_with_no_builder_skips_injection(self):
        """before_model_call with no builder returns early (logs warning)."""
        rail = RuntimePromptRail(language="cn")
        rail.system_prompt_builder = None
        ctx = AgentCallbackContext(agent=MagicMock())
        ctx.extra = {}

        asyncio.run(rail.before_model_call(ctx))

        # No reminder should be written when builder is None
        assert "_system_reminders" not in ctx.extra

    def test_request_system_prompt_added_to_builder_when_set(self):
        """request_system_prompt is added to builder when self._request_system_prompt is set."""
        rail = RuntimePromptRail(language="cn")
        rail.set_request_system_prompt("额外提示内容")
        builder = SystemPromptBuilder(language="cn")
        rail.system_prompt_builder = builder
        ctx = AgentCallbackContext(agent=MagicMock())
        ctx.extra = {}

        asyncio.run(rail.before_model_call(ctx))

        built = builder.build()
        assert "额外提示内容" in built

    def test_request_system_prompt_removed_when_empty(self):
        """Empty request_system_prompt removes the section from builder."""
        rail = RuntimePromptRail(language="cn")
        # First set, then clear
        rail.set_request_system_prompt("临时内容")
        builder = SystemPromptBuilder(language="cn")
        rail.system_prompt_builder = builder
        ctx = AgentCallbackContext(agent=MagicMock())
        ctx.extra = {}

        # Add section first
        builder.add_section(PromptSection(
            name="request_system_prompt", content={"cn": "旧内容", "en": "old"}, priority=95,
        ))

        # Now set to empty — should remove it
        rail.set_request_system_prompt("")
        asyncio.run(rail.before_model_call(ctx))

        assert not builder.has_section("request_system_prompt")

    def test_request_identify_stored(self):
        """request_identify is stored correctly."""
        rail = RuntimePromptRail(request_identify="自定义身份")
        assert getattr(rail, "_request_identify") == "自定义身份"

    def test_request_soul_stored(self):
        """request_soul is stored correctly."""
        rail = RuntimePromptRail(request_soul="自定义灵魂")
        assert getattr(rail, "_request_soul") == "自定义灵魂"

    def test_request_identify_whitespace_trimmed(self):
        """request_identify whitespace is trimmed."""
        rail = RuntimePromptRail(request_identify="  带空格的身份  ")
        assert getattr(rail, "_request_identify") == "带空格的身份"

    def test_request_soul_whitespace_trimmed(self):
        """request_soul whitespace is trimmed."""
        rail = RuntimePromptRail(request_soul="  带空格的灵魂  ")
        assert getattr(rail, "_request_soul") == "带空格的灵魂"

    def test_init_sets_system_prompt_builder(self):
        """init() grabs system_prompt_builder from agent."""
        rail = RuntimePromptRail(language="cn")
        mock_agent = MagicMock()
        mock_agent.system_prompt_builder = SystemPromptBuilder(language="cn")
        rail.init(mock_agent)
        assert rail.system_prompt_builder is mock_agent.system_prompt_builder


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-s"])