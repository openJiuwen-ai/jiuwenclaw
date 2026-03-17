# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Optional

from openjiuwen.core.runner.resources_manager.abstract_manager import AbstractManager
from openjiuwen.core.multi_agent import BaseGroup
from openjiuwen.core.runner.resources_manager.base import AgentGroupProvider


class AgentGroupMgr(AbstractManager[BaseGroup]):
    def __init__(self):
        super().__init__()

    def add_agent_group(self, agent_group_id: str, agent_group: AgentGroupProvider):
        self._register_resource_provider(agent_group_id, agent_group)

    def remove_agent_group(self, agent_group_id: str) -> Optional[AgentGroupProvider]:
        return self._unregister_resource_provider(agent_group_id)

    async def get_agent_group(self, agent_group_id: str) -> Optional[BaseGroup]:
        return await self._get_resource(agent_group_id)
