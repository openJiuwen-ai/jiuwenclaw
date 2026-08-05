# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""ExecutionVerdictRail — 执行判定护栏。

框架贡献：在 agent 的工具调用结果落进上下文之前，先把执行结果分类为
SUCCESS / RESULT_NEGATIVE / EXECUTION_FAILURE / INCONCLUSIVE，并把判定
写回 tool result 的 `evidence_first_verdict` 字段。

目的：让后续提示词与下游消费方都能看到「这是失败的运行」，从而阻止
「代码跑崩被写成科研结论」。判定规则见 evidence_first.verdict（大小写不敏感，
显式识别 ZeroDivisionError / division by zero 等诚实报错措辞）。

钩子：
- before_model_call：注入一段执行判定语义提示（仅当本 rail 启用时）。
- after_tool_call：读取工具结果文本 → classify() → 把判定写入结果消息。
"""

from __future__ import annotations

import logging

from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenswarm.agents.harness.evidence_first.verdict import ExecutionVerdict, classify

logger = logging.getLogger(__name__)

VERDICT_PROMPT = """[Execution Verdict]
每条工具结果先按如下语义判定，再写结论：
- 若工具报错/抛异常/超时 → 执行失败，禁止把该结果当作科研结论；
- 若执行成功但结果是否定/不达标 → 如实报告负面结果；
- 不得编造未出现的数值。"""

# 常见 tool result 文本字段候选，防御性读取不同版本的 ctx 结构。
_RESULT_FIELD_CANDIDATES = ("content", "result", "text", "output", "tool_result")


class ExecutionVerdictRail(DeepAgentRail):
    """给每条工具执行结果打上科研语义判定标签。"""

    priority: int = 120

    def __init__(self, *, prompt_section: bool = True) -> None:
        super().__init__()
        self._prompt_section = prompt_section
        self._last_verdicts: dict[str, ExecutionVerdict] = {}

    def _builder(self, ctx: AgentCallbackContext):
        return getattr(
            getattr(self, "_deep_agent", None) or ctx.agent,
            "system_prompt_builder",
            None,
        )

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        if not self._prompt_section:
            return
        builder = self._builder(ctx)
        if builder is None:
            return
        builder.add_section(PromptSection(
            name="execution_verdict",
            content={getattr(builder, "language", "cn") or "cn": VERDICT_PROMPT},
            priority=self.priority,
        ))

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        result = self._extract_result_text(ctx)
        if not result:
            return
        verdict = classify(result)
        if verdict == ExecutionVerdict.EXECUTION_FAILURE:
            logger.info("[ExecutionVerdictRail] 检测到执行失败，禁止当结论: %r", result[:120])
        self._annotate(ctx, verdict)

    # -- 防御性访问 -----------------------------------------------------------

    def _extract_result_text(self, ctx: AgentCallbackContext) -> str:
        """尽量从 ctx 中取工具结果文本；取不到返回空串。"""
        for candidate in _RESULT_FIELD_CANDIDATES:
            value = getattr(ctx, candidate, None)
            if value is None:
                continue
            text = value if isinstance(value, str) else self._message_content(value)
            if text and text.strip():
                return text
        # 兜底：ToolMessage 列表
        messages = getattr(ctx, "tool_messages", None) or getattr(ctx, "messages", None)
        if isinstance(messages, (list, tuple)) and messages:
            last = messages[-1]
            text = self._message_content(last)
            if text:
                return text
        return ""

    @staticmethod
    def _message_content(value) -> str:
        if isinstance(value, ToolMessage):
            return str(getattr(value, "content", "") or "")
        if isinstance(value, dict):
            return str(value.get("content") or value.get("text") or "")
        return ""

    def _annotate(self, ctx: AgentCallbackContext, verdict: ExecutionVerdict) -> None:
        """把判定写回结果消息/上下文，供下游消费。"""
        result = getattr(ctx, "tool_result", None)
        if result is not None:
            try:
                setattr(result, "evidence_first_verdict", verdict.value)
            except Exception as exc:  # noqa: BLE001
                logger.debug("[ExecutionVerdictRail] 标注失败: %s", exc)
        key = f"tool_{len(self._last_verdicts)}"
        self._last_verdicts[key] = verdict
