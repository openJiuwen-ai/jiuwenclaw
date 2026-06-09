# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""CodeAgentModeRail — defer plan-mode exit until the user approves in chat."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.agent_mode_rail import AgentModeRail

from jiuwenswarm.agents.harness.code.rails.code_plan_approval_rail import (
    PENDING_EXIT_RESULT_PREFIX,
)

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent

_EMPTY_EXIT_MSG = {
    "cn": "规划模式已结束。你现在可以结束本轮。\n计划文件：{plan_path}",
    "en": "Plan mode ended. You can now exit the turn.\nPlan file: {plan_path}",
}

_SWITCH_MODE_EXIT_PLAN_MSG = {
    "cn": (
        "[AgentModeRail] plan 模式下不能用 switch_mode 退出。"
        "请先调用 exit_plan_mode 提交计划，再在对话中回复「按计划实现」等批准执行；"
        "或使用 /mode code.normal 切换模式。"
    ),
    "en": (
        "[AgentModeRail] switch_mode cannot exit plan mode. "
        "Call exit_plan_mode, then approve in chat (e.g. implement the plan), "
        "or use /mode code.normal."
    ),
}

_NON_GIT_WRITE_RE = re.compile(
    r"\b(mkdir|touch|mv|cp|chmod|chown|dd|tee|wget|curl\s+.*\s*-[a-zA-Z]*O)\b"
    r"|\brm\s+(-[a-zA-Z]*[rf]|/|[~.])"
    r"|\b(7z|tar|zip|unzip|gzip|gunzip)\s+"
    r"|>\s*\S"
    r"|>>"
)


def _plan_tool_language(language: str | None) -> str:
    return "cn" if language == "cn" else "en"


class CodeAgentModeRail(AgentModeRail):
    """AgentModeRail variant for jiuwenswarm code mode.

    ``exit_plan_mode`` presents the plan for review but does **not** restore
    normal mode until the user approves via chat (handled by the server-side
    pending-approval gate).
    """

    def init(self, agent: "DeepAgent") -> None:
        """Register tools and patch ``exit_plan_mode`` to defer mode restore."""
        super().init(agent)
        self._patch_exit_plan_mode_tool()

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Enforce plan-mode write blocks beyond the parent git-only guard."""
        agent = self._agent
        session = ctx.session
        plan_state = agent.load_state(session).plan_mode
        tool_name = ctx.inputs.tool_name

        if plan_state.mode == "plan" and tool_name == "switch_mode":
            target = self._parse_switch_mode_target(ctx)
            if target in {"normal", "auto"}:
                lang = "cn" if self._language_is_cn() else "en"
                self._reject_tool(ctx, _SWITCH_MODE_EXIT_PLAN_MSG[lang])
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
        """Skip default exit handling while the plan awaits user approval."""
        if (
            ctx.inputs.tool_name == "exit_plan_mode"
            and not ctx.extra.get("_skip_tool")
            and ctx.inputs.tool_result is not None
        ):
            return
        await super().after_tool_call(ctx)

    def _patch_exit_plan_mode_tool(self) -> None:
        """Read plan for review without calling ``restore_mode_after_plan_exit``."""
        for tool in self._tools:
            if getattr(tool.card, "name", "") != "exit_plan_mode":
                continue

            language = _plan_tool_language(getattr(tool, "_language", "cn"))

            async def patched_invoke(inputs, _tool=tool, _lang=language, **kwargs):
                agent = _tool._agent_ref  # pylint: disable=protected-access
                session = kwargs.get("session")
                plan_path = agent.get_plan_file_path(session)
                plan_text = ""
                if plan_path and plan_path.exists():
                    plan_text = plan_path.read_text(encoding="utf-8")
                plan_path_str = str(plan_path) if plan_path else ""
                if not plan_text.strip():
                    return _EMPTY_EXIT_MSG[_lang].format(plan_path=plan_path_str)
                prefix = PENDING_EXIT_RESULT_PREFIX[_lang].format(
                    plan_path=plan_path_str,
                )
                return prefix + plan_text

            tool.invoke = patched_invoke
            break

    @staticmethod
    def _parse_switch_mode_target(ctx: AgentCallbackContext) -> str:
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
