# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved
"""LLMAgent - ReAct style Agent based on ControllerAgent"""
import asyncio
import datetime
from datetime import timezone
from typing import Dict, List, Any, AsyncIterator, Optional

from openjiuwen.core.common.constants.enums import ControllerType
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import Session
from openjiuwen.core.single_agent.legacy import (
    AgentSession, ControllerAgent,
    PluginSchema,
    LegacyReActAgentConfig as ReActAgentConfig,
    WorkflowSchema,
)
from openjiuwen.core.application.llm_agent.llm_controller import LLMController
from openjiuwen.core.common.logging import logger
from openjiuwen.core.memory.long_term_memory import LongTermMemory
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.foundation.llm import ModelConfig, UserMessage, AssistantMessage
from openjiuwen.core.foundation.tool import Tool
from openjiuwen.core.workflow import Workflow


def create_llm_agent_config(agent_id: str,
                            agent_version: str,
                            description: str,
                            workflows: List[WorkflowSchema],
                            plugins: List[PluginSchema],
                            model: ModelConfig,
                            prompt_template: List[Dict],
                            tools: Optional[List[str]] = None):
    """Create LLM Agent configuration - backward compatible factory function"""
    if tools is None:
        tools = []
    config = ReActAgentConfig(id=agent_id,
                              version=agent_version,
                              description=description,
                              workflows=workflows,
                              plugins=plugins,
                              model=model,
                              prompt_template=prompt_template,
                              tools=tools)
    return config


def create_llm_agent(agent_config: ReActAgentConfig,
                     workflows: List[Workflow] = None,
                     tools: List[Tool] = None):
    """Create LLM Agent - backward compatible factory function"""
    agent = LLMAgent(agent_config)
    agent.add_workflows(workflows)
    agent.add_tools(tools or [])
    return agent


def _memory_log_task_exception(task: asyncio.Task) -> None:
    task_name = task.get_name()
    try:
        task.result()
        logger.info("add memory task [%s] completed successfully", task_name)
    except asyncio.CancelledError:
        logger.warning("add memory task: [%s] cancelled", task_name)
    except Exception as e:
        logger.exception("add memory task: [%s] failed: %s", task_name, e)


def _extract_answer_output(result) -> str:
    """Extract answer type output from result, return empty string if not matching"""
    if not (hasattr(result, 'payload') and isinstance(result.payload, dict)):
        return ""
    payload = result.payload
    if payload.get("result_type") == 'answer' and isinstance(payload.get("output"), str):
        return payload.get("output")
    return ""


def _convert_response_to_message(result) -> AssistantMessage | None:
    assistant_message = None
    if isinstance(result, OutputSchema) and result.type == "answer" and isinstance(result.payload, dict):
        response = result.payload.get("output")
        if response and isinstance(response, str):
            assistant_message = AssistantMessage(content=response)
    elif (isinstance(result, dict) and result.get("result_type") == 'answer'
          and isinstance(result.get("output"), str)):
        assistant_message = AssistantMessage(content=result.get("output"))
    elif isinstance(result, str):
        assistant_message = AssistantMessage(content=result)
    return assistant_message


