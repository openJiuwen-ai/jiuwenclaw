# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Optional

from openjiuwen.core.runner.resources_manager.abstract_manager import AbstractManager
from openjiuwen.core.multi_agent import BaseTeam
from openjiuwen.core.runner.resources_manager.base import AgentTeamProvider


class AgentTeamMgr(AbstractManager[BaseTeam]):
    def __init__(self):
        super().__init__()

    def add_agent_team(self, agent_team_id: str, agent_team: AgentTeamProvider):
        self._register_resource_provider(agent_team_id, agent_team)

    def remove_agent_team(self, agent_team_id: str) -> Optional[AgentTeamProvider]:
        return self._unregister_resource_provider(agent_team_id)

    async def get_agent_team(self, agent_team_id: str) -> Optional[BaseTeam]:
        return await self._get_resource(agent_team_id)
