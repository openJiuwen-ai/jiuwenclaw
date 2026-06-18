# coding: utf-8
# pylint: disable=protected-access
"""Verification tests for SkillCredentialInjectionRail.

Tests cover:
1. _build_subprocess_env helper (command_ops.py)
2. Rail credential injection logic
3. No-overwrite behaviour for existing env keys
4. Non-shell tool skip
5. Hot-reload via update_skill_envs
6. Session ID resolution
7. JSON-string tool_args (production runtime shape from AbilityManager)
"""

import asyncio
import json
import os
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    ToolCallInputs,
)

from jiuwenclaw.agentserver.deep_agent.rails.skill_credential_injection_rail import (
    SkillCredentialInjectionRail,
)
from jiuwenclaw.agentserver.tools.command_tools import _build_subprocess_env


# ============================================================
# 1. _build_subprocess_env Tests
# ============================================================

class TestBuildSubprocessEnv(unittest.TestCase):
    """Verify _build_subprocess_env helper function."""

    def test_returns_none_when_extra_env_is_none(self):
        assert _build_subprocess_env(None) is None

    def test_returns_none_when_extra_env_is_empty(self):
        assert _build_subprocess_env({}) is None

    def test_merges_extra_env_into_os_environ_copy(self):
        extra = {"MY_TEST_KEY": "test_value_12345"}
        result = _build_subprocess_env(extra)
        assert result is not None
        assert result["MY_TEST_KEY"] == "test_value_12345"
        # Should also contain existing env vars
        assert "PATH" in result or "path" in result  # Windows uses lowercase

    def test_does_not_modify_original_os_environ(self):
        extra = {"ANOTHER_TEST_KEY": "should_not_leak"}
        _build_subprocess_env(extra)
        assert "ANOTHER_TEST_KEY" not in os.environ

    def test_extra_env_overrides_existing_keys(self):
        # Set a known env var
        os.environ["TEST_OVERRIDE_KEY"] = "original"
        try:
            result = _build_subprocess_env({"TEST_OVERRIDE_KEY": "overridden"})
            assert result["TEST_OVERRIDE_KEY"] == "overridden"
        finally:
            del os.environ["TEST_OVERRIDE_KEY"]


# ============================================================
# 2. SkillCredentialInjectionRail — Basic Injection Tests
# ============================================================

class TestCredentialInjection(unittest.TestCase):
    """Verify before_tool_call injects credentials correctly."""

    def _make_rail(self, skill_envs=None):
        return SkillCredentialInjectionRail(skill_envs=skill_envs)

    def _make_ctx(self, tool_name="mcp_exec_command", tool_args=None, conversation_id="test-session"):
        ctx = AgentCallbackContext(agent=MagicMock())
        inputs = ToolCallInputs(
            tool_call=MagicMock(),
            tool_name=tool_name,
            tool_args=tool_args or {"command": "echo hello"},
            tool_result=None,
            tool_msg=None,
        )
        inputs.conversation_id = conversation_id
        ctx.inputs = inputs
        return ctx

    @patch(
        "jiuwenclaw.agentserver.deep_agent.rails.skill_credential_injection_rail.get_session_active_skill"
    )
    def test_injects_credentials_into_tool_args(self, mock_get_skill):
        """When active skill has envs configured, they get injected into tool_args['env']."""
        mock_get_skill.return_value = "test-skill"
        rail = self._make_rail(skill_envs={
            "test-skill": {"API_KEY": "secret123", "BASE_URL": "https://api.example.com"}
        })
        ctx = self._make_ctx()
        asyncio.run(rail.before_tool_call(ctx))

        env = ctx.inputs.tool_args["env"]
        assert env["API_KEY"] == "secret123"
        assert env["BASE_URL"] == "https://api.example.com"

    @patch(
        "jiuwenclaw.agentserver.deep_agent.rails.skill_credential_injection_rail.get_session_active_skill"
    )
    def test_does_not_overwrite_existing_env_keys(self, mock_get_skill):
        """If tool_args already has env keys, they should NOT be overwritten."""
        mock_get_skill.return_value = "test-skill"
        rail = self._make_rail(skill_envs={
            "test-skill": {"API_KEY": "from_rail", "OTHER_KEY": "from_rail"}
        })
        ctx = self._make_ctx(tool_args={
            "command": "echo hello",
            "env": {"API_KEY": "user_provided"},
        })
        asyncio.run(rail.before_tool_call(ctx))

        env = ctx.inputs.tool_args["env"]
        assert env["API_KEY"] == "user_provided"  # Not overwritten
        assert env["OTHER_KEY"] == "from_rail"     # Injected

    @patch(
        "jiuwenclaw.agentserver.deep_agent.rails.skill_credential_injection_rail.get_session_active_skill"
    )
    def test_creates_env_dict_when_missing(self, mock_get_skill):
        """If tool_args has no 'env' key, one is created."""
        mock_get_skill.return_value = "test-skill"
        rail = self._make_rail(skill_envs={
            "test-skill": {"TOKEN": "abc"}
        })
        ctx = self._make_ctx(tool_args={"command": "echo hello"})
        asyncio.run(rail.before_tool_call(ctx))

        assert "env" in ctx.inputs.tool_args
        assert ctx.inputs.tool_args["env"]["TOKEN"] == "abc"


