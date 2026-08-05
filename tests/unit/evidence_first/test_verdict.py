# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""ExecutionVerdict 分类器离线测试。"""

from __future__ import annotations

from jiuwenswarm.agents.harness.evidence_first.verdict import (
    ExecutionVerdict,
    classify,
    verdict_is_terminal,
)


def test_zero_division_recognized_case_insensitive():
    # Phase 2 发现的伪差分根因：大小写敏感漏判 ZeroDivisionError。
    assert classify("ZeroDivisionError: division by zero") == ExecutionVerdict.EXECUTION_FAILURE
    assert classify("ZeroDivisionError: division by zero").lower() == "execution_failure"


def test_chinese_failure_phrasings():
    for text in ("执行失败", "程序报错退出", "无法完成", "抛出了异常", "除零错误"):
        assert classify(text) == ExecutionVerdict.EXECUTION_FAILURE, text


def test_english_failure_phrasings():
    for text in ("Error: file not found", "failed to run", "cannot import", "Traceback ..."):
        assert classify(text) == ExecutionVerdict.EXECUTION_FAILURE, text


def test_negative_result_phrasings():
    for text in ("结果不达标", "指标未达标", "低于阈值", "no improvement", "negative"):
        assert classify(text) == ExecutionVerdict.RESULT_NEGATIVE, text


def test_success_numeric():
    assert classify("144") == ExecutionVerdict.SUCCESS
    # 负结果由否定关键词或 gt 判定；纯负数无上下文时视为产出数字（SUCCESS）。
    assert classify("-5") == ExecutionVerdict.SUCCESS
    assert classify("-5", gt="negative") == ExecutionVerdict.RESULT_NEGATIVE


def test_gt_mismatch_is_negative():
    assert classify("144", gt=42) == ExecutionVerdict.RESULT_NEGATIVE
    assert classify("42", gt=42) == ExecutionVerdict.SUCCESS


def test_inconclusive():
    assert classify(None) == ExecutionVerdict.INCONCLUSIVE
    assert classify("") == ExecutionVerdict.INCONCLUSIVE


def test_terminal_semantics():
    assert not verdict_is_terminal(ExecutionVerdict.EXECUTION_FAILURE)
    assert verdict_is_terminal(ExecutionVerdict.SUCCESS)
    assert verdict_is_terminal(ExecutionVerdict.RESULT_NEGATIVE)
