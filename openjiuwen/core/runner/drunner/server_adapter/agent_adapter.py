# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Any, AsyncIterator

from openjiuwen.core.runner.drunner.server_adapter.mq_server_adapter import MqServerAdapter
from openjiuwen.core.runner.runner_config import get_runner_config


class AgentAdapter:
    """AgentAdapter"""

    def __init__(self, agent_id: str, version: str = ""):
        self.agent_id = agent_id
        self.version = version
        self.topic = get_runner_config().agent_topic_template().format(agent_id=agent_id, version=version)

        self.server = MqServerAdapter(
            adapter_id=agent_id,
            topic=self.topic,
            invoke_handler=self._handle_invoke,
            stream_handler=self._handle_stream
        )

    def start(self):
        self.server.start()

    async def stop(self):
        await self.server.stop()

    async def _handle_invoke(self, inputs: dict) -> Any:
        from openjiuwen.core.runner import Runner
        agent_result = await Runner.run_agent(self.agent_id, inputs)
        return agent_result

    async def _handle_stream(self, inputs: dict) -> AsyncIterator[Any]:
        from openjiuwen.core.runner import Runner
        async for item in Runner.run_agent_streaming(self.agent_id, inputs):
            yield item

