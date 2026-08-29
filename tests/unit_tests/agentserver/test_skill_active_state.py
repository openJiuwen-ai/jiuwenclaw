# coding: utf-8
# pylint: disable=protected-access
"""Tests for skill active-state session id + HITL preserve behavior."""

import asyncio
import unittest
from unittest.mock import MagicMock

from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    InvokeInputs,
    ToolCallInputs,
)

from jiuwenswarm.agents.harness.common.rails.skill_active_state import (
    _CHAT_SEND_SOURCE_EXTRA_KEY,
    _DEFAULT_SESSION_ID,
    _PRESERVE_SKILL_ACTIVE_EXTRA_KEY,
    _SESSION_ID_EXTRA_KEY,
    SkillActiveStateRail,
    clear_session_skill_state,
    get_session_active_skill,
    is_interrupt_resume_source,
    resolve_skill_session_id,
    should_preserve_skill_active_from_params,
)
from jiuwenswarm.agents.harness.common.rails.skill_credential_injection_rail import (
    SkillCredentialInjectionRail,
)


class TestResolveSkillSessionId(unittest.TestCase):
    def _ctx(self, *, conversation_id=None, session_id=None, extra=None):
        ctx = AgentCallbackContext(agent=MagicMock())
        inputs = ToolCallInputs(
            tool_call=MagicMock(),
            tool_name="bash",
            tool_args={"command": "echo hi"},
            tool_result=None,
            tool_msg=None,
        )
        if conversation_id is not None:
            inputs.conversation_id = conversation_id
        ctx.inputs = inputs
        if session_id is not None:
            session = MagicMock()
            session.get_session_id.return_value = session_id
            ctx.session = session
        if extra is not None:
            ctx.extra = extra
        else:
            ctx.extra = {}
        return ctx

    def test_preset_wins(self):
        ctx = self._ctx(conversation_id="from-inputs")
        assert resolve_skill_session_id(ctx, "officeclaw_preset") == "officeclaw_preset"

    def test_inputs_conversation_id(self):
        ctx = self._ctx(conversation_id="officeclaw_from_inputs")
        assert resolve_skill_session_id(ctx) == "officeclaw_from_inputs"

    def test_session_object(self):
        ctx = self._ctx(session_id="officeclaw_from_session")
        assert resolve_skill_session_id(ctx) == "officeclaw_from_session"

    def test_ctx_extra_shared_key(self):
        ctx = self._ctx(extra={_SESSION_ID_EXTRA_KEY: "officeclaw_from_extra"})
        assert resolve_skill_session_id(ctx) == "officeclaw_from_extra"

    def test_fallback_default(self):
        ctx = self._ctx()
        assert resolve_skill_session_id(ctx) == _DEFAULT_SESSION_ID


class TestPreserveSkillActiveParams(unittest.TestCase):
    def test_interrupt_sources(self):
        assert is_interrupt_resume_source("permission_interrupt")
        assert is_interrupt_resume_source("confirm_interrupt")
        assert is_interrupt_resume_source("ask_user_interrupt")
        # Evolution / forward-compat sources are out of OCR/HITL scope.
        assert not is_interrupt_resume_source("evolution_interrupt")
        assert not is_interrupt_resume_source("skill_evolution_approval")
        assert not is_interrupt_resume_source("custom_interrupt")
        assert not is_interrupt_resume_source("user")
        assert not is_interrupt_resume_source("")

    def test_params_permission_resume(self):
        assert should_preserve_skill_active_from_params(
            {
                "source": "permission_interrupt",
                "request_id": "req-1",
                "query": "",
                "answers": [{"x": 1}],
            }
        )

    def test_params_answers_only_no_source(self):
        assert not should_preserve_skill_active_from_params(
            {"query": "", "answers": [{"selected_options": ["本次允许"]}]}
        )

    def test_params_source_without_request_id_still_preserves(self):
        # Source alone is enough to keep active skill across security HITL.
        assert should_preserve_skill_active_from_params(
            {
                "source": "permission_interrupt",
                "query": "",
                "answers": [{"x": 1}],
            }
        )
        assert should_preserve_skill_active_from_params(
            {"source": "permission_interrupt", "query": ""}
        )

    def test_params_evolution_source_not_preserved_here(self):
        assert not should_preserve_skill_active_from_params(
            {
                "source": "evolution_interrupt",
                "request_id": "req-1",
                "answers": [{"x": 1}],
            }
        )

    def test_params_new_user_query(self):
        assert not should_preserve_skill_active_from_params(
            {"query": "再用 hwocr 识别", "source": ""}
        )


