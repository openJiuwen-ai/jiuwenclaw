# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Callable, Optional, Union, TYPE_CHECKING

from openjiuwen.core.runner.resources_manager.base import AgentProvider
from openjiuwen.core.runner.drunner.remote_client.remote_agent import RemoteAgent
from openjiuwen.core.runner.drunner.server_adapter.agent_adapter import AgentAdapter
from openjiuwen.core.runner.runner_config import get_runner_config
from openjiuwen.core.runner.resources_manager.abstract_manager import AbstractManager

if TYPE_CHECKING:
    from openjiuwen.core.single_agent.legacy import LegacyBaseAgent as BaseAgent


class AgentMgr(AbstractManager['BaseAgent']):
    _AGENT_ADAPTER = "agent_adapter_"

    def __init__(self):
        super().__init__()
        self._remote_agents: dict[str, "AgentAdapter" | RemoteAgent] = {}

    def add_agent(self, agent_id: str, agent: Union[AgentProvider, RemoteAgent]) -> None:
        if get_runner_config().distributed_mode:
            if not isinstance(agent, RemoteAgent):
                from openjiuwen.core.runner.drunner.server_adapter.agent_adapter import AgentAdapter
                mq_agent_adapter = AgentAdapter(agent_id)
                mq_agent_adapter.start()
                self._remote_agents[self._AGENT_ADAPTER + agent_id] = mq_agent_adapter
        if self._remote_agents.get(agent_id) is not None:
            raise ValueError(f"already same id remote agent, id={agent_id}")
        if isinstance(agent, Callable):
            self._register_resource_provider(agent_id, agent)
        else:
            self._remote_agents[agent_id] = agent


    def remove_agent(self, agent_id: str):
        if get_runner_config().distributed_mode:
            adapter = self._remote_agents.pop(self._AGENT_ADAPTER + agent_id)
            if adapter is not None:
                adapter.stop()
        self._remote_agents.pop(agent_id, None)
        return self._unregister_resource_provider(agent_id)

    async def get_agent(self, agent_id: str) -> Optional[Union['BaseAgent', RemoteAgent]]:
        agent = self._remote_agents.get(agent_id, None)
        if not agent:
            agent = await self._get_resource(agent_id)

        return agent
