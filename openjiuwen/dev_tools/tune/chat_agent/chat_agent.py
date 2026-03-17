# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
ChatAgent - simplest llm chat single_agent
"""
from typing import Dict, Any, List, AsyncIterator

from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import AgentCard
from openjiuwen.core.single_agent.legacy import LegacyBaseAgent as BaseAgent, LLMCallConfig
from openjiuwen.dev_tools.tune.chat_agent.chat_config import ChatAgentConfig
from openjiuwen.core.context_engine import ContextEngineConfig, ContextEngine
from openjiuwen.core.operator.legacy.llm_call.base import LLMCall
from openjiuwen.core.session.agent import Session
from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.foundation.tool import Tool


def create_chat_agent_config(agent_id: str,
                             agent_version: str,
                             description: str,
                             model: LLMCallConfig,
                             ):
    config = ChatAgentConfig(id=agent_id,
                             version=agent_version,
                             description=description,
                             model=model,
                             )
    return config


def create_chat_agent(agent_config: ChatAgentConfig,
                      tools: List[Tool] = None):
    agent = ChatAgent(agent_config)
    agent.add_tools(tools or [])
    return agent


class ChatAgent(BaseAgent):
    def __init__(self, agent_config: ChatAgentConfig):
        # Initialize BaseAgent
        super().__init__(agent_config)
        
        # Initialize LLM Call
        llm_config = agent_config.model
        self._llm_call = LLMCall(
            llm_config.model.model_name,
            self._init_model(llm_config.model, llm_config.model_client),
            llm_config.system_prompt,
            llm_config.user_prompt,
            llm_config.freeze_system_prompt,
            llm_config.freeze_user_prompt
        )
        self._session = Session(
            session_id="default_session",
            card=AgentCard(id="prompt_optimization")
        )

    def _init_model(self, model_config, model_client_config):
        """Initialize model"""
        return Model(
            model_client_config,
            model_config
        )

    def _create_context_engine(self) -> ContextEngine:
        """ChatAgent uses default configured ContextEngine"""
        context_config = ContextEngineConfig()
        return ContextEngine(
            config=context_config,
        )

    async def invoke(self, inputs: Dict, session: Session = None) -> Dict:
        # 1. init ContextEngine and Session
        session_id = inputs.pop("conversation_id", "default_session")

        if session is None:
            # Compatible with old usage without session
            agent_session = self._session
        else:
            agent_session = session

        # 2. invoke LLMCall
        agent_context = await self.context_engine.create_context(session=session)
        result = await self._llm_call.invoke(
            inputs=inputs,
            session=agent_session,
            history=agent_context.get_messages(),
            tools=await Runner.resource_mgr.get_tool_infos(
                tool_id=[tool.card.id for tool in self.tools] or None,
                tag=self.agent_config.id
            )
        )
        return dict(output=result.content, tool_calls=result.tool_calls)

    async def stream(self, inputs: Dict, session: Session = None) -> AsyncIterator[Any]:
        # 1. init ContextEngine and Session
        session_id = inputs.pop("conversation_id", "default_session")

        if session is None:
            # Compatible with old usage without session
            agent_session = await self._session.pre_run(session_id=session_id)
        else:
            agent_session = session

        # 2. stream invoke LLMCall
        agent_context = await self.context_engine.create_context(session=session)
        stream_iterator = self._llm_call.stream(
            inputs=inputs,
            session=agent_session,
            history=agent_context.get_messages(),
            tools=await Runner.resource_mgr.get_tool_infos()
        )
        if session is None:
            await agent_session.post_run()
        async for result in stream_iterator:
            yield dict(output=result.content, tool_calls=result.tool_calls)

    def get_llm_calls(self) -> Dict:
        return dict(llm_call=self._llm_call)

    def copy(self) -> "BaseAgent":
        return create_chat_agent(self.agent_config)