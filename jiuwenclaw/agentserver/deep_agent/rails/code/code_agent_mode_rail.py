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
    r"\b(mkdir|touch|mv|cp|chmod|chown|dd|tee|wget|install|truncate|"
    r"curl\s+.*\s*-[a-zA-Z]*O)\b"
    r"|\brm\s+(-[a-zA-Z]*[rf]|/|[~.])"
    r"|\b(7z|tar|zip|unzip|gzip|gunzip)\s+"
    r"|\bsed\s+[^;&|]*(?<!\S)(?:-[^\s;&|]*i|--in-place)(?:[^\w-]|$)"
    r"|\bpython(?:\d+(?:\.\d+)*)?\s+[^;&|]*(?<!\S)-c(?:\s|$)"
    r"|\b(?:perl|ruby)\s+[^;&|]*(?<!\S)-e(?:\s|$)"
    r"|\bfind\s+[^;&|]*(?<!\S)-delete(?:\s|$)"
    r"|>\s*\S"
    r"|>>"
)


class CodeAgentModeRail(AgentModeRail):
    """AgentModeRail variant for jiuwenswarm code mode.

    Plan approval is handled by ``PlanApprovalInterruptRail`` which intercepts
    ``exit_plan_mode`` with an immediate approval dialog (aligned with Claude Code).
    Mode restoration happens inside ``ExitPlanModeTool.invoke()`` on approval.
    """

    def __init__(self, allowed_tools: list[str] | None = None) -> None:
        """Initialize request-level Code submode synchronization state."""
        super().__init__(allowed_tools=allowed_tools)
        self._requested_modes: dict[str, tuple[int, str]] = {}
        self._applied_mode_generations: dict[str, int] = {}
        self._mode_generation = 0

    def init(self, agent: DeepAgent) -> None:
        """Register tools. No exit_plan_mode patching needed —
        ``PlanApprovalInterruptRail`` handles the approval gate.
        """
        super().init(agent)

    def set_requested_mode(
        self,
        mode: str,
        *,
        session_id: str | None = None,
    ) -> None:
        """Queue a Code submode for the next model turn.

        The adapter's ``code.plan``/``code.normal`` mode is distinct from the
        harness session's ``PlanModeState``. Apply it once per request so a
        later plan approval or ``switch_mode`` call is not overwritten on
        every model iteration.
        """
        normalized = str(mode or "").strip().lower()
        if normalized not in {"code", "code.plan", "code.normal"}:
            return
        target = "plan" if normalized == "code.plan" else "auto"
        key = self._session_key_from_value(session_id)
        self._mode_generation += 1
        self._requested_modes[key] = (self._mode_generation, target)

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Synchronize the requested Code submode before parent enforcement."""
        self._apply_requested_mode(ctx)
        await super().before_model_call(ctx)

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
        """Delegate lifecycle cleanup and restore mode only after approval."""
        tool_name = ctx.inputs.tool_name
        agent = self._agent
        if tool_name == "exit_plan_mode" and not ctx.extra.get("_plan_approved"):
            return

        await super().after_tool_call(ctx)
        if tool_name == "exit_plan_mode" and ctx.extra.get("_plan_approved"):
            session = ctx.session
            state = agent.load_state(session)
            if state.plan_mode.mode == "plan" and ctx.inputs.tool_result is not None:
                try:
                    agent.restore_mode_after_plan_exit(session)
                    logger.info(
                        "[CodeAgentModeRail] Restored mode after plan exit "
                        "(plan was empty, tool did not restore)"
                    )
                except Exception as exc:  # noqa: BLE001 - mode restore must not abort
                    logger.warning(
                        "[CodeAgentModeRail] Failed to restore mode: %s", exc
                    )

    def _apply_requested_mode(self, ctx: AgentCallbackContext) -> None:
        """Apply a queued adapter mode at most once for the current session."""
        session = ctx.session
        key = self._session_key_from_value(session)
        pending = self._requested_modes.get(key)
        if pending is None:
            return
        generation, target = pending
        if self._applied_mode_generations.get(key) == generation:
            return
        self._agent.switch_mode(session, target)
        self._applied_mode_generations[key] = generation

    @staticmethod
    def _session_key_from_value(value: Any) -> str:
        """Return a stable session key for mode synchronization."""
        if value is None:
            return "default"
        getter = getattr(value, "get_session_id", None)
        if callable(getter):
            try:
                value = getter()
            except Exception:  # noqa: BLE001 - best-effort session identity
                value = None
        if value is None:
            value = getattr(value, "session_id", None)
        return str(value or "default")

    def _language_is_cn(self) -> bool:
        """Return whether the active agent prompt language is Chinese."""
        builder = getattr(self._agent, "system_prompt_builder", None)
        return getattr(builder, "language", "cn") != "en"

    @staticmethod
    def _extract_bash_command(ctx: AgentCallbackContext) -> str:
        """Extract a bash command without widening an unparseable input."""
        raw: Any = getattr(ctx.inputs, "tool_args", None)
        if isinstance(raw, dict):
            for key in ("command", "cmd", "script"):
                value = raw.get(key)
                if isinstance(value, str):
                    return value
            return ""
        if isinstance(raw, str):
            return raw

        tool_call = getattr(ctx.inputs, "tool_call", None)
        raw_arguments = getattr(tool_call, "arguments", None)
        if isinstance(raw_arguments, str):
            try:
                parsed = json.loads(raw_arguments)
            except (TypeError, ValueError):
                return raw_arguments
            if isinstance(parsed, dict):
                for key in ("command", "cmd", "script"):
                    value = parsed.get(key)
                    if isinstance(value, str):
                        return value
        return ""

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
