# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""CodeAgentModeRail — plan-mode write enforcement for code mode.

Plan approval is handled by ``PlanApprovalInterruptRail`` with an
immediate dialog (aligned with Claude Code).  This rail handles:
- Blocking ``switch_mode`` from exiting plan mode
- Blocking non-git write operations via bash in plan mode
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.agent_mode_rail import AgentModeRail

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent

_NON_GIT_WRITE_RE = re.compile(
    r"\b(mkdir|touch|mv|cp|chmod|chown|dd|tee|wget|curl\s+.*\s*-[a-zA-Z]*O)\b"
    r"|\brm\s+(-[a-zA-Z]*[rf]|/|[~.])"
    r"|\b(7z|tar|zip|unzip|gzip|gunzip)\s+"
    r"|>\s*\S"
    r"|>>"
)


class CodeAgentModeRail(AgentModeRail):
    """AgentModeRail variant for jiuwenswarm code mode.

    Plan approval is handled by ``PlanApprovalInterruptRail`` which intercepts
    ``exit_plan_mode`` with an immediate approval dialog (aligned with Claude Code).
    Mode restoration happens inside ``ExitPlanModeTool.invoke()`` on approval.
    """

    def init(self, agent: "DeepAgent") -> None:
        """Register tools. No exit_plan_mode patching needed —
        ``PlanApprovalInterruptRail`` handles the approval gate.
        """
        super().init(agent)

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Enforce plan-mode write blocks beyond the parent git-only guard."""
        agent = self._agent
        session = ctx.session
        plan_state = agent.load_state(session).plan_mode
        tool_name = ctx.inputs.tool_name

        if plan_state.mode == "plan" and tool_name == "switch_mode":
            target = self._parse_switch_mode_target(ctx)
            if target in {"normal", "auto"}:
                if self._language_is_cn():
                    msg = (
                        "[AgentModeRail] plan 模式下不能用 switch_mode 退出。"
                        "请先调用 exit_plan_mode 提交计划等待审批。"
                    )
                else:
                    msg = (
                        "[AgentModeRail] switch_mode cannot exit plan mode. "
                        "Call exit_plan_mode to submit your plan for approval."
                    )
                self._reject_tool(ctx, msg)
                return

        await super().before_tool_call(ctx)
        if ctx.extra.get("_skip_tool"):
            return

        if plan_state.mode != "plan":
            return
        if tool_name == "bash":
            command = self._extract_bash_command(ctx)
            if _NON_GIT_WRITE_RE.search(command):
                if self._language_is_cn():
                    msg = f"[AgentModeRail] plan 模式下禁止写操作（{command!r}）。"
                else:
                    msg = (
                        f"[AgentModeRail] Write operations are blocked in plan mode "
                        f"({command!r})."
                    )
                self._reject_tool(ctx, msg)

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Override parent to fix mode restoration on user rejection.

        The parent ``AgentModeRail.after_tool_call()`` has a supplement
        mode-restoration block that calls ``restore_mode_after_plan_exit()``
        when ``tool_result is not None``.  However, when
        ``PlanApprovalInterruptRail`` rejects the call (user clicks Reject),
        ``_skip_tool()`` sets ``tool_result`` to the feedback string — so the
        ``is not None`` check passes and the mode is erroneously restored.

        **Important**: we check ``_plan_rejected`` instead of ``_skip_tool``
        because ``ability_manager._railed_execute_single_tool_call`` **pops**
        ``_skip_tool`` from ``ctx.extra`` before ``after_tool_call`` runs.
        ``PlanApprovalInterruptRail`` sets ``_plan_rejected`` which persists
        through the pop.
        """
        tool_name = ctx.inputs.tool_name
        agent = self._agent
        rejected = ctx.extra.get("_plan_rejected", False)

        # Segment 1: register / unregister task_tool (same as parent)
        if tool_name == "enter_plan_mode" and not rejected:
            self._register_task_tool(agent)
        elif tool_name == "exit_plan_mode" and not rejected:
            self._unregister_task_tool(agent)

        # Segment 2: supplement mode restoration (PARENT BUG FIXED)
        # Only restore when the tool was NOT rejected — i.e. it actually
        # executed but the plan was empty (ExitPlanModeTool.invoke() returns
        # early without calling restore_mode_after_plan_exit).
        if tool_name == "exit_plan_mode" and not rejected:
            session = ctx.session
            state = agent.load_state(session)
            if (state.plan_mode.mode == "plan"
                    and ctx.inputs.tool_result is not None):
                try:
                    agent.restore_mode_after_plan_exit(session)
                    logger.info(
                        "[CodeAgentModeRail] Restored mode after plan exit "
                        "(plan was empty, tool did not restore)"
                    )
                except Exception as exc:
                    logger.warning(
                        "[CodeAgentModeRail] Failed to restore mode: %s", exc
                    )

    @staticmethod
    def _parse_switch_mode_target(ctx: AgentCallbackContext) -> str:
        """Parse the target mode from a switch_mode tool-call context."""
        raw: Any = None
        tool_call = getattr(ctx.inputs, "tool_call", None)
        if tool_call is not None:
            raw = getattr(tool_call, "arguments", None)
        if raw is None:
            raw = getattr(ctx.inputs, "tool_args", None)
        args: dict[str, Any] = {}
        if isinstance(raw, dict):
            args = raw
        elif isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    args = parsed
            except (TypeError, ValueError):
                pass
        return str(args.get("mode") or args.get("target_mode") or "").strip()