class TestSkillActiveStateRailPreset(unittest.TestCase):
    def tearDown(self):
        clear_session_skill_state("officeclaw_preset_sess")
        clear_session_skill_state(_DEFAULT_SESSION_ID)

    def _activate_ctx(self, skill_name="hwocr"):
        ctx = AgentCallbackContext(agent=MagicMock())
        tool_call = MagicMock()
        tool_call.arguments = {"skill_name": skill_name}
        tool_msg = MagicMock()
        tool_msg.metadata = {"skill_name": skill_name}
        inputs = ToolCallInputs(
            tool_call=tool_call,
            tool_name="skill_tool",
            tool_args={"skill_name": skill_name},
            tool_result=None,
            tool_msg=tool_msg,
        )
        ctx.inputs = inputs
        ctx.extra = {}
        return ctx

    def test_preset_session_used_when_tool_inputs_lack_conversation_id(self):
        rail = SkillActiveStateRail(session_id="officeclaw_preset_sess")
        ctx = self._activate_ctx()
        asyncio.run(rail.after_tool_call(ctx))
        assert get_session_active_skill("officeclaw_preset_sess") == "hwocr"
        assert get_session_active_skill(_DEFAULT_SESSION_ID) is None

    def test_before_invoke_binds_extra(self):
        rail = SkillActiveStateRail(session_id="officeclaw_preset_sess")
        ctx = self._activate_ctx()
        ctx.inputs = InvokeInputs(query="hello", conversation_id="officeclaw_preset_sess")
        asyncio.run(rail.before_invoke(ctx))
        assert ctx.extra[_SESSION_ID_EXTRA_KEY] == "officeclaw_preset_sess"


