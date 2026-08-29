# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Resume replay: 当工具因非确定性参数导致 tool_call_id 的 args_hash 变化时，
仍能按 (tool_name, global_seq) 对齐注入 user_input，避免循环中断。

tool_call_id 末段 idx 为本次 plan 执行内的全局工具调用序（跨 tool_name 递增），
因此同一执行内多次 ask_user（不同 questions）不会共享 idx=0，resume 不会误注入。

回退匹配：tool_name 一致且 global seq 一致时，用 expected_tool_call_id 对齐。
"""

from __future__ import annotations

from unittest.mock import MagicMock

from jiuwenswarm.server.runtime.skill_turbo.executor import (
    SkillTurboExecutor,
    _parse_args_hash_from_call_id,
    _parse_call_idx_from_call_id,
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


class TestParseCallIdxFromCallId:
    def test_parses_idx_zero(self):
        assert _parse_call_idx_from_call_id("skill_turbo-tc-ask_user-605c02b1-0") == 0

    def test_parses_idx_nonzero(self):
        assert _parse_call_idx_from_call_id("skill_turbo-tc-ask_user-bbbbbbbb-2") == 2

    def test_returns_none_for_non_numeric_idx(self):
        assert _parse_call_idx_from_call_id("skill_turbo-tc-ask_user-hash-x") is None

    def test_returns_none_for_missing_dash(self):
        assert _parse_call_idx_from_call_id("no_dash_here") is None
        assert _parse_call_idx_from_call_id("trailing-") is None

    def test_returns_none_for_non_string(self):
        assert _parse_call_idx_from_call_id(None) is None  # type: ignore[arg-type]


class TestConsumePendingResumeIdxComparison:
    """回退匹配比较 tool_call_id 末段 global seq，避免误注入到其他次序的同名调用。"""

    def test_idx_match_aligns_to_expected(self):
        """同名 + idx 一致（中断在第 2 次同参调用）：回退命中。"""
        ex = _make_executor()
        expected_tcid = "skill_turbo-tc-ask_user-bbbbbbbb-1"
        replay_tcid = "skill_turbo-tc-ask_user-cccccccc-1"  # args 变了，idx 仍是 1
        ex.set_pending_resume(
            expected_tool_call_id=expected_tcid,
            user_input=[{"question": "q", "selected_options": ["B"]}],
        )
        user_input, effective_id = ex._consume_pending_resume_input(
            replay_tcid, "ask_user"
        )
        assert user_input == [{"question": "q", "selected_options": ["B"]}]
        assert effective_id == expected_tcid
        assert ex._pending_resume is None

    def test_idx_mismatch_keeps_pending(self):
        """同名 + idx 不一致：不消费 pending，等 idx 匹配的调用到来。"""
        ex = _make_executor()
        expected_tcid = "skill_turbo-tc-ask_user-bbbbbbbb-1"
        replay_tcid = "skill_turbo-tc-ask_user-aaaaaaaa-0"  # 第 0 次同名调用
        ex.set_pending_resume(
            expected_tool_call_id=expected_tcid,
            user_input=[{"question": "q", "selected_options": ["B"]}],
        )
        user_input, effective_id = ex._consume_pending_resume_input(
            replay_tcid, "ask_user"
        )
        assert user_input is None
        assert effective_id == replay_tcid
        # pending 保留给第 1 次调用
        assert ex._pending_resume is not None

    def test_idx_match_then_mismatch_full_flow(self):
        """完整流程：第 0 次不命中保留 pending，第 1 次命中消费。"""
        ex = _make_executor()
        expected_tcid = "skill_turbo-tc-ask_user-bbbbbbbb-1"
        ex.set_pending_resume(
            expected_tool_call_id=expected_tcid,
            user_input="answer_for_second",
        )
        # 第 0 次同名调用（idx=0 != 1）
        r0 = ex._consume_pending_resume_input(
            "skill_turbo-tc-ask_user-aaaaaaaa-0", "ask_user"
        )
        assert r0 == (None, "skill_turbo-tc-ask_user-aaaaaaaa-0")
        # 第 1 次同名调用（idx=1 == 1）
        r1 = ex._consume_pending_resume_input(
            "skill_turbo-tc-ask_user-cccccccc-1", "ask_user"
        )
        assert r1 == ("answer_for_second", expected_tcid)


class TestGlobalToolCallSequence:
    def test_sequential_ask_user_gets_distinct_global_idx(self):
        ex = _make_executor()
        first_questions = {"questions": [{"header": "A", "question": "q1"}]}
        second_questions = {"questions": [{"header": "B", "question": "q2"}]}
        first_id = ex._next_tool_call_id("ask_user", first_questions)
        second_id = ex._next_tool_call_id("ask_user", second_questions)
        assert _parse_call_idx_from_call_id(first_id) == 0
        assert _parse_call_idx_from_call_id(second_id) == 1
        assert _parse_args_hash_from_call_id(first_id) != _parse_args_hash_from_call_id(second_id)

    def test_later_ask_user_pending_not_consumed_by_earlier_ask_on_replay(self):
        ex = _make_executor()
        first_tcid = ex._next_tool_call_id(
            "ask_user",
            {"questions": [{"header": "A", "question": "q"}]},
        )
        second_tcid = ex._next_tool_call_id(
            "ask_user",
            {"questions": [{"header": "B", "question": "q"}]},
        )
        ex.set_pending_resume(
            expected_tool_call_id=second_tcid,
            user_input=[{"question": "q", "selected_options": ["option-B"]}],
        )
        user_input, effective_id = ex._consume_pending_resume_input(first_tcid, "ask_user")
        assert user_input is None
        assert effective_id == first_tcid
        assert ex._pending_resume is not None
        user_input2, effective_id2 = ex._consume_pending_resume_input(second_tcid, "ask_user")
        assert user_input2 == [{"question": "q", "selected_options": ["option-B"]}]
        assert effective_id2 == second_tcid
        assert ex._pending_resume is None

    def test_other_tools_advance_global_seq(self):
        ex = _make_executor()
        bash_id = ex._next_tool_call_id("bash", {"command": "echo hi"})
        ask_id = ex._next_tool_call_id("ask_user", {"questions": [{"header": "A"}]})
        assert _parse_call_idx_from_call_id(bash_id) == 0
        assert _parse_call_idx_from_call_id(ask_id) == 1

    def test_resume_seeds_global_seq_from_pending_idx(self):
        """跳过 completed stage 后首个 use_tool 应与中断时同一 global idx。"""
        ex = _make_executor()
        expected_tcid = "skill_turbo-tc-ask_user-18cefd55-3"
        ex.set_pending_resume(
            expected_tool_call_id=expected_tcid,
            user_input=[{"question": "风格", "selected_options": ["典雅叙事"]}],
        )
        ex._setup_execution_context("plan", {}, 0.0)
        replay_tcid = ex._next_tool_call_id(
            "ask_user",
            {"questions": [{"header": "风格", "question": "请选择演示文稿的视觉风格"}]},
        )
        assert _parse_call_idx_from_call_id(replay_tcid) == 3
        user_input, effective_id = ex._consume_pending_resume_input(
            replay_tcid, "ask_user"
        )
        assert user_input == [{"question": "风格", "selected_options": ["典雅叙事"]}]
        assert effective_id == expected_tcid
        assert ex._pending_resume is None
