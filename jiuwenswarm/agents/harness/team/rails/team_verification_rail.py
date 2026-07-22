# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verification-aware planning rail for team / swarm members.

Ports veriMAP's verification-aware planning into JiuwenSwarm's rail model:

* On the **Leader**, ``before_model_call`` injects planning guidance so the
  planner emits an ``Acceptance Criteria`` block for every subtask it delegates
  (the "verification-aware planning" step).
* On a gated **teammate**, ``after_invoke`` runs an inline verifier over the
  deliverable against the acceptance criteria carried in the subtask prompt,
  retrying via bounded model revision and escalating to the Leader on
  exhaustion.

The heavy lifting (verifiers, criteria extraction, the bounded loop) lives in
:mod:`jiuwenswarm.agents.harness.team.verification` so it stays unit-testable
without a live agent.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenswarm.agents.harness.team.verification import (
    ReviseFn,
    JudgeFn,
    build_verifier,
    extract_criteria,
    is_enabled,
    normalize_mode,
    run_verification_loop,
)

logger = logging.getLogger(__name__)

# Result dict keys that may carry the member's textual deliverable, in priority
# order. The first present non-empty string is the one we verify / rewrite.
_RESULT_TEXT_KEYS: tuple[str, ...] = (
    "output",
    "response",
    "content",
    "answer",
    "final_output",
    "result",
    "text",
)

_PLANNING_GUIDANCE = {
    "en": (
        "# Verification-Aware Planning\n\n"
        "When you decompose the goal and delegate a subtask to a teammate "
        "(build_team / spawn_teammate / a SwarmFlow agent node), you MUST append "
        "an explicit, checkable acceptance-criteria block to that subtask's "
        "instructions, introduced by a line reading exactly `Acceptance "
        "Criteria:`. List each criterion as a concrete, verifiable bullet "
        "(expected outputs, required sections, formats, constraints). Each "
        "teammate deliverable is verified against these criteria before it is "
        "accepted, so keep them specific and testable.\n"
    ),
    "cn": (
        "# 验证感知规划\n\n"
        "在分解目标并把子任务派发给成员时（build_team / spawn_teammate / "
        "SwarmFlow 智能体节点），你必须在该子任务的指令末尾追加一段明确、可核查的"
        "验收标准，并以单独一行 `验收标准：` 开头。将每条标准写成具体、可验证的条目"
        "（期望产出、必需章节、格式、约束）。每个成员的交付物在被采纳前都会依据这些"
        "标准进行验证，请保持其具体且可测试。\n"
    ),
}

_ESCALATION_NOTE = {
    "en": (
        "\n\n---\n[VERIFICATION FAILED after {attempts} revision attempt(s)] "
        "This deliverable does not yet satisfy the acceptance criteria: {reason} "
        "Leader review / human intervention is required before it is accepted.\n"
    ),
    "cn": (
        "\n\n---\n[验证未通过，已尝试修订 {attempts} 次] "
        "该交付物尚未满足验收标准：{reason} 需要 Leader 复核或人工介入后方可采纳。\n"
    ),
}