class LLMAgent(ControllerAgent):
    """LLM Agent - ReAct style Agent based on new architecture

    Core features:
    1. Inherits ControllerAgent, holds LLMController
    2. Uses message queue pattern to process messages
    3. Supports LLM reasoning to generate task plans
    4. Supports multi-round conversations and task execution
    """

    def __init__(self, agent_config: ReActAgentConfig):
        """Initialize LLMAgent

        Args:
            agent_config: ReAct Agent configuration
        """
        # Validate controller_type
        if agent_config.controller_type != ControllerType.ReActController:
            raise NotImplementedError(
                f"LLMAgent requires ReActController, "
                "got {agent_config.controller_type}"
            )

        # Initialize base class (pass controller)
        super().__init__(agent_config, controller=None)
        self._session = AgentSession(config=self._config)
        self._long_term_memory_instance = LongTermMemory()
        self._memory_scope_id = agent_config.memory_scope_id
        self._enable_memory = (self._memory_scope_id and (agent_config.agent_memory_config.enable_long_term_mem
                                                          or len(agent_config.agent_memory_config.mem_variables)))
        self._memory_agent_config = agent_config.agent_memory_config

        self.controller = LLMController(
            config=agent_config,
            context_engine=self.context_engine,
            session=self._session
        )

    async def invoke(self, inputs: Dict, session: Session = None) -> Dict:
        """Synchronous call - fully delegate to controller

        Args:
            inputs: Input data, contains query and conversation_id
            session: Session instance (optional)

        Returns:
            Execution result
        """
        # Fully delegate to ControllerAgent implementation
        result = await super().invoke(inputs, session)

        if self._enable_memory:
            # Async write AI result message memory
            agent_memory_task = asyncio.create_task(self._write_messages_to_memory(inputs, result))
            agent_memory_task.set_name("invoke_add_memory_task")
            agent_memory_task.add_done_callback(_memory_log_task_exception)
        return result

    async def stream(self, inputs: Dict, session: Session = None) -> AsyncIterator[Any]:
        """Streaming invocation - Fully delegate to controller

        Args:
            inputs: Input data
            session: Session instance (if None, auto create)

        Yields:
            Streaming output
        """
        if not self.controller:
            raise RuntimeError(
                f"{self.__class__.__name__} has no controller, "
                "subclass should create controller before invocation"
            )

        # If session not provided, create one
        session_id = inputs.get("conversation_id", "default_session")
        if session is None:
            agent_session = await self._session.pre_run(session_id=session_id)
            need_cleanup = True
            own_stream = True  # Own stream lifecycle
        else:
            agent_session = session
            need_cleanup = False
            own_stream = False  # External owns stream lifecycle

            # Sync agent's tools to external session
            # When external session is provided, agent's tools need to be registered
            if self._tools:
                tools_to_add = [(tool.name, tool) for tool in self._tools]
                Runner.resource_mgr.add_tool(tool=tools_to_add, tag=self.agent_config.id)
            # Sync agent's workflows to external session
            # When external session is provided, agent's workflows need to be registered

        # Store final result for send_to_agent
        final_result_holder = {"result": None}
        await self.context_engine.create_context(session=agent_session)

        # Fully delegate to controller
        async def stream_process():
            try:
                result = await self.controller.invoke(inputs, agent_session)
                final_result_holder["result"] = result
            finally:
                if need_cleanup:
                    await agent_session.post_run()

        task = asyncio.create_task(stream_process())
        result_for_memory_list = []

        if own_stream:
            # Only read from stream_iterator when owning stream
            # If external session passed, external caller handles reading
            async for result in agent_session.stream_iterator():
                result_for_memory_list.append(_extract_answer_output(result))
                yield result

        await task

        # When own_stream = False, yield final result to send_to_agent
        # This allows send_to_agent to get actual single_agent return value
        if not own_stream and final_result_holder["result"] is not None:
            res = final_result_holder["result"]
            if isinstance(res, list):
                for item in res:
                    yield item
            else:
                yield res

        if self._enable_memory:
            # Async write AI result message memory
            result_for_memory = ''.join(result_for_memory_list[:-1])
            agent_memory_task = asyncio.create_task(self._write_messages_to_memory(inputs, result_for_memory))
            agent_memory_task.set_name("stream_add_memory_task")
            agent_memory_task.add_done_callback(_memory_log_task_exception)

    def set_prompt_template(self, prompt_template: List[Dict]):
        self.agent_config.prompt_template = prompt_template
        self._config_wrapper.set_agent_config(self.agent_config)
        self._config = self._config_wrapper
        self.controller.set_llm_controller_prompt_template(prompt_template)

    async def _write_messages_to_memory(self, inputs, result=None):
        user_id = inputs.get("user_id")
        session_id = inputs.get("conversation_id", "default_session")

        if not user_id or not self._long_term_memory_instance:
            return
        message_list = []
        # Add user message
        if not isinstance(inputs, dict):
            logger.warning(f"Unexpected inputs in write_messages_to_memory: {inputs}")
            return
        query = inputs.get("query")
        if query is not None and isinstance(query, str):
            user_message = UserMessage(content=query)
            if user_message and user_message.content != "":
                message_list.append(user_message)
        # Add AI response message if exist
        if result is not None:
            assistant_message = _convert_response_to_message(result)
            if assistant_message is not None and assistant_message.content != "":
                message_list.append(assistant_message)

        try:
            await self._long_term_memory_instance.add_messages(
                user_id=user_id,
                scope_id=self._memory_scope_id,
                session_id=session_id,
                messages=message_list,
                timestamp=datetime.datetime.now(tz=timezone.utc).astimezone(),
                agent_config=self._memory_agent_config,
            )
        except Exception as e:
            logger.error(
                f"Add memory failed: {e}"
            )
