# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""AgentDropoutRail — rectify-or-reject teammate contributions before team share.

Maps AgentDropoutV2 "intercept before broadcast" onto DeepAgentRail hooks:
- ``before_tool_call`` audits ``team.send_message``-style share tools
- ``before_model_call`` injects pending rectify feedback into the prompt
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenswarm.agents.dropout import (
    AgentDropoutConfig,
    AgentDropoutService,
    ContributionAction,
)
from jiuwenswarm.agents.dropout.prompts import (
    DROP_SIGNAL_MESSAGE,
    DROP_SIGNAL_PREFIX,
    RECTIFY_TOOL_MESSAGE,
    REJECT_TOOL_MESSAGE,
)

logger = logging.getLogger(__name__)

# Team-sharing tools (openjiuwen team tools use id ``team.send_message``).
_TEAM_SHARE_TOOL_NAMES = frozenset({"send_message"})
_CONTENT_ARG_KEYS = ("content", "message", "text", "query", "body")


class AgentDropoutRail(DeepAgentRail):
    """Audit teammate share tools; rectify, reject, or signal member dropout."""

    priority = 85
    SECTION_NAME = "agent_dropout_feedback"
    SECTION_PRIORITY = 45

    def __init__(
        self,
        *,
        service: AgentDropoutService,
        member_name: str = "teammate",
        role: str = "teammate",
        active_members: int = 2,
        task_resolver: Callable[[AgentCallbackContext], str] | None = None,
    ) -> None:
        super().__init__()
        self.system_prompt_builder = None
        self._service = service
        self._member_name = member_name or "teammate"
        self._role = role or "teammate"
        self._active_members = max(1, int(active_members))
        self._task_resolver = task_resolver

    def init(self, agent) -> None:
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)
        card = getattr(agent, "card", None)
        if card is not None:
            name = getattr(card, "name", None) or getattr(card, "id", None)
            if name:
                self._member_name = str(name)

    def uninit(self, agent) -> None:
        _ = agent
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section(self.SECTION_NAME)
        self.system_prompt_builder = None

    def set_active_members(self, count: int) -> None:
        self._active_members = max(1, int(count))

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject pending rectify feedback into the system prompt, if any."""
        _ = ctx
        if self.system_prompt_builder is None:
            return
        feedback = self._service.get_pending_feedback(self._member_name)
        if not feedback:
            self.system_prompt_builder.remove_section(self.SECTION_NAME)
            return
        self.system_prompt_builder.add_section(
            PromptSection(
                name=self.SECTION_NAME,
                content={
                    "en": "# Agent Dropout Rectify Feedback\n\n" + feedback + "\n",
                    "cn": "# Agent Dropout 纠正反馈\n\n" + feedback + "\n",
                },
                priority=self.SECTION_PRIORITY,
            )
        )

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Intercept team share tools and run rectify-or-reject evaluation."""
        inputs = getattr(ctx, "inputs", None)
        if inputs is None:
            return
        tool_name = getattr(inputs, "tool_name", None) or ""
        tool_call = getattr(inputs, "tool_call", None)
        if not self._is_team_share_tool(tool_name, tool_call):
            return

        content = self._extract_share_content(tool_call)
        if not content.strip():
            return

        task = self._resolve_task(ctx)
        try:
            result = await self._service.evaluate_contribution(
                task=task,
                content=content,
                member_name=self._member_name,
                role=self._role,
                active_members=self._active_members,
            )
        except Exception as exc:  # noqa: BLE001 — never break team tool path
            logger.warning(
                "[AgentDropoutRail] evaluate_contribution failed member=%s: %s",
                self._member_name,
                exc,
            )
            return

        if result.action == ContributionAction.PASS:
            self._service.clear_pending_feedback(self._member_name)
            if self.system_prompt_builder is not None:
                self.system_prompt_builder.remove_section(self.SECTION_NAME)
            return

        if result.action == ContributionAction.RECTIFY:
            feedback = result.audit.feedback or "Please revise your contribution."
            message = RECTIFY_TOOL_MESSAGE.format(
                prefix=DROP_SIGNAL_PREFIX,
                attempt=result.rectify_attempt,
                max_attempts=self._service.config.max_rectify_attempts,
                feedback=feedback,
            )
            self._reject_tool(ctx, message)
            return

        details = result.audit.feedback or "Audit failed after rectify attempts."
        if result.action == ContributionAction.DROP and result.dropout is not None:
            message = DROP_SIGNAL_MESSAGE.format(
                prefix=DROP_SIGNAL_PREFIX,
                member_name=self._member_name,
                failure_count=result.dropout.failure_count,
                reason=result.dropout.reason,
            )
            message = f"{message}\n{details}"
            self._reject_tool(ctx, message)
            return

        message = REJECT_TOOL_MESSAGE.format(
            prefix=DROP_SIGNAL_PREFIX,
            details=details,
        )
        self._reject_tool(ctx, message)

    def _resolve_task(self, ctx: AgentCallbackContext) -> str:
        if self._task_resolver is not None:
            try:
                return self._task_resolver(ctx) or "team collaboration"
            except Exception:  # noqa: BLE001
                pass
        # Best-effort: conversation / query fields when present.
        inputs = getattr(ctx, "inputs", None)
        for attr in ("query", "task", "user_query", "conversation_id"):
            value = getattr(inputs, attr, None) if inputs is not None else None
            if isinstance(value, str) and value.strip():
                return value.strip()
        return "team collaboration"

    @staticmethod
    def _is_team_share_tool(tool_name: str, tool_call: Any) -> bool:
        tool_id = str(getattr(tool_call, "id", "") or "")
        if tool_id.startswith("team.") or "team.send_message" in tool_id:
            return True
        return tool_name in _TEAM_SHARE_TOOL_NAMES

    @staticmethod
    def _extract_share_content(tool_call: Any) -> str:
        args = getattr(tool_call, "arguments", None) if tool_call is not None else None
        if not isinstance(args, dict):
            return ""
        for key in _CONTENT_ARG_KEYS:
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return ""

    @staticmethod
    def _reject_tool(ctx: AgentCallbackContext, message: str) -> None:
        """Skip tool execution and return the rejection / feedback message."""
        tool_call = getattr(ctx.inputs, "tool_call", None)
        tool_call_id = tool_call.id if tool_call else ""
        ctx.extra["_skip_tool"] = True
        ctx.inputs.tool_result = message
        ctx.inputs.tool_msg = ToolMessage(content=message, tool_call_id=tool_call_id)


def build_agent_dropout_rail(
    *,
    config: AgentDropoutConfig | dict[str, Any] | None,
    member_name: str = "teammate",
    role: str = "teammate",
    active_members: int = 2,
    llm=None,
) -> AgentDropoutRail | None:
    """Factory used by the swarm provider; returns None when disabled."""
    cfg = (
        config
        if isinstance(config, AgentDropoutConfig)
        else AgentDropoutConfig.from_mapping(config if isinstance(config, dict) else None)
    )
    if not cfg.enabled:
        return None
    service = AgentDropoutService(config=cfg, llm=llm)
    return AgentDropoutRail(
        service=service,
        member_name=member_name,
        role=role,
        active_members=active_members,
    )


__all__ = ["AgentDropoutRail", "build_agent_dropout_rail"]
