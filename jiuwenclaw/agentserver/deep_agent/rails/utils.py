# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs


def is_ask_user_question_interrupt(ctx: AgentCallbackContext) -> bool:
    """检测中断是否为工具权限确认弹窗(ask_user_question/popup)。"""
    inputs = ctx.inputs
    if not isinstance(inputs, InvokeInputs) or not isinstance(inputs.query, InteractiveInput):
        return False
    result = getattr(inputs, "result", None)
    if isinstance(result, dict) and result.get("result_type") == "interrupt":
        # 工具权限中断有 interrupt_ids，区别于其他类型中断
        return "interrupt_ids" in result
    return False