class TeamVerificationRail(DeepAgentRail):
    """Inline verification gate + verification-aware planning guidance.

    Args:
        mode: Verification mode (``none`` / ``generic`` / ``structured``).
        max_iterations: Max bounded revision attempts before escalating.
        output_enforcement: Require structured (JSON) output in ``structured``
            mode.
        apply_to_roles: Roles whose deliverables are gated in ``after_invoke``
            (the Leader always receives planning guidance regardless).
        role: This member's role, resolved from the build context.
        language: Prompt language (``cn`` / ``en``).
    """

    priority = 35
    SECTION_NAME = "verification_aware_planning"
    SECTION_PRIORITY = 41

    def __init__(
        self,
        *,
        mode: str = "none",
        max_iterations: int = 2,
        output_enforcement: bool = False,
        apply_to_roles: Optional[Sequence[str]] = None,
        role: str = "",
        language: str = "cn",
    ) -> None:
        super().__init__()
        self._mode = normalize_mode(mode)
        self._max_iterations = max(0, int(max_iterations))
        self._output_enforcement = bool(output_enforcement)
        self._apply_to_roles = tuple(apply_to_roles or ("teammate",))
        self._role = role or ""
        self._language = "cn" if str(language).lower() in {"cn", "zh"} else "en"
        self._agent: Any = None
        self.system_prompt_builder = None

    # -- lifecycle -----------------------------------------------------------

    def init(self, agent: Any) -> None:
        """Capture the agent and its prompt builder."""
        self._agent = agent
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

    def uninit(self, agent: Any) -> None:
        """Remove the injected planning section and drop the agent reference."""
        _ = agent
        if self.system_prompt_builder is not None:
            try:
                self.system_prompt_builder.remove_section(self.SECTION_NAME)
            except Exception:  # noqa: BLE001 - builder may not have the section
                logger.debug("TeamVerificationRail: failed to clear section")
        self.system_prompt_builder = None
        self._agent = None

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject verification-aware planning guidance on the Leader."""
        _ = ctx
        if self._role != "leader" or self.system_prompt_builder is None:
            return
        guidance = _PLANNING_GUIDANCE[self._language]
        self.system_prompt_builder.add_section(
            PromptSection(
                name=self.SECTION_NAME,
                content={self._language: guidance},
                priority=self.SECTION_PRIORITY,
            )
        )

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        """Verify a gated teammate deliverable against its acceptance criteria."""
        if self._role not in self._apply_to_roles:
            return
        inputs = ctx.inputs
        if not isinstance(inputs, InvokeInputs) or not isinstance(inputs.result, dict):
            return

        criteria = self._resolve_criteria(inputs)
        if not criteria:
            return  # Nothing to verify for this subtask.

        output = self._get_result_text(inputs.result)
        if not output:
            return

        verifier = build_verifier(
            self._mode,
            output_enforcement=self._output_enforcement,
            judge=self._build_judge(),
        )
        if verifier is None:
            return

        result = await run_verification_loop(
            output=output,
            criteria=criteria,
            verifier=verifier,
            revise=self._build_revise(),
            max_iterations=self._max_iterations,
        )

        final_output = result.output
        if result.escalated:
            note = _ESCALATION_NOTE[self._language].format(
                attempts=result.attempts,
                reason=result.outcome.reason or "criteria not met",
            )
            final_output = f"{final_output}{note}"

        self._set_result_text(inputs.result, final_output)
        report = result.outcome.to_dict()
        report["attempts"] = result.attempts
        report["escalated"] = result.escalated
        inputs.result["verification"] = report
        logger.info(
            "TeamVerificationRail: role=%s mode=%s passed=%s attempts=%s escalated=%s",
            self._role,
            self._mode,
            result.outcome.passed,
            result.attempts,
            result.escalated,
        )

    # -- helpers -------------------------------------------------------------

    def _resolve_criteria(self, inputs: InvokeInputs) -> Optional[str]:
        """Pull acceptance criteria out of the subtask prompt."""
        return extract_criteria(_query_text(inputs.query))

    @staticmethod
    def _get_result_text(result: dict[str, Any]) -> str:
        """Return the first non-empty string deliverable from *result*."""
        for key in _RESULT_TEXT_KEYS:
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return ""

    @staticmethod
    def _set_result_text(result: dict[str, Any], text: str) -> None:
        """Write *text* back to the same key :meth:`_get_result_text` reads."""
        for key in _RESULT_TEXT_KEYS:
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                result[key] = text
                return
        result["output"] = text

    def _build_judge(self) -> Optional[JudgeFn]:
        """Build an async LLM judge bound to the member's model (if available)."""
        llm = getattr(self._agent, "_llm", None)
        invoke = getattr(llm, "invoke", None)
        if invoke is None:
            return None

        async def judge(prompt: str) -> str:
            message = await invoke(messages=[{"role": "user", "content": prompt}])
            return _message_text(message)

        return judge

    def _build_revise(self) -> Optional[ReviseFn]:
        """Build an async reviser that asks the member's model for a fix."""
        llm = getattr(self._agent, "_llm", None)
        invoke = getattr(llm, "invoke", None)
        if invoke is None:
            return None

        async def revise(output: str, criteria: str, reason: str) -> Optional[str]:
            prompt = (
                "Your previous deliverable did not satisfy the acceptance "
                "criteria. Revise it so it fully complies. Return ONLY the "
                "corrected deliverable, with no commentary.\n\n"
                f"ACCEPTANCE CRITERIA:\n{criteria}\n\n"
                f"WHY IT FAILED:\n{reason}\n\n"
                f"PREVIOUS DELIVERABLE:\n{output}\n"
            )
            message = await invoke(messages=[{"role": "user", "content": prompt}])
            return _message_text(message) or None

        return revise


def _query_text(query: Any) -> str:
    """Best-effort extraction of the textual query from ``InvokeInputs.query``."""
    if query is None:
        return ""
    if isinstance(query, str):
        return query
    for attr in ("query", "content", "text"):
        value = getattr(query, attr, None)
        if isinstance(value, str) and value.strip():
            return value
    return str(query)


def _message_text(message: Any) -> str:
    """Extract text content from an ``AssistantMessage`` / str / dict response."""
    if message is None:
        return ""
    if isinstance(message, str):
        return message
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return "" if content is None else str(content)


__all__ = ["TeamVerificationRail"]
