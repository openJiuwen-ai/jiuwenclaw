# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""DispatchAgentRail — 在 agent 上注册 dispatch_agent 工具。

与 SubagentRail 并列：SubagentRail 注册按白名单 dispatch 的 ``task_tool``；
本 rail 注册允许 LLM 自由起草规格的 ``dispatch_agent``。两者互不依赖，
可同时挂载。
"""

from __future__ import annotations

from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenclaw.agentserver.deep_agent.tools.dispatch_agent_tool import (
    create_dispatch_agent_tool,
)


class DispatchAgentRail(DeepAgentRail):
    """Rail registering dispatch_agent tool for free-form subagent spawn."""

    priority = 96

    def __init__(self) -> None:
        super().__init__()
        self.tools = None
        self.system_prompt_builder = None

    def init(self, agent) -> None:
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)
        language = (
            getattr(self.system_prompt_builder, "language", None) or "cn"
        )
        agent_id = getattr(getattr(agent, "card", None), "id", None)

        tools = create_dispatch_agent_tool(
            parent_agent=agent, language=language, agent_id=agent_id,
        )
        self.tools = tools

        Runner.resource_mgr.add_tool(list(tools))
        for tool in tools:
            agent.ability_manager.add(tool.card)

        logger.info("[DispatchAgentRail] dispatch_agent tool registered")

    def uninit(self, agent) -> None:
        if not self.tools:
            return
        for tool in self.tools:
            name = getattr(tool.card, "name", None)
            if name and hasattr(agent, "ability_manager"):
                agent.ability_manager.remove(name)
            tool_id = getattr(tool.card, "id", None)
            if tool_id:
                Runner.resource_mgr.remove_tool(tool_id)
        logger.info("[DispatchAgentRail] dispatch_agent tool unregistered")
        self.tools = None

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        _ = ctx
        return


__all__ = ["DispatchAgentRail"]