# ============================================================
# 3. Skip Non-Shell Tools
# ============================================================

class TestSkipNonShellTools(unittest.TestCase):
    """Verify that non-shell tools are skipped."""

    def _make_rail(self, skill_envs=None):
        return SkillCredentialInjectionRail(skill_envs=skill_envs)

    def _make_ctx(self, tool_name, tool_args=None):
        ctx = AgentCallbackContext(agent=MagicMock())
        inputs = ToolCallInputs(
            tool_call=MagicMock(),
            tool_name=tool_name,
            tool_args=tool_args or {"command": "echo hello"},
            tool_result=None,
            tool_msg=None,
        )
        inputs.conversation_id = "test-session"
        ctx.inputs = inputs
        return ctx

    @patch(
        "jiuwenclaw.agentserver.deep_agent.rails.skill_credential_injection_rail.get_session_active_skill"
    )
    def test_skips_web_search(self, mock_get_skill):
        mock_get_skill.return_value = "test-skill"
        rail = self._make_rail(skill_envs={"test-skill": {"KEY": "val"}})
        ctx = self._make_ctx(tool_name="web_search", tool_args={"query": "test"})
        asyncio.run(rail.before_tool_call(ctx))
        assert "env" not in ctx.inputs.tool_args

    @patch(
        "jiuwenclaw.agentserver.deep_agent.rails.skill_credential_injection_rail.get_session_active_skill"
    )
    def test_skips_skill_tool(self, mock_get_skill):
        mock_get_skill.return_value = "test-skill"
        rail = self._make_rail(skill_envs={"test-skill": {"KEY": "val"}})
        ctx = self._make_ctx(tool_name="skill_tool", tool_args={"skill_name": "foo"})
        asyncio.run(rail.before_tool_call(ctx))
        assert "env" not in ctx.inputs.tool_args

    @patch(
        "jiuwenclaw.agentserver.deep_agent.rails.skill_credential_injection_rail.get_session_active_skill"
    )
    def test_processes_bash_tool(self, mock_get_skill):
        """bash is in SHELL_PERMISSION_TOOLS, so it should be processed."""
        mock_get_skill.return_value = "test-skill"
        rail = self._make_rail(skill_envs={"test-skill": {"KEY": "val"}})
        ctx = self._make_ctx(tool_name="bash", tool_args={"command": "echo hi"})
        asyncio.run(rail.before_tool_call(ctx))
        assert ctx.inputs.tool_args["env"]["KEY"] == "val"

    @patch(
        "jiuwenclaw.agentserver.deep_agent.rails.skill_credential_injection_rail.get_session_active_skill"
    )
    def test_processes_mcp_exec_command(self, mock_get_skill):
        """mcp_exec_command is in SHELL_PERMISSION_TOOLS."""
        mock_get_skill.return_value = "test-skill"
        rail = self._make_rail(skill_envs={"test-skill": {"KEY": "val"}})
        ctx = self._make_ctx(tool_name="mcp_exec_command", tool_args={"command": "echo hi"})
        asyncio.run(rail.before_tool_call(ctx))
        assert ctx.inputs.tool_args["env"]["KEY"] == "val"


