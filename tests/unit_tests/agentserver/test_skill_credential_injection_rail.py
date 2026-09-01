# coding: utf-8
# pylint: disable=protected-access
"""Tests for skill credential injection and coalesce helpers."""

import asyncio
import json
import os
import unittest
from unittest.mock import MagicMock, patch

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs

from jiuwenswarm.agents.harness.common.rails.skill_credential_injection_rail import (
    SkillCredentialInjectionRail,
    coalesce_config_skill_envs,
    coalesce_skill_envs,
)
from jiuwenswarm.agents.harness.common.tools.command_tools import _build_subprocess_env


class TestBuildSubprocessEnv(unittest.TestCase):
    def test_returns_none_when_extra_env_is_none(self):
        assert _build_subprocess_env(None) is None

    def test_returns_none_when_extra_env_is_empty(self):
        assert _build_subprocess_env({}) is None

    def test_merges_extra_env_into_os_environ_copy(self):
        extra = {"MY_TEST_KEY": "test_value_12345"}
        result = _build_subprocess_env(extra)
        assert result is not None
        assert result["MY_TEST_KEY"] == "test_value_12345"
        assert "PATH" in result or "path" in result

    def test_does_not_modify_original_os_environ(self):
        extra = {"ANOTHER_TEST_KEY": "should_not_leak"}
        _build_subprocess_env(extra)
        assert "ANOTHER_TEST_KEY" not in os.environ


class TestCoalesceSkillEnvs(unittest.TestCase):
    def test_empty_incoming_keeps_current(self):
        current = {"hwocr": {"HWOCR_AK": "ak"}}
        assert coalesce_skill_envs({}, current) == current
        assert coalesce_skill_envs(None, current) == current

    def test_catalog_clear_with_skill_key_wins(self):
        current = {"hwocr": {"HWOCR_AK": "ak"}}
        incoming = {"hwocr": {"HWOCR_AK": ""}}
        assert coalesce_skill_envs(incoming, current) == incoming

    def test_config_placeholder_keeps_previous_react_block(self):
        previous = {
            "react": {
                "skill_envs": {"hwocr": {"HWOCR_AK": "ak"}},
                "agent_name": "main",
            }
        }
        yaml_reload = {"react": {"skill_envs": {}, "agent_name": "main"}}
        merged = coalesce_config_skill_envs(yaml_reload, previous)
        assert merged["react"]["skill_envs"]["hwocr"]["HWOCR_AK"] == "ak"

    def test_config_catalog_clear_replaces(self):
        previous = {"react": {"skill_envs": {"hwocr": {"HWOCR_AK": "ak"}}}}
        catalog = {"react": {"skill_envs": {"hwocr": {"HWOCR_AK": ""}}}}
        merged = coalesce_config_skill_envs(catalog, previous)
        assert merged["react"]["skill_envs"]["hwocr"]["HWOCR_AK"] == ""


class TestCredentialInjection(unittest.TestCase):
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
        "jiuwenswarm.agents.harness.common.rails.skill_credential_injection_rail.get_session_active_skill"
    )
    def test_injects_credentials_into_tool_args(self, mock_get_skill):
        mock_get_skill.return_value = "hwocr"
        rail = self._make_rail(
            skill_envs={"hwocr": {"HWOCR_AK": "ak", "HWOCR_SK": "sk"}}
        )
        ctx = self._make_ctx()
        asyncio.run(rail.before_tool_call(ctx))
        env = ctx.inputs.tool_args["env"]
        assert env["HWOCR_AK"] == "ak"
        assert env["HWOCR_SK"] == "sk"

    @patch(
        "jiuwenswarm.agents.harness.common.rails.skill_credential_injection_rail.get_session_active_skill"
    )
    def test_does_not_overwrite_existing_env_keys(self, mock_get_skill):
        mock_get_skill.return_value = "hwocr"
        rail = self._make_rail(
            skill_envs={"hwocr": {"HWOCR_AK": "from_rail", "HWOCR_SK": "from_rail"}}
        )
        ctx = self._make_ctx(
            tool_args={"command": "echo hello", "env": {"HWOCR_AK": "user_provided"}}
        )
        asyncio.run(rail.before_tool_call(ctx))
        env = ctx.inputs.tool_args["env"]
        assert env["HWOCR_AK"] == "user_provided"
        assert env["HWOCR_SK"] == "from_rail"

    @patch(
        "jiuwenswarm.agents.harness.common.rails.skill_credential_injection_rail.get_session_active_skill"
    )
    def test_injects_from_json_string_tool_args(self, mock_get_skill):
        mock_get_skill.return_value = "hwocr"
        rail = self._make_rail(skill_envs={"hwocr": {"HWOCR_AK": "ak"}})
        ctx = self._make_ctx(tool_args=json.dumps({"command": "echo hello"}))
        asyncio.run(rail.before_tool_call(ctx))
        assert isinstance(ctx.inputs.tool_args, dict)
        assert ctx.inputs.tool_args["env"]["HWOCR_AK"] == "ak"

    @patch(
        "jiuwenswarm.agents.harness.common.rails.skill_credential_injection_rail.get_session_active_skill"
    )
    def test_bash_injects_env_into_tool_args(self, mock_get_skill):
        mock_get_skill.return_value = "hwocr"
        rail = self._make_rail(
            skill_envs={"hwocr": {"HWOCR_AK": "ak", "HWOCR_SK": "sk"}}
        )
        ctx = self._make_ctx(tool_name="bash", tool_args={"command": "& hwocr.exe run"})
        asyncio.run(rail.before_tool_call(ctx))
        env = ctx.inputs.tool_args["env"]
        assert env["HWOCR_AK"] == "ak"
        assert env["HWOCR_SK"] == "sk"

    @patch(
        "jiuwenswarm.agents.harness.common.rails.skill_credential_injection_rail.get_session_active_skill"
    )
    def test_skips_when_no_active_skill(self, mock_get_skill):
        mock_get_skill.return_value = None
        rail = self._make_rail(skill_envs={"hwocr": {"HWOCR_AK": "ak"}})
        ctx = self._make_ctx(tool_args={"command": "echo hello"})
        asyncio.run(rail.before_tool_call(ctx))
        assert "env" not in ctx.inputs.tool_args


if __name__ == "__main__":
    unittest.main(verbosity=2)
