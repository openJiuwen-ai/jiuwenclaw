# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Resume replay: 当 ask_user 等工具因非确定性参数导致 tool_call_id 变化时，
仍能按 tool_name 对齐注入 user_input，避免循环中断。

复现 officeclaw_a873fc300d433068be0b0741 的根因：pptx-craft 的 ask_user 节点
在 resume 重放时 LLM 重新生成主题候选 → questions args 变 → args_hash 变 →
tool_call_id 与中断时不一致 → _consume_pending_resume_input 返回 None →
ask_user_rail user_input=None → 再次 interrupt → 死循环。
"""

from __future__ import annotations

from unittest.mock import MagicMock

from jiuwenswarm.server.runtime.skill_turbo.executor import (
    SkillTurboExecutor,
    _parse_tool_name_from_call_id,
)


def _make_executor() -> SkillTurboExecutor:
    env = MagicMock()
    env.config = {}
    env.skill_code_import_prefixes = (
        "jiuwenswarm.server.runtime.skill_turbo.skill_codes",
    )
    return SkillTurboExecutor(environment=env)


class TestParseToolNameFromCallId:
    def test_parses_standard_id(self):
        assert (
            _parse_tool_name_from_call_id("skill_turbo-tc-ask_user-605c02b1-0")
            == "ask_user"
        )

    def test_parses_tool_name_with_dash(self):
        assert (
            _parse_tool_name_from_call_id("skill_turbo-tc-write_file-abcd1234-2")
            == "write_file"
        )

    def test_returns_none_for_non_skill_turbo_id(self):
        assert _parse_tool_name_from_call_id("other-prefix-ask_user-1234-0") is None

    def test_returns_none_for_empty_or_short(self):
        assert _parse_tool_name_from_call_id("") is None
        assert _parse_tool_name_from_call_id("skill_turbo-tc-") is None
        assert _parse_tool_name_from_call_id("skill_turbo-tc-x-1") is None

    def test_non_string_returns_none(self):
        assert _parse_tool_name_from_call_id(None) is None  # type: ignore[arg-type]


class TestConsumePendingResumeInputFallback:
    def test_exact_match_returns_user_input_and_keeps_id(self):
        ex = _make_executor()
        ex.set_pending_resume(
            expected_tool_call_id="skill_turbo-tc-ask_user-605c02b1-0",
            user_input=[{"question": "q", "selected_options": ["A"]}],
        )
        user_input, effective_id = ex._consume_pending_resume_input(
            "skill_turbo-tc-ask_user-605c02b1-0", "ask_user"
        )
        assert user_input == [{"question": "q", "selected_options": ["A"]}]
        assert effective_id == "skill_turbo-tc-ask_user-605c02b1-0"
        assert ex._pending_resume is None

    def test_no_pending_returns_none_and_keeps_id(self):
        ex = _make_executor()
        user_input, effective_id = ex._consume_pending_resume_input(
            "skill_turbo-tc-ask_user-deadbeef-0", "ask_user"
        )
        assert user_input is None
        assert effective_id == "skill_turbo-tc-ask_user-deadbeef-0"

    def test_tool_name_mismatch_keeps_pending(self):
        """tool_name 不同时不消费 pending（防止误注入到无关工具调用）。"""
        ex = _make_executor()
        ex.set_pending_resume(
            expected_tool_call_id="skill_turbo-tc-ask_user-605c02b1-0",
            user_input=[{"question": "q", "selected_options": ["A"]}],
        )
        user_input, effective_id = ex._consume_pending_resume_input(
            "skill_turbo-tc-write_file-aaaaaaaa-0", "write_file"
        )
        assert user_input is None
        assert effective_id == "skill_turbo-tc-write_file-aaaaaaaa-0"
        # pending 保留，等真正的 ask_user 调用到来
        assert ex._pending_resume is not None

    def test_tcid_mismatch_but_tool_name_match_aligns_to_expected(self):
        """核心修复：tcid 因非确定性 args 变化，但 tool_name 匹配时，
        用 expected_tool_call_id 对齐并注入 user_input，打破死循环。"""
        ex = _make_executor()
        expected_tcid = "skill_turbo-tc-ask_user-605c02b1-0"
        replay_tcid = "skill_turbo-tc-ask_user-ac68cd05-0"
        ex.set_pending_resume(
            expected_tool_call_id=expected_tcid,
            user_input=[{"question": "q", "selected_options": ["主题A"]}],
        )
        user_input, effective_id = ex._consume_pending_resume_input(
            replay_tcid, "ask_user"
        )
        assert user_input == [{"question": "q", "selected_options": ["主题A"]}]
        # 关键断言：effective_id 对齐到中断时的 expected_tcid，使 rail 能
        # 按 RESUME_USER_INPUT_KEY 正确取出 user_input。
        assert effective_id == expected_tcid
        assert ex._pending_resume is None

    def test_pending_consumed_once(self):
        """回退命中后 pending 清空，后续同名调用拿不到 user_input。"""
        ex = _make_executor()
        ex.set_pending_resume(
            expected_tool_call_id="skill_turbo-tc-ask_user-605c02b1-0",
            user_input=[{"question": "q", "selected_options": ["A"]}],
        )
        _, _ = ex._consume_pending_resume_input(
            "skill_turbo-tc-ask_user-ac68cd05-0", "ask_user"
        )
        # 第二次调用：pending 已清空
        user_input2, effective_id2 = ex._consume_pending_resume_input(
            "skill_turbo-tc-ask_user-11111111-0", "ask_user"
        )
        assert user_input2 is None
        assert effective_id2 == "skill_turbo-tc-ask_user-11111111-0"

    def test_expected_tool_name_parsed_from_id(self):
        """set_pending_resume 解析 expected_tool_name，供回退匹配使用。"""
        ex = _make_executor()
        ex.set_pending_resume(
            expected_tool_call_id="skill_turbo-tc-ask_user-605c02b1-0",
            user_input="payload",
        )
        assert ex._pending_resume is not None
        assert ex._pending_resume["expected_tool_name"] == "ask_user"

    def test_none_user_input_does_not_trigger_fallback(self):
        """user_input 为 None 的 pending 不走回退（防止把空回复注入）。"""
        ex = _make_executor()
        ex.set_pending_resume(
            expected_tool_call_id="skill_turbo-tc-ask_user-605c02b1-0",
            user_input=None,
        )
        user_input, effective_id = ex._consume_pending_resume_input(
            "skill_turbo-tc-ask_user-ac68cd05-0", "ask_user"
        )
        assert user_input is None
        assert effective_id == "skill_turbo-tc-ask_user-ac68cd05-0"