# ============================================================
# 4. No Active Skill / No Configured Envs
# ============================================================

class TestNoInjectionScenarios(unittest.TestCase):
    """Verify no injection when there's no active skill or no configured envs."""

    def _make_rail(self, skill_envs=None):
        return SkillCredentialInjectionRail(skill_envs=skill_envs)

    def _make_ctx(self, tool_name="mcp_exec_command", tool_args=None):
        ctx = AgentCallbackContext(agent=MagicMock())
        inputs = ToolCallInputs(
            tool_call=MagicMock(),
            tool_name=tool_name,
            tool_args=tool_args or {"command": "echo hello"},
            tool_result=None,
            tool_msg=None,
        )
        inputs.conversation_id = "test-session"
        ctx.inputs = inputs
        return ctx

    @patch(
        "jiuwenclaw.agentserver.deep_agent.rails.skill_credential_injection_rail.get_session_active_skill"
    )
    def test_no_injection_when_no_active_skill(self, mock_get_skill):
        mock_get_skill.return_value = None
        rail = self._make_rail(skill_envs={"test-skill": {"KEY": "val"}})
        ctx = self._make_ctx()
        asyncio.run(rail.before_tool_call(ctx))
        assert "env" not in ctx.inputs.tool_args

    @patch(
        "jiuwenclaw.agentserver.deep_agent.rails.skill_credential_injection_rail.get_session_active_skill"
    )
    def test_no_injection_when_skill_not_in_config(self, mock_get_skill):
        mock_get_skill.return_value = "unknown-skill"
        rail = self._make_rail(skill_envs={"other-skill": {"KEY": "val"}})
        ctx = self._make_ctx()
        asyncio.run(rail.before_tool_call(ctx))
        assert "env" not in ctx.inputs.tool_args

    @patch(
        "jiuwenclaw.agentserver.deep_agent.rails.skill_credential_injection_rail.get_session_active_skill"
    )
    def test_no_injection_when_skill_envs_empty(self, mock_get_skill):
        mock_get_skill.return_value = "test-skill"
        rail = self._make_rail(skill_envs={"test-skill": {}})
        ctx = self._make_ctx()
        asyncio.run(rail.before_tool_call(ctx))
        assert "env" not in ctx.inputs.tool_args

    def test_no_injection_when_rail_has_no_skill_envs(self):
        rail = self._make_rail(skill_envs=None)
        ctx = self._make_ctx()
        # No need to mock get_session_active_skill since _get_skill_envs will return {}
        asyncio.run(rail.before_tool_call(ctx))
        assert "env" not in ctx.inputs.tool_args


# ============================================================
# 5. Hot-Reload via update_skill_envs
# ============================================================

class TestHotReload(unittest.TestCase):
    """Verify update_skill_envs replaces internal state correctly."""

    def test_update_replaces_skill_envs(self):
        rail = SkillCredentialInjectionRail(skill_envs={"old-skill": {"KEY": "old"}})
        assert rail._get_skill_envs("old-skill") == {"KEY": "old"}

        rail.update_skill_envs({"new-skill": {"TOKEN": "new"}})
        assert rail._get_skill_envs("old-skill") == {}
        assert rail._get_skill_envs("new-skill") == {"TOKEN": "new"}

    def test_update_with_none_clears_envs(self):
        rail = SkillCredentialInjectionRail(skill_envs={"skill": {"K": "V"}})
        rail.update_skill_envs(None)
        assert rail._get_skill_envs("skill") == {}

    def test_update_with_empty_dict_clears_envs(self):
        rail = SkillCredentialInjectionRail(skill_envs={"skill": {"K": "V"}})
        rail.update_skill_envs({})
        assert rail._get_skill_envs("skill") == {}


# ============================================================
# 6. Session ID Resolution
# ============================================================