class TestHitlPreserveActiveSkill(unittest.TestCase):
    sid = "officeclaw_hitl_sess"

    def tearDown(self):
        clear_session_skill_state(self.sid)

    def _rail(self):
        return SkillActiveStateRail(session_id=self.sid)

    def _activate(self, rail):
        ctx = AgentCallbackContext(agent=MagicMock())
        tool_call = MagicMock()
        tool_call.arguments = {"skill_name": "hwocr"}
        tool_msg = MagicMock()
        tool_msg.metadata = {"skill_name": "hwocr"}
        ctx.inputs = ToolCallInputs(
            tool_call=tool_call,
            tool_name="skill_tool",
            tool_args={"skill_name": "hwocr"},
            tool_result=None,
            tool_msg=tool_msg,
        )
        ctx.extra = {}
        asyncio.run(rail.after_tool_call(ctx))

    def _invoke_ctx(self, *, query="hello", preserve=False, source=""):
        ctx = AgentCallbackContext(agent=MagicMock())
        run_context = MagicMock()
        run_context.extra = {
            _PRESERVE_SKILL_ACTIVE_EXTRA_KEY: preserve,
        }
        if source:
            run_context.extra[_CHAT_SEND_SOURCE_EXTRA_KEY] = source
        ctx.inputs = InvokeInputs(
            query=query,
            conversation_id=self.sid,
            run_context=run_context,
        )
        ctx.extra = {}
        return ctx

    def test_after_invoke_does_not_clear(self):
        rail = self._rail()
        self._activate(rail)
        ctx = self._invoke_ctx(preserve=False)
        asyncio.run(rail.after_invoke(ctx))
        assert get_session_active_skill(self.sid) == "hwocr"

    def test_permission_resume_before_invoke_keeps_active(self):
        rail = self._rail()
        self._activate(rail)
        ctx = self._invoke_ctx(
            query="",
            preserve=True,
            source="permission_interrupt",
        )
        asyncio.run(rail.before_invoke(ctx))
        assert get_session_active_skill(self.sid) == "hwocr"

    def test_ask_user_resume_keeps_active(self):
        rail = self._rail()
        self._activate(rail)
        ctx = self._invoke_ctx(preserve=True, source="ask_user_interrupt")
        asyncio.run(rail.before_invoke(ctx))
        assert get_session_active_skill(self.sid) == "hwocr"

    def test_new_user_task_before_invoke_clears(self):
        rail = self._rail()
        self._activate(rail)
        ctx = self._invoke_ctx(query="下一题", preserve=False)
        asyncio.run(rail.before_invoke(ctx))
        assert get_session_active_skill(self.sid) is None

    def test_resume_then_inject_bash(self):
        rail = self._rail()
        inject = SkillCredentialInjectionRail(
            skill_envs={"hwocr": {"HWOCR_AK": "ak", "HWOCR_SK": "sk"}},
            preset_session_id=self.sid,
        )
        self._activate(rail)
        # Simulate interrupted invoke ending without clearing.
        asyncio.run(rail.after_invoke(self._invoke_ctx(preserve=False)))
        # Permission resume.
        asyncio.run(
            rail.before_invoke(
                self._invoke_ctx(preserve=True, source="permission_interrupt")
            )
        )
        bash_ctx = AgentCallbackContext(agent=MagicMock())
        bash_ctx.inputs = ToolCallInputs(
            tool_call=MagicMock(),
            tool_name="bash",
            tool_args={"command": "hwocr.exe general-text"},
            tool_result=None,
            tool_msg=None,
        )
        bash_ctx.extra = {}
        asyncio.run(inject.before_tool_call(bash_ctx))
        assert bash_ctx.inputs.tool_args["env"]["HWOCR_AK"] == "ak"

    def test_skill_complete_clears(self):
        rail = self._rail()
        self._activate(rail)
        ctx = AgentCallbackContext(agent=MagicMock())
        tool_call = MagicMock()
        tool_call.arguments = {"skill_name": "hwocr"}
        tool_msg = MagicMock()
        tool_msg.metadata = {}
        ctx.inputs = ToolCallInputs(
            tool_call=tool_call,
            tool_name="skill_complete",
            tool_args={"skill_name": "hwocr"},
            tool_result=None,
            tool_msg=tool_msg,
        )
        ctx.extra = {}
        asyncio.run(rail.after_tool_call(ctx))
        assert get_session_active_skill(self.sid) is None


class TestCredentialInjectionUsesPreset(unittest.TestCase):
    def tearDown(self):
        clear_session_skill_state("officeclaw_inject_sess")

    def test_injects_when_active_skill_keyed_by_preset(self):
        active = SkillActiveStateRail(session_id="officeclaw_inject_sess")
        inject = SkillCredentialInjectionRail(
            skill_envs={"hwocr": {"HWOCR_AK": "ak", "HWOCR_SK": "sk"}},
            preset_session_id="officeclaw_inject_sess",
        )

        activate_ctx = AgentCallbackContext(agent=MagicMock())
        tool_call = MagicMock()
        tool_call.arguments = {"skill_name": "hwocr"}
        tool_msg = MagicMock()
        tool_msg.metadata = {"skill_name": "hwocr"}
        activate_ctx.inputs = ToolCallInputs(
            tool_call=tool_call,
            tool_name="skill_tool",
            tool_args={"skill_name": "hwocr"},
            tool_result=None,
            tool_msg=tool_msg,
        )
        activate_ctx.extra = {}
        asyncio.run(active.after_tool_call(activate_ctx))

        bash_ctx = AgentCallbackContext(agent=MagicMock())
        bash_ctx.inputs = ToolCallInputs(
            tool_call=MagicMock(),
            tool_name="bash",
            tool_args={"command": "hwocr.exe general-text"},
            tool_result=None,
            tool_msg=None,
        )
        bash_ctx.extra = {}
        asyncio.run(inject.before_tool_call(bash_ctx))
        env = bash_ctx.inputs.tool_args["env"]
        assert env["HWOCR_AK"] == "ak"
        assert env["HWOCR_SK"] == "sk"


