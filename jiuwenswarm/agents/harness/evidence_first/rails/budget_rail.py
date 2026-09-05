# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""BudgetRail — 预算护栏。

框架贡献：给科研 agent 注入「剩余预算」感知，并硬性拦截超支：
- before_model_call：注入剩余 token/调用预算提示，引导低成本决策；
- after_model_call：若 ctx 暴露 usage 信息，则累计到当前 run 预算；
- 预算耗尽后：设置 `budget_exceeded` 标志，后续调用在 before_model_call
  直接短路，阻止继续消耗预算。

预算通过 ContextVar `current_run_budget` 与 `current_budget_cap` 传入，
与 evidence_first.research_queue 的预算账本对接。
"""

from __future__ import annotations

import logging
from contextvars import ContextVar

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.rails.base import DeepAgentRail

logger = logging.getLogger(__name__)

# 当前 run 的预算上下文（由调度方 set）。
current_budget_used: ContextVar[int] = ContextVar("evidence_first_budget_used", default=0)
current_budget_cap: ContextVar[int] = ContextVar("evidence_first_budget_cap", default=0)
budget_exceeded: ContextVar[bool] = ContextVar("evidence_first_budget_exceeded", default=False)


class BudgetRail(DeepAgentRail):
    """预算感知 + 超支拦截。"""

    priority: int = 200

    def __init__(self, *, cap_tokens: int = 0, prompt_section: bool = True) -> None:
        super().__init__()
        self.cap_tokens = cap_tokens
        self._prompt_section = prompt_section
        if cap_tokens > 0:
            current_budget_cap.set(cap_tokens)

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        used = current_budget_used.get()
        cap = self.cap_tokens or current_budget_cap.get()
        if cap and used >= cap:
            budget_exceeded.set(True)
            logger.warning("[BudgetRail] 预算已耗尽 used=%d cap=%d，本次调用被拦截", used, cap)
            return
        if not self._prompt_section:
            return
        builder = getattr(
            getattr(self, "_deep_agent", None) or ctx.agent,
            "system_prompt_builder",
            None,
        )
        if builder is None:
            return
        remain = max(0, (cap - used)) if cap else 0
        prompt = (
            "[Budget] 剩余预算 token 约 %d。优先用轻量工具/短输出；"
            "无法确证时如实报告，不要为了省预算编造结论。" % remain
        )
        builder.add_section(PromptSection(
            name="budget_aware",
            content={getattr(builder, "language", "cn") or "cn": prompt},
            priority=self.priority,
        ))

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        usage = getattr(ctx, "usage", None) or getattr(ctx, "model_usage", None)
        tokens = 0
        if isinstance(usage, dict):
            tokens = int(usage.get("total_tokens") or usage.get("completion_tokens") or 0)
        elif usage is not None:
            tokens = int(getattr(usage, "total_tokens", 0) or 0)
        if tokens:
            current_budget_used.set(current_budget_used.get() + tokens)