class TestSessionIdResolution(unittest.TestCase):
    """Verify _resolve_session_id uses the expected strategy."""

    def test_preset_session_id_takes_priority(self):
        rail = SkillCredentialInjectionRail(preset_session_id="preset-123")
        ctx = AgentCallbackContext(agent=MagicMock())
        ctx.inputs = ToolCallInputs(
            tool_call=MagicMock(), tool_name="bash",
            tool_args={}, tool_result=None, tool_msg=None,
        )
        ctx.inputs.conversation_id = "from-context"
        assert rail._resolve_session_id(ctx) == "preset-123"

    def test_conversation_id_used_when_no_preset(self):
        rail = SkillCredentialInjectionRail()
        ctx = AgentCallbackContext(agent=MagicMock())
        ctx.inputs = ToolCallInputs(
            tool_call=MagicMock(), tool_name="bash",
            tool_args={}, tool_result=None, tool_msg=None,
        )
        ctx.inputs.conversation_id = "conv-456"
        assert rail._resolve_session_id(ctx) == "conv-456"

    def test_fallback_to_default(self):
        rail = SkillCredentialInjectionRail()
        ctx = AgentCallbackContext(agent=MagicMock())
        ctx.inputs = ToolCallInputs(
            tool_call=MagicMock(), tool_name="bash",
            tool_args={}, tool_result=None, tool_msg=None,
        )
        # No conversation_id set
        assert rail._resolve_session_id(ctx) == "default"

    def test_uses_contextvar_when_conversation_id_empty(self):
        """Production regression: conversation_id is often empty at tool dispatch;
        the real session id is carried by ``_current_session_var`` (set by
        ``SkillComplianceRail.before_invoke``). The rail MUST consult it.
        """
        from jiuwenclaw.agentserver.deep_agent.rails import (
            skill_compliance_rail as scr,
        )
        rail = SkillCredentialInjectionRail()
        ctx = AgentCallbackContext(agent=MagicMock())
        ctx.inputs = ToolCallInputs(
            tool_call=MagicMock(), tool_name="bash",
            tool_args={}, tool_result=None, tool_msg=None,
        )
        # conversation_id deliberately unset
        token = scr._current_session_var.set("ctxvar-session-789")
        try:
            assert rail._resolve_session_id(ctx) == "ctxvar-session-789"
        finally:
            scr._current_session_var.reset(token)

    def test_contextvar_takes_precedence_over_default(self):
        from jiuwenclaw.agentserver.deep_agent.rails import (
            skill_compliance_rail as scr,
        )
        rail = SkillCredentialInjectionRail()
        ctx = AgentCallbackContext(agent=MagicMock())
        ctx.inputs = ToolCallInputs(
            tool_call=MagicMock(), tool_name="bash",
            tool_args={}, tool_result=None, tool_msg=None,
        )
        token = scr._current_session_var.set("from-ctxvar")
        try:
            # No conversation_id, no preset — should NOT fall through to "default"
            assert rail._resolve_session_id(ctx) != "default"
            assert rail._resolve_session_id(ctx) == "from-ctxvar"
        finally:
            scr._current_session_var.reset(token)


# ============================================================
# 7. Priority Verification
# ============================================================

class TestRailPriority(unittest.TestCase):
    """Verify rail priority is set correctly."""

    def test_priority_is_5(self):
        rail = SkillCredentialInjectionRail()
        assert rail.priority == 5

    def test_priority_lower_than_permission_rail(self):
        """SkillCredentialInjectionRail (5) should run before PermissionInterruptRail (90)."""
        rail = SkillCredentialInjectionRail()
        assert rail.priority < 90


# ============================================================
# 8. Import Chain Verification
# ============================================================

class TestImportChain(unittest.TestCase):
    """Verify the rail is properly exported from the package."""

    def test_importable_from_rails_package(self):
        from jiuwenclaw.agentserver.deep_agent.rails import SkillCredentialInjectionRail as Rail
        assert Rail is SkillCredentialInjectionRail

    def test_in_all_exports(self):
        import jiuwenclaw.agentserver.deep_agent.rails as rails_pkg
        assert "SkillCredentialInjectionRail" in rails_pkg.__all__


# ============================================================
# 9. JSON-string tool_args (production runtime shape from AbilityManager)
# ============================================================

class TestStringToolArgs(unittest.TestCase):
    """At runtime AbilityManager constructs ToolCallInputs with tool_args as a
    JSON-encoded string (the raw ToolCall.arguments produced by the LLM).
    The rail must parse it, inject credentials, and write the dict back so
    that downstream dispatch propagates the env vars.
    """

    def _make_rail(self, skill_envs=None):
        return SkillCredentialInjectionRail(skill_envs=skill_envs)

    def _make_ctx(self, tool_name="mcp_exec_command", tool_args=None, conversation_id="test-session"):
        ctx = AgentCallbackContext(agent=MagicMock())
        inputs = ToolCallInputs(
            tool_call=MagicMock(),
            tool_name=tool_name,
            tool_args=tool_args,
            tool_result=None,
            tool_msg=None,
        )
        inputs.conversation_id = conversation_id
        ctx.inputs = inputs
        return ctx

    @patch(
        "jiuwenclaw.agentserver.deep_agent.rails.skill_credential_injection_rail.get_session_active_skill"
    )
    def test_injects_into_json_string(self, mock_get_skill):
        mock_get_skill.return_value = "test-skill"
        rail = self._make_rail(skill_envs={
            "test-skill": {"API_KEY": "secret123", "BASE_URL": "https://api.example.com"}
        })
        tool_args_str = json.dumps({"command": "echo hello"})
        ctx = self._make_ctx(tool_args=tool_args_str)

        asyncio.run(rail.before_tool_call(ctx))

        # Rail must convert tool_args to a dict and write back to inputs.tool_args
        assert isinstance(ctx.inputs.tool_args, dict)
        env = ctx.inputs.tool_args["env"]
        assert env["API_KEY"] == "secret123"
        assert env["BASE_URL"] == "https://api.example.com"
        # Original command must survive
        assert ctx.inputs.tool_args["command"] == "echo hello"

    @patch(
        "jiuwenclaw.agentserver.deep_agent.rails.skill_credential_injection_rail.get_session_active_skill"
    )
    def test_does_not_overwrite_existing_env_in_string(self, mock_get_skill):
        mock_get_skill.return_value = "test-skill"
        rail = self._make_rail(skill_envs={
            "test-skill": {"API_KEY": "from_rail", "OTHER_KEY": "from_rail"}
        })
        tool_args_str = json.dumps({
            "command": "echo hello",
            "env": {"API_KEY": "user_provided"},
        })
        ctx = self._make_ctx(tool_args=tool_args_str)

        asyncio.run(rail.before_tool_call(ctx))

        env = ctx.inputs.tool_args["env"]
        assert env["API_KEY"] == "user_provided"  # Not overwritten
        assert env["OTHER_KEY"] == "from_rail"     # Injected

    @patch(
        "jiuwenclaw.agentserver.deep_agent.rails.skill_credential_injection_rail.get_session_active_skill"
    )
    def test_invalid_json_string_is_skipped(self, mock_get_skill):
        mock_get_skill.return_value = "test-skill"
        rail = self._make_rail(skill_envs={"test-skill": {"KEY": "val"}})
        ctx = self._make_ctx(tool_args="not-a-json-string")
        original = ctx.inputs.tool_args

        asyncio.run(rail.before_tool_call(ctx))

        # Unparseable JSON: rail leaves tool_args untouched (no env injected)
        assert ctx.inputs.tool_args is original
        assert isinstance(ctx.inputs.tool_args, str)

    @patch(
        "jiuwenclaw.agentserver.deep_agent.rails.skill_credential_injection_rail.get_session_active_skill"
    )
    def test_json_array_string_is_skipped(self, mock_get_skill):
        """JSON that parses to non-dict (e.g. list) must not be mutated."""
        mock_get_skill.return_value = "test-skill"
        rail = self._make_rail(skill_envs={"test-skill": {"KEY": "val"}})
        ctx = self._make_ctx(tool_args='[1, 2, 3]')
        original = ctx.inputs.tool_args

        asyncio.run(rail.before_tool_call(ctx))

        assert ctx.inputs.tool_args is original


if __name__ == "__main__":
    unittest.main(verbosity=2)