class TestAdoptDefaultActiveSkill(unittest.TestCase):
    def tearDown(self):
        clear_session_skill_state("officeclaw_adopt_sess")
        clear_session_skill_state(_DEFAULT_SESSION_ID)

    def test_inject_adopts_default_orphan(self):
        from jiuwenswarm.agents.harness.common.rails.skill_active_state import (
            adopt_default_active_skill,
        )

        # Simulate legacy activation under default (no conversation_id / preset).
        default_rail = SkillActiveStateRail()
        ctx = AgentCallbackContext(agent=MagicMock())
        tool_call = MagicMock()
        tool_call.arguments = {"skill_name": "hwocr"}
        tool_msg = MagicMock()
        tool_msg.metadata = {"skill_name": "hwocr"}
        ctx.inputs = ToolCallInputs(
            tool_call=tool_call,
            tool_name="skill_tool",
            tool_args={"skill_name": "hwocr"},
            tool_result=None,
            tool_msg=tool_msg,
        )
        ctx.extra = {}
        asyncio.run(default_rail.after_tool_call(ctx))
        assert get_session_active_skill(_DEFAULT_SESSION_ID) == "hwocr"

        assert adopt_default_active_skill("officeclaw_adopt_sess") == "hwocr"
        assert get_session_active_skill("officeclaw_adopt_sess") == "hwocr"
        assert get_session_active_skill(_DEFAULT_SESSION_ID) is None

        inject = SkillCredentialInjectionRail(
            skill_envs={"hwocr": {"HWOCR_AK": "ak"}},
            preset_session_id="officeclaw_adopt_sess",
        )
        bash_ctx = AgentCallbackContext(agent=MagicMock())
        bash_ctx.inputs = ToolCallInputs(
            tool_call=MagicMock(),
            tool_name="bash",
            tool_args={"command": "hwocr.exe general-text"},
            tool_result=None,
            tool_msg=None,
        )
        bash_ctx.extra = {}
        # Re-seed orphan for inject path (adopt already moved it).
        clear_session_skill_state("officeclaw_adopt_sess")
        asyncio.run(default_rail.after_tool_call(ctx))
        asyncio.run(inject.before_tool_call(bash_ctx))
        assert bash_ctx.inputs.tool_args["env"]["HWOCR_AK"] == "ak"


class TestFalsePreserveFlagWithInterruptSource(unittest.TestCase):
    sid = "officeclaw_false_preserve"

    def tearDown(self):
        clear_session_skill_state(self.sid)

    def test_interrupt_source_overrides_false_preserve_flag(self):
        rail = SkillActiveStateRail(session_id=self.sid)
        ctx = AgentCallbackContext(agent=MagicMock())
        tool_call = MagicMock()
        tool_call.arguments = {"skill_name": "hwocr"}
        tool_msg = MagicMock()
        tool_msg.metadata = {"skill_name": "hwocr"}
        ctx.inputs = ToolCallInputs(
            tool_call=tool_call,
            tool_name="skill_tool",
            tool_args={"skill_name": "hwocr"},
            tool_result=None,
            tool_msg=tool_msg,
        )
        ctx.extra = {}
        asyncio.run(rail.after_tool_call(ctx))

        run_context = MagicMock()
        run_context.extra = {
            _PRESERVE_SKILL_ACTIVE_EXTRA_KEY: False,
            _CHAT_SEND_SOURCE_EXTRA_KEY: "permission_interrupt",
        }
        invoke_ctx = AgentCallbackContext(agent=MagicMock())
        invoke_ctx.inputs = InvokeInputs(
            query="",
            conversation_id=self.sid,
            run_context=run_context,
        )
        invoke_ctx.extra = {}
        asyncio.run(rail.before_invoke(invoke_ctx))
        assert get_session_active_skill(self.sid) == "hwocr"


if __name__ == "__main__":
    unittest.main()
