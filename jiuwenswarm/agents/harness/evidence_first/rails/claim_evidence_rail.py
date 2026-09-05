# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""ClaimEvidenceRail — 声明-证据绑定护栏。

框架贡献：在 agent 运行期间收集工具输出，为最终生成的每条「声明」建立
证据绑定（声明 → 任务 → 最近一次成功工具输出 → 配置 → 种子），并在
post_run 产出 ClaimBinding 列表，供论文写作引用。

- after_tool_call：把工具输出追加到当前 run 的 evidence buffer。
- after_invoke：在 run 结束时若 ctx 暴露最终答案与任务元数据，生成绑定记录；
  绑定不闭合的声明会记录为 unverifiable，供论文如实报告声明可追溯率。
"""

from __future__ import annotations

import logging

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenswarm.agents.harness.evidence_first.claim_evidence import (
    ClaimBinding, bind_claim, evidence_binding_ok,
)

logger = logging.getLogger(__name__)


class ClaimEvidenceRail(DeepAgentRail):
    """为每条声明绑定可重放证据。"""

    priority: int = 90

    def __init__(self, *, task_id: str = "", config: str = "", seed: int = 0) -> None:
        super().__init__()
        self.task_id = task_id
        self.config = config
        self.seed = seed
        self._tool_outputs: list[dict[str, str]] = []
        self.bindings: list[ClaimBinding] = []
        self.unverifiable: list[str] = []

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        output = self._extract_output(ctx)
        tool = self._extract_tool(ctx)
        if output:
            self._tool_outputs.append({"tool": tool or "", "output": output})

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        final = self._extract_final(ctx)
        claims = self._extract_claims(ctx, final)
        for claim in claims:
            binding = bind_claim(
                claim, self._tool_outputs,
                task_id=self.task_id, config=self.config, seed=self.seed,
            )
            if evidence_binding_ok(binding):
                self.bindings.append(binding)
            else:
                self.unverifiable.append(claim)
                logger.info("[ClaimEvidenceRail] 声明无可追溯证据: %s", claim[:80])
        if final:
            logger.info("[ClaimEvidenceRail] 生成 %d 条声明绑定（不可追溯 %d 条）",
                        len(self.bindings), len(self.unverifiable))

    # -- 防御性访问 -----------------------------------------------------------

    def _extract_output(self, ctx: AgentCallbackContext) -> str:
        for candidate in ("tool_result", "result", "content", "output"):
            value = getattr(ctx, candidate, None)
            if value is None:
                continue
            text = value if isinstance(value, str) else self._msg_content(value)
            if text and text.strip():
                return text.strip()
        return ""

    def _extract_tool(self, ctx: AgentCallbackContext) -> str:
        for candidate in ("tool_name", "tool", "function_name"):
            value = getattr(ctx, candidate, None)
            if isinstance(value, str) and value:
                return value
        return ""

    def _extract_final(self, ctx: AgentCallbackContext) -> str:
        for candidate in ("final_answer", "answer", "final_text"):
            value = getattr(ctx, candidate, None)
            if value is None:
                continue
            text = value if isinstance(value, str) else self._msg_content(value)
            if text and text.strip():
                return text.strip()
        return ""

    def _extract_claims(self, ctx: AgentCallbackContext, final: str) -> list[str]:
        claims = getattr(ctx, "claims", None) or getattr(ctx, "declarations", None)
        if isinstance(claims, list):
            out = []
            for c in claims:
                if isinstance(c, str):
                    out.append(c)
                elif isinstance(c, dict):
                    out.append(str(c.get("text") or c.get("claim") or ""))
            if out:
                return out
        return [final] if final else []

    @staticmethod
    def _msg_content(value) -> str:
        if isinstance(value, dict):
            return str(value.get("content") or value.get("text") or "")
        if hasattr(value, "content"):
            return str(getattr(value, "content") or "")
        return ""
