# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jiuwenclaw.agentserver.interface import JiuWenClaw


class AgentManager:
    def __init__(self):
        self.agents = {}

    async def initialize(self):
        # 需要提前初始化agent, 否则在get_agent获取的agent可能为空
        from jiuwenclaw.agentserver.interface import JiuWenClaw
        agent = JiuWenClaw()
        await agent.create_instance()
        self.agents["default_session"] = agent

    async def prepare_agent(self, session_id, *args):
        if self.agents.get("default_session") is None:
            from jiuwenclaw.agentserver.interface import JiuWenClaw
            agent = JiuWenClaw()
            await agent.create_instance()
            self.agents["default_session"] = agent

    def get_agent(self, session_id, *args) -> "JiuWenClaw":
        return self.agents["default_session"]

    def is_working(self) -> dict:
        """返回 Agent 是否正在工作的状态.

        Returns:
            dict: 工作状态信息，包含 working, initialized, active_tasks,
                  stream_tasks, pending_messages, active_sessions 字段.
                  如果 Agent 未初始化，返回 working=False.
        """
        agent = self.agents.get("default_session")
        if agent is None:
            return {
                "working": False,
                "initialized": False,
                "model_configured": False,
                "active_tasks": 0,
                "stream_tasks": 0,
                "pending_messages": 0,
                "active_sessions": [],
            }
        return agent.is_working()