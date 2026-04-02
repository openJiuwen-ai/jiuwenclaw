# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jiuwenclaw.agentserver.interface import JiuWenClaw


class AgentManager:
    def __init__(self):
        self.agents = {}

    async def initialize(self):
        return

    async def prepare_agent(self, session_id, *args):
        from jiuwenclaw.agentserver.interface import JiuWenClaw

        agent = JiuWenClaw()
        await agent.create_instance()
        self.agents["default_session"] = agent

    def get_agent(self, session_id, *args) -> "JiuWenClaw":
        return self.agents["default_session"]