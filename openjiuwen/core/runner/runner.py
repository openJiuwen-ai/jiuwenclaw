# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import (
    Any,
    AsyncIterator,
    Optional,
    Union,
)

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import LogEventType, runner_logger as logger
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.multi_agent import (
    BaseGroup,
    Session as AgentGroupSession,
)
from openjiuwen.core.runner.callback import AsyncCallbackFramework
from openjiuwen.core.runner.drunner.dmessage_queue.dsubscription.reply_topic_subscription import ReplyTopicSubscription
from openjiuwen.core.runner.drunner.dmessage_queue.message_queue_factory import MessageQueueFactory
from openjiuwen.core.runner.drunner.remote_client.remote_agent import RemoteAgent
from openjiuwen.core.runner.message_queue_base import LocalMessageQueue
from openjiuwen.core.runner.resources_manager.resource_manager import ResourceMgr
from openjiuwen.core.runner.runner_config import (
    DEFAULT_RUNNER_CONFIG,
    get_runner_config,
    RunnerConfig,
    set_runner_config,
)
from openjiuwen.core.session import Config
from openjiuwen.core.session.checkpointer import CheckpointerFactory
from openjiuwen.core.session.stream import BaseStreamMode
from openjiuwen.core.single_agent import (
    BaseAgent,
    create_agent_session,
    LegacyBaseAgent,
    Session as AgentSession,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.workflow import (
    create_workflow_session,
    generate_workflow_key,
    Session as WorkflowSession,
    Workflow,
)


class _RunnerImpl:
    """
    Runner implementation class.
    """
    _DEFAULT_RUNNER_ID = "global"

    _DEFAULT_AGENT_SESSION_ID = "default_session"

    _AGENT_CONVERSATION_ID = "conversation_id"

    def __init__(self, runner_id: str = _DEFAULT_RUNNER_ID, config: RunnerConfig = None):
        """
        Initialize the Runner with configuration.

        Args:
            runner_id: Runner unique id.
            config: Configuration for the runner. If None, defaults will be used.
        """
        self._runner_id = runner_id
        self._resource_manager = ResourceMgr()
        self._message_queue = LocalMessageQueue()
        if config is not None:
            set_runner_config(config)
        else:
            set_runner_config(DEFAULT_RUNNER_CONFIG)
        # Distributed system related components
        self.system_reply_sub: ReplyTopicSubscription | None = None
        self._distribute_message_queue = None
        self._callback_framework = AsyncCallbackFramework()

    @property
    def resource_mgr(self) -> ResourceMgr:
        """Get the resource manager for workflow, agent, agent_group, tool, model, prompt..."""
        return self._resource_manager

    @property
    def pubsub(self):
        """Get the local message queue for publish/subscribe communication."""
        return self._message_queue

    @property
    def dist_pubsub(self):
        """Get the distributed message queue for cross-process communication."""
        return self._distribute_message_queue

    @property
    def callback_framework(self) -> AsyncCallbackFramework:
        """Get the callback framework for asynchronous callbacks."""
        return self._callback_framework

    def set_config(self, config: RunnerConfig):
        """Set the runner configuration with provided config object.

        Args:
            config: The RunnerConfig object containing configuration settings
        """
        logger.info(f"set runner {self._runner_id} config {config}")
        set_runner_config(config)

    def get_config(self):
        """Retrieve the current runner configuration.

        Returns:
            RunnerConfig: The current configuration object
        """
        return get_runner_config()

    async def start(self) -> bool:
        """Start the runner and its associated components, such as message queue."""
        result = True
        logger.info("Begin to start runner", event_type=LogEventType.RUNNER_START, runner_id=self._runner_id)

        # Initialize checkpointer if configured
        checkpointer_config = get_runner_config().checkpointer_config
        if checkpointer_config is not None:
            logger.info(f"Begin to initializing checkpointer with type: {checkpointer_config.type}",
                        event_type=LogEventType.RUNNER_START, runner_id=self._runner_id)
            try:
                # Lazy import checkpointer providers based on type
                if checkpointer_config.type == "redis":
                    try:
                        # Import Redis checkpointer provider to ensure it's registered
                        from openjiuwen.extensions.checkpointer.redis import checkpointer as _  # noqa: F401
                    except ImportError as e:
                        logger.error(f"Redis checkpointer not available. "
                                     f"Please install redis dependencies",
                                     event_type=LogEventType.RUNNER_START, runner_id=self._runner_id, exception=e)
                        raise

                checkpointer = await CheckpointerFactory.create(checkpointer_config)
                CheckpointerFactory.set_default_checkpointer(checkpointer)
                logger.info(f"Succeed to initializing checkpointer with type: {checkpointer_config.type}",
                            event_type=LogEventType.RUNNER_START, runner_id=self._runner_id)
            except Exception as e:
                logger.error(f"Failed to initializing checkpointer with type: {checkpointer_config.type}",
                             event_type=LogEventType.RUNNER_START, runner_id=self._runner_id, exception=e)
                logger.error(f"Failed to start runner",
                             event_type=LogEventType.RUNNER_START, runner_id=self._runner_id, exception=e)
                raise

        if get_runner_config().distributed_mode:
            # start dmq
            self._distribute_message_queue = MessageQueueFactory.create(
                get_runner_config().distributed_config.message_queue_config)
            self._distribute_message_queue.start()
            # start reply topic sub
            self.system_reply_sub = ReplyTopicSubscription(self._distribute_message_queue)
            self.system_reply_sub.activate()
            result = await self._message_queue.start()
        if result:
            logger.info(f"Succeed to start runner",
                        event_type=LogEventType.RUNNER_START, runner_id=self._runner_id)
        else:
            logger.error(f"Failed to start runner, message queue start failed",
                         event_type=LogEventType.RUNNER_START, runner_id=self._runner_id)
        return result

    async def stop(self):
        """Stop the runner and clean up resources."""
        logger.info("Begin to stop runner", event_type=LogEventType.RUNNER_STOP, runner_id=self._runner_id)
        try:
            if get_runner_config().distributed_mode:
                # 1. Stop ReplyTopicSubscription, clean up collector
                if self.system_reply_sub:
                    await self.system_reply_sub.deactivate()
                    self.system_reply_sub = None
                # 2. Stop MQ
                if self._distribute_message_queue:
                    await self._distribute_message_queue.stop()
                    self._distribute_message_queue = None

            result = await self._message_queue.stop()
            logger.info("Succeed to stop runner", event_type=LogEventType.RUNNER_STOP, runner_id=self._runner_id)
            return result
        except Exception as e:
            logger.warning("Failed to stop runner", event_type=LogEventType.RUNNER_STOP, runner_id=self._runner_id,
                           exception=e)
            return False
        finally:
            await self._resource_manager.release()

    async def run_workflow(self,
                           workflow: str | Workflow,
                           inputs: Any,
                           *,
                           session: Optional[str | WorkflowSession | AgentSession] = None,
                           context: ModelContext = None,
                           envs: Optional[dict[str, Any]] = None):
        """
        Execute a workflow with given inputs.

        Args:
            workflow: Workflow name or Workflow instance to execute
            inputs: Input data for the workflow
            session: Existing session ID or Session instance for context persistence
            context: model context
            envs: Environment variables or configuration overrides,
        """
        workflow_instance, workflow_session = await self._prepare_workflow(workflow, session)
        return await workflow_instance.invoke(inputs, session=workflow_session, context=context)

    async def run_workflow_streaming(self,
                                     workflow: str | Workflow,
                                     inputs: Any,
                                     *,
                                     session: Optional[str | WorkflowSession | AgentSession] = None,
                                     context: ModelContext = None,
                                     stream_modes: list[BaseStreamMode] = None,
                                     envs: Optional[dict[str, Any]] = None):
        """
        Execute a workflow with streaming output support.

        Args:
            workflow: Workflow name or Workflow instance to execute
            inputs: Input data for the workflow
            session: Existing session ID or Session instance for context persistence
            context: model context
            stream_modes: Types of streaming data to output
            envs: Environment variables or configuration overrides
        """
        workflow_instance, workflow_session = await self._prepare_workflow(workflow, session)
        async for chunk in workflow_instance.stream(inputs, session=workflow_session,
                                                    stream_modes=stream_modes, context=context):
            yield chunk

    async def run_agent(self,
                        agent: str | BaseAgent | LegacyBaseAgent,
                        inputs: Any,
                        *,
                        session: Optional[str | AgentSession] = None,
                        context: ModelContext = None,
                        envs: Optional[dict[str, Any]] = None,
                        ):
        """
        Execute a single agent with given inputs.

        Args:
            agent: Agent name or BaseAgent instance to execute
            inputs: Input data for the agent
            session: Existing session ID or Session instance for context persistence
            context: model context
            envs: Environment variables or configuration overrides
        """
        agent_instance, agent_session = await self._prepare_agent(agent, inputs)
        if isinstance(agent_instance, RemoteAgent):
            res = await agent_instance.invoke(inputs)
        elif isinstance(agent_instance, LegacyBaseAgent):
            # ControllerAgent handles its own session lifecycle
            res = await agent_instance.invoke(inputs, session=None)
        else:
            res = await agent_instance.invoke(inputs, agent_session)
            await agent_session.post_run()
        return res

    async def run_agent_streaming(self,
                                  agent: str | BaseAgent | LegacyBaseAgent,
                                  inputs: Any,
                                  *,
                                  session: Optional[str | AgentSession] = None,
                                  context: ModelContext = None,
                                  stream_modes: list[BaseStreamMode] = None,
                                  envs: Optional[dict[str, Any]] = None):
        """
           Execute a single agent with streaming output support.

           Args:
               agent: Agent name or BaseAgent instance to execute
               inputs: Input data for the agent
               session: Existing session ID or Session instance for context persistence
               context: model context
               stream_modes: Types of streaming data to output
               envs: Environment variables or configuration override
        """
        agent_instance, agent_session = await self._prepare_agent(agent, inputs, session)
        if isinstance(agent_instance, RemoteAgent):
            async for chunk in agent_instance.stream(inputs):
                yield chunk
        elif isinstance(agent_instance, LegacyBaseAgent):
            # ControllerAgent handles its own session lifecycle
            async for chunk in agent_instance.stream(inputs, session=None):
                yield chunk
        else:
            async for chunk in agent_instance.stream(inputs, session=agent_session):
                yield chunk
            await agent_session.post_run()

    async def run_agent_group(self,
                              agent_group: Union[str, 'BaseGroup'],
                              inputs: Any,
                              *,
                              session: Optional[str | AgentGroupSession] = None,
                              context: ModelContext = None,
                              envs: Optional[dict[str, Any]] = None
                              ):
        """
        Execute a group of agents with given inputs.

        Args:
            agent_group: AgentGroup name or instance to execute
            inputs: Input data for the agent group
            session: Existing session ID or Session instance for context persistence
            context: model contex
            envs: Environment variables or configuration overrides
        """
        agent_group_instance = await self._prepare_agent_group(agent_group)
        return await agent_group_instance.invoke(inputs)

    async def run_agent_group_streaming(self,
                                        agent_group: Union[str, 'BaseGroup'],
                                        inputs: Any,
                                        *,
                                        session: Optional[str | AgentGroupSession] = None,
                                        context: ModelContext = None,
                                        stream_modes: list[BaseStreamMode] = None,
                                        envs: Optional[dict[str, Any]] = None,
                                        ):
        """
        Execute a group of agents with streaming output support.

        Args:
            agent_group: AgentGroup name or instance to execute
            inputs: Input data for the agent group
            session: Existing session ID or Session instance for context persistence
            context: model context
            stream_modes: Types of streaming data to output
            envs: Environment variables or configuration overrides
        """
        agent_group_instance = await self._prepare_agent_group(agent_group)
        async for chunk in agent_group_instance.stream(inputs):
            yield chunk

    async def release(self, session_id: str):
        """
        Release resources associated with a session.

        Args:
            session_id: ID of the session to clean up
        """
        await CheckpointerFactory.get_checkpointer().release(session_id)

    @classmethod
    def _is_called_by_agent(cls, session: AgentSession) -> bool:
        return session and isinstance(session, AgentSession)

    @classmethod
    def _create_workflow_session(cls, session):
        # Convert workflow session
        if not session:
            workflow_session = create_workflow_session()
        elif isinstance(session, str):
            workflow_session = create_workflow_session(session_id=session)
        elif isinstance(session, AgentSession):
            workflow_session = session.create_workflow_session()
        else:
            workflow_session = session
        return workflow_session

    async def _prepare_agent(self, agent: Union[str, BaseAgent], inputs: Any,
                             session: Optional[str | AgentSession] = None):
        session_id = inputs.get(self._AGENT_CONVERSATION_ID,
                                session if isinstance(session, str) else self._DEFAULT_AGENT_SESSION_ID)
        if isinstance(agent, str):
            agent_instance = await self._resource_manager.get_agent(agent_id=agent)
            if agent_instance is None:
                raise build_error(StatusCode.RUNNER_RUN_AGENT_ERROR, agent_id=agent, reason="agent not exist")
            if isinstance(agent_instance, RemoteAgent):
                # Remote single_agent does not add session, keep sessionId in input
                if self._AGENT_CONVERSATION_ID not in inputs:
                    inputs[self._AGENT_CONVERSATION_ID] = session_id
                return agent_instance, None

            agent_session = self._create_agent_session(agent_instance, session_id)
            await agent_session.pre_run(inputs=inputs)
            return agent_instance, agent_session

        agent_session = self._create_agent_session(agent, session_id)
        await agent_session.pre_run(inputs=inputs)
        return agent, agent_session

    async def _prepare_workflow(self, workflow: Union[str, Workflow],
                                session: str | AgentSession | WorkflowSession) -> tuple[Workflow, WorkflowSession]:
        if isinstance(workflow, str):
            workflow_key = workflow
        else:
            workflow_key = generate_workflow_key(workflow.card.id, workflow.card.version)

        workflow_session = self._create_workflow_session(session)
        if isinstance(workflow, str):
            workflow_instance = await self._resource_manager.get_workflow(workflow_id=workflow_key,
                                                                          session=workflow_session)
        else:
            workflow_instance = workflow
        return workflow_instance, workflow_session

    async def _prepare_agent_group(self, agent_group: Union[str, BaseGroup]):
        if isinstance(agent_group, str):
            return await self._resource_manager.get_agent_group(
                group_id=agent_group
            )
        return agent_group

    @staticmethod
    def _create_agent_session(agent, session_id):
        envs = None
        if hasattr(agent, "card"):
            config = agent.config
            card = agent.card
        else:
            # LegacyBaseAgent does not have card attribute
            config = agent.config()
            card = AgentCard(id=config.get_agent_config().id)
        if isinstance(config, Config):
            envs = getattr(config, "_env", None)
        agent_session = create_agent_session(session_id=session_id, envs=envs, card=card)
        return agent_session


# Global runner instance
GLOBAL_RUNNER = _RunnerImpl(config=DEFAULT_RUNNER_CONFIG)


class _ClassProperty:
    """Descriptor for class-level properties."""
    
    def __init__(self, name: str):
        self.name = name
    
    def __get__(self, obj, objtype=None):
        return getattr(GLOBAL_RUNNER, self.name)


class Runner:
    """
    Runner singleton class that proxies all calls to the global runner instance.
    
    This class provides a singleton interface for accessing the global runner instance.
    All method calls and property accesses are automatically proxied to GLOBAL_RUNNER.
    
    Example:
        >>> from openjiuwen.core.runner import Runner
        >>> await Runner.start()
        >>> resource_mgr = Runner.resource_mgr
        >>> await Runner.run_agent(agent, inputs)
    """
    
    # Properties
    resource_mgr: ResourceMgr = _ClassProperty("resource_mgr")  # type: ignore[assignment]
    """Get the resource manager for workflow, agent, agent_group, tool, model, prompt..."""
    
    pubsub = _ClassProperty("pubsub")
    """Get the local message queue for publish/subscribe communication."""
    
    dist_pubsub = _ClassProperty("dist_pubsub")
    """Get the distributed message queue for cross-process communication."""

    system_reply_sub: ReplyTopicSubscription = _ClassProperty("system_reply_sub")
    """Get the reply topic subscription for distributed system reply messages."""
    
    callback_framework: AsyncCallbackFramework = _ClassProperty("callback_framework")  # type: ignore[assignment]
    """Get the callback framework for asynchronous callbacks."""
    
    # Methods
    @classmethod
    def set_config(cls, config: RunnerConfig) -> None:
        """Set the runner configuration with provided config object.

        Args:
            config: The RunnerConfig object containing configuration settings
        """
        GLOBAL_RUNNER.set_config(config)
    
    @classmethod
    def get_config(cls) -> RunnerConfig:
        """Retrieve the current runner configuration.

        Returns:
            RunnerConfig: The current configuration object
        """
        return GLOBAL_RUNNER.get_config()
    
    @classmethod
    async def start(cls) -> bool:
        """Start the runner and its associated components, such as message queue."""
        return await GLOBAL_RUNNER.start()
    
    @classmethod
    async def stop(cls):
        """Stop the runner and clean up resources."""
        return await GLOBAL_RUNNER.stop()
    
    @classmethod
    async def run_workflow(
        cls,
        workflow: str | Workflow,
        inputs: Any,
        *,
            session: Optional[str | WorkflowSession | AgentSession] = None,
        context: Optional[ModelContext] = None,
        envs: Optional[dict[str, Any]] = None
    ) -> Any:
        """
        Execute a workflow with given inputs.

        Args:
            workflow: Workflow name or Workflow instance to execute
            inputs: Input data for the workflow
            session: Existing session ID or Session instance for context persistence
            context: model context
            envs: Environment variables or configuration overrides
        """
        return await GLOBAL_RUNNER.run_workflow(
            workflow=workflow,
            inputs=inputs,
            session=session,
            context=context,
            envs=envs
        )
    
    @classmethod
    async def run_workflow_streaming(
        cls,
        workflow: str | Workflow,
        inputs: Any,
        *,
            session: Optional[str | WorkflowSession | AgentSession] = None,
        context: Optional[ModelContext] = None,
        stream_modes: Optional[list[BaseStreamMode]] = None,
        envs: Optional[dict[str, Any]] = None
    ) -> AsyncIterator[Any]:
        """
        Execute a workflow with streaming output support.

        Args:
            workflow: Workflow name or Workflow instance to execute
            inputs: Input data for the workflow
            session: Existing session ID or Session instance for context persistence
            context: model context
            stream_modes: Types of streaming data to output
            envs: Environment variables or configuration overrides
        """
        async for chunk in GLOBAL_RUNNER.run_workflow_streaming(
            workflow=workflow,
            inputs=inputs,
            session=session,
            context=context,
            stream_modes=stream_modes,
            envs=envs
        ):
            yield chunk
    
    @classmethod
    async def run_agent(
        cls,
        agent: str | BaseAgent | LegacyBaseAgent,
        inputs: Any,
        *,
            session: Optional[str | AgentSession] = None,
        context: Optional[ModelContext] = None,
        envs: Optional[dict[str, Any]] = None,
    ) -> Any:
        """
        Execute a single agent with given inputs.

        Args:
            agent: Agent name or BaseAgent instance to execute
            inputs: Input data for the agent
            session: Existing session ID or Session instance for context persistence
            context: model context
            envs: Environment variables or configuration overrides
        """
        return await GLOBAL_RUNNER.run_agent(
            agent=agent,
            inputs=inputs,
            session=session,
            context=context,
            envs=envs
        )
    
    @classmethod
    async def run_agent_streaming(
        cls,
        agent: str | BaseAgent | LegacyBaseAgent,
        inputs: Any,
        *,
            session: Optional[str | AgentSession] = None,
        context: Optional[ModelContext] = None,
        stream_modes: Optional[list[BaseStreamMode]] = None,
        envs: Optional[dict[str, Any]] = None
    ) -> AsyncIterator[Any]:
        """
        Execute a single agent with streaming output support.

        Args:
            agent: Agent name or BaseAgent instance to execute
            inputs: Input data for the agent
            session: Existing session ID or Session instance for context persistence
            context: model context
            stream_modes: Types of streaming data to output
            envs: Environment variables or configuration override
        """
        async for chunk in GLOBAL_RUNNER.run_agent_streaming(
            agent=agent,
            inputs=inputs,
            session=session,
            context=context,
            stream_modes=stream_modes,
            envs=envs
        ):
            yield chunk
    
    @classmethod
    async def run_agent_group(
        cls,
        agent_group: Union[str, 'BaseGroup'],
        inputs: Any,
        *,
            session: Optional[str | AgentGroupSession] = None,
        context: Optional[ModelContext] = None,
        envs: Optional[dict[str, Any]] = None
    ) -> Any:
        """
        Execute a group of agents with given inputs.

        Args:
            agent_group: AgentGroup name or instance to execute
            inputs: Input data for the agent group
            session: Existing session ID or Session instance for context persistence
            context: model context
            envs: Environment variables or configuration overrides
        """
        return await GLOBAL_RUNNER.run_agent_group(
            agent_group=agent_group,
            inputs=inputs,
            session=session,
            context=context,
            envs=envs
        )
    
    @classmethod
    async def run_agent_group_streaming(
        cls,
        agent_group: Union[str, 'BaseGroup'],
        inputs: Any,
        *,
            session: Optional[str | AgentGroupSession] = None,
        context: Optional[ModelContext] = None,
        stream_modes: Optional[list[BaseStreamMode]] = None,
        envs: Optional[dict[str, Any]] = None,
    ) -> AsyncIterator[Any]:
        """
        Execute a group of agents with streaming output support.

        Args:
            agent_group: AgentGroup name or instance to execute
            inputs: Input data for the agent group
            session: Existing session ID or Session instance for context persistence
            context: model context
            stream_modes: Types of streaming data to output
            envs: Environment variables or configuration overrides
        """
        async for chunk in GLOBAL_RUNNER.run_agent_group_streaming(
            agent_group=agent_group,
            inputs=inputs,
            session=session,
            context=context,
            stream_modes=stream_modes,
            envs=envs
        ):
            yield chunk
    
    @classmethod
    async def release(cls, session_id: str) -> None:
        """
        Release resources associated with a session.

        Args:
            session_id: ID of the session to clean up
        """
        await GLOBAL_RUNNER.release(session_id)