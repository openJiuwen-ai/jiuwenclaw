# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""ExecutionVerdict — 执行判定分类器。

将一条工具执行结果判定为四种互斥的科研语义之一，防止"代码跑崩被写成科研结论"：

- SUCCESS            成功，产出数字结论
- RESULT_NEGATIVE    成功执行，但结果是否定/负面（不达标、负收益）
- EXECUTION_FAILURE  执行本身失败（异常/报错/超时），不得当作结论
- INCONCLUSIVE       无法判定（缺证据/无法解析）

判定规则是大小写不敏感的，并显式识别 "ZeroDivisionError"、"division by zero"
这类诚实报错措辞——这是 Phase 2 中发现的伪差分根因（见 docs/paper 中 5.2 节）。
本模块与 scripts/recompute_scoring.py 的离线重算规则保持一致。
"""

from __future__ import annotations

import re
from enum import Enum


class ExecutionVerdict(str, Enum):
    """科研语义执行判定。"""

    SUCCESS = "SUCCESS"
    RESULT_NEGATIVE = "RESULT_NEGATIVE"
    EXECUTION_FAILURE = "EXECUTION_FAILURE"
    INCONCLUSIVE = "INCONCLUSIVE"


# 执行失败关键词（小写匹配）。显式包含零除/异常措辞，避免大小写与拼写漏判。
FAILURE_KEYWORDS = (
    "失败", "报错", "无法", "异常", "错误", "不能",
    "error", "fail", "cannot", "unable", "exception",
    "zero division", "division by zero", "除零",
    "traceback", "timeout", "超时", "没有这个", "no such",
)

# 否定/负面结果关键词（小写匹配）。
NEGATIVE_KEYWORDS = (
    "不达标", "未达标", "未通过", "未达到", "低于", "小于",
    "negative", "no improvement", "无改善", "没有达标",
    "decrease", "下降", "降低",
)


def _last_number(text: str) -> float | None:
    """提取文本中最后一个数字（含负号）。"""
    m = re.findall(r"-?\d+(?:\.\d+)?", text)
    return float(m[-1]) if m else None


def classify(
    final_text: str | int | float | None,
    *,
    gt: str | int | float | None = None,
) -> ExecutionVerdict:
    """对最终文本给出执行判定。

    参数
    ----
    final_text : agent 的最终输出（数字、文本或报错信息）。
    gt         : 可选的真实标签。若提供，用于区分「成功但结果与预期不符」，
                 此时把标签直接映射为对应的否定语义。

    返回
    ----
    ExecutionVerdict 枚举值。规则优先级：EXECUTION_FAILURE > RESULT_NEGATIVE > SUCCESS。
    """
    if final_text is None:
        return ExecutionVerdict.INCONCLUSIVE

    text = str(final_text)
    low = text.lower()

    # 1) 执行失败最优先：任何失败措辞都禁止当作科研结论。
    if any(k in low for k in FAILURE_KEYWORDS):
        return ExecutionVerdict.EXECUTION_FAILURE

    # 2) 显式否定措辞 → 负面结果（如实报告）。
    if any(k in low for k in NEGATIVE_KEYWORDS):
        return ExecutionVerdict.RESULT_NEGATIVE

    # 3) 有真实标签时，数值语义判定（与 Phase 2 executor.rescore 对齐）。
    if gt is not None:
        gt_text = str(gt).strip().lower()
        last_num = _last_number(text)
        if gt_text == "negative":
            # 否定标签：显式关键词已在上一步覆盖，这里补「负数字」判定。
            if last_num is not None and last_num < 0:
                return ExecutionVerdict.RESULT_NEGATIVE
        elif last_num is not None and gt_text not in ("", "none"):
            try:
                if abs(last_num - float(gt)) > 1e-6:
                    return ExecutionVerdict.RESULT_NEGATIVE
            except (TypeError, ValueError):
                pass

    # 4) 有数字/结论文本 → 成功。
    if _last_number(text) is not None or len(text.strip()) > 2:
        return ExecutionVerdict.SUCCESS

    return ExecutionVerdict.INCONCLUSIVE


def verdict_is_terminal(verdict: ExecutionVerdict) -> bool:
    """判定是否可安全作为科研结论（失败永远不可）。"""
    return verdict != ExecutionVerdict.EXECUTION_FAILURE
