# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""DesignAgentModeRail — design profile 的 plan 模式约束。

派生自 :class:`CodeAgentModeRail`（与 :class:`WorkAgentModeRail` 同构），仅替换
构造参数：

- 工具白名单换成 design 场景的只读集合（无代码型子 agent，但允许 ``skill_tool``
  加载 ppt-creation 的 SKILL.md 做只读调研）。
- 提示词换成 design 专属版本，引用 PPT 设计工作流，不引用代码专用子 agent。

这样 design 与 code 共享同一套 plan 模式安全兜底，不会因为两份拷贝而出现行为漂移。
"""

from __future__ import annotations

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext

from jiuwenswarm.agents.harness.code.rails.code_agent_mode_rail import CodeAgentModeRail
from jiuwenswarm.agents.harness.design.prompt.design_plan_prompts import (
    DESIGN_PLAN_ALLOWED_TOOLS,
    design_enter_plan_instructions,
    design_exit_plan_notification,
    design_plan_mode_system_note,
)

# 非 plan 态额外隐藏的工具。这个 rail 现在是常挂的，父类 ``AgentModeRail.init``
# 会注册 switch_mode / enter_plan_mode / exit_plan_mode 三个工具。父类只在非 plan
# 态隐藏后两个，plan 态则按 ``DESIGN_PLAN_ALLOWED_TOOLS`` 白名单过滤。补上
# ``switch_mode`` 防止 design agent 自己切进 plan——design 的 plan 开关是用户在
# 界面上控制、由服务端写进会话状态的（与 work profile 行为一致）。
_HIDDEN_IN_NORMAL_EXTRA = frozenset({"switch_mode"})


class DesignAgentModeRail(CodeAgentModeRail):
    """design profile 的 plan 模式 rail。

    Args:
        language: 提示词语言（``cn`` / ``en``）。
        allowed_tools: 覆盖默认的 design plan 工具白名单（测试与定制用）。
        exit_plan_notification: 覆盖退出 plan 后追加的提示。
    """

    def __init__(
        self,
        *,
        language: str = "cn",
        allowed_tools: list[str] | None = None,
        exit_plan_notification: str | None = None,
    ) -> None:
        super().__init__(
            allowed_tools=list(allowed_tools or DESIGN_PLAN_ALLOWED_TOOLS),
            plan_mode_attachment_note=design_plan_mode_system_note(language),
            enter_plan_instructions=design_enter_plan_instructions(language),
            exit_plan_notification=(
                exit_plan_notification or design_exit_plan_notification(language)
            ),
        )

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """在父类基础上，非 plan 态再隐藏 ``switch_mode``。"""
        await super().before_model_call(ctx)
        if self._agent.load_state(ctx.session).plan_mode.mode == "plan":
            return
        if not isinstance(ctx.inputs.tools, list):
            return
        ctx.inputs.tools = [
            tool
            for tool in ctx.inputs.tools
            if getattr(tool, "name", "") not in _HIDDEN_IN_NORMAL_EXTRA
        ]


__all__ = ["DesignAgentModeRail"]
