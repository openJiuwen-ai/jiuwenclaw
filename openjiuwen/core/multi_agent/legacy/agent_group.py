# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved

"""Agent Group Base Module"""

import asyncio
from abc import ABC, abstractmethod
from typing import Any, Dict, AsyncIterator

from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.single_agent.legacy import AgentSession, LegacyBaseAgent as BaseAgent
from openjiuwen.core.multi_agent.legacy.config import AgentGroupConfig
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.logging import logger
from openjiuwen.core.session import Config
from openjiuwen.core.session.agent_group import Session


class AgentGroupSession(AgentSession):
    """AgentGroup Session
    
    Inherits from openjiuwen.core.single_agent.single_agent.AgentSession
    Reuses all capabilities including Session from pre_run()
    
    Why direct inheritance:
    1. AgentSession(config, resource_mgr) has simple constructor
    2. Already includes write_stream() method
    3. Session from pre_run() has stream_iterator()
    """

    def __init__(self, config: Config = None):
        """Initialize AgentGroupSession
        
        Args:
            config: Config object (optional, auto-created)
            resource_mgr: Resource manager (optional, auto-created)
        """
        # Create Config with agent_config if not provided
        if config is None:
            from openjiuwen.core.single_agent.legacy import AgentConfig
            config = Config()
            # Create virtual AgentConfig for Group Session
            agent_config = AgentConfig(id="agent_group_session")
            config.set_agent_config(agent_config)

        # Call parent constructor
        super().__init__(config)

    # write_stream() already implemented in parent AgentSession
    # No need to redefine


class BaseGroup(ABC):
    """
    Abstract base class for implementing single_agent groups.

    This class provides the foundational structure and common functionality
    for managing groups of agents in a multi-single_agent system. It defines the
    essential interface that all concrete single_agent group implementations must
    follow, ensuring consistency across different group types.
    """

    def __init__(self, config: AgentGroupConfig):
        """
        Initialize the single_agent group.

        Args:
            config (AgentGroupConfig): The configuration object for this group.
        """
        self.config: AgentGroupConfig = config
        self.group_id = config.group_id
        self.agents: Dict[str, BaseAgent] = {}

    def add_agent(self, agent_id: str, agent: BaseAgent):
        """
        Register single_agent

        Args:
            agent_id: Agent unique identifier (primary key)
            agent: Agent instance

        Raises:
            ValueError: Agent ID already exists
        """
        if agent_id in self.agents:
            raise build_error(
                StatusCode.AGENT_GROUP_ADD_RUNTIME_ERROR,
                error_msg="Agent ID already exists"
            )
        else:
            if self.get_agent_count() == self.config.max_agents:
                raise build_error(
                    StatusCode.AGENT_GROUP_ADD_RUNTIME_ERROR,
                    error_msg="Agent count exceeds max agents"
                )
            self.agents[agent_id] = agent

            # Auto-inject group reference to single_agent's controller
            # Duck typing: if controller has set_group method, inject self
            if hasattr(agent, 'controller') and agent.controller is not None:
                if hasattr(agent.controller, 'set_group'):
                    agent.controller.set_group(self)
                    logger.debug(
                        f"BaseGroup: Auto-injected group reference to "
                        f"single_agent '{agent_id}' controller"
                    )

    def get_agent_count(self) -> int:
        """
        Get the number of agents currently in the group.

        Returns:
            int: Number of agents in the group
        """
        return len(self.agents)

    @abstractmethod
    async def invoke(self, message, session: Session = None) -> Any:
        """
        Execute a synchronous operation on the single_agent group.

        This method processes message through the group of agents and returns
        the collective result. It should handle the complete execution flow
        including task distribution, single_agent coordination, and result aggregation.
        
        Args:
            message: Message object (for compatibility, also supports Dict for backward compatibility)
            session: Session for single_agent group instance
            
        Returns:
            The collective output from the single_agent group
        """
        raise NotImplementedError(
            f"invoke method or controller method must be implemented {self.__class__.__name__}"
        )

    @abstractmethod
    async def stream(self, message, session: Session = None) -> AsyncIterator[Any]:
        """
        Execute a streaming operation on the single_agent group.

        This method processes message and returns results as a stream,
        allowing for real-time or progressive output from the single_agent group.
        Useful for long-running operations or when intermediate results are needed.

        Args:
            message: Message object (for compatibility, also supports Dict for backward compatibility)
            session: Session for single_agent group instance

        Returns:
            The collective output from the single_agent group
        """
        raise NotImplementedError(
            f"stream method must be implemented by {self.__class__.__name__}"
        )


class ControllerGroup(BaseGroup):
    """Agent Group with Controller
    
    Design features (similar to ControllerAgent):
    1. Inherits BaseGroup, reuses single_agent management capabilities
    2. Holds GroupController, fully delegates message routing logic
    3. Automatically configures GroupController (via setup_from_group)
    4. invoke/stream fully delegated to group_controller
    5. Session lifecycle: pre_run -> controller.invoke -> post_run
    """

    def __init__(self, config: AgentGroupConfig, group_controller=None):
        """Initialize ControllerGroup
        
        Args:
            config: AgentGroup configuration object
            group_controller: Optional GroupController instance (will be auto-configured)
        
        Usage:
            # Simplest way - group_controller auto-configured
            group_controller = DefaultGroupController()  # No parameters needed
            group = ControllerGroup(config=config, group_controller=group_controller)
        """
        super().__init__(config)
        self.group_controller = group_controller

        # Initialize session (like BaseAgent)
        self._session = AgentGroupSession()

        # Auto-configure group_controller
        if self.group_controller is not None:
            self._setup_group_controller()

    def _setup_group_controller(self):
        """Auto-configure group_controller (inject group reference)"""
        if hasattr(self.group_controller, 'setup_from_group'):
            self.group_controller.setup_from_group(self)

    def _convert_message(self, message):
        """Convert dict to Message if needed (backward compatibility)"""
        from openjiuwen.core.controller.legacy import Event
        if isinstance(message, dict):
            return Event.create_user_event(
                content=message.get("content") or message.get("query", ""),
                conversation_id=message.get("conversation_id", "default_session")
            )
        return message

    async def invoke(self, message, session: Session = None) -> Any:
        """Synchronous invocation - Fully delegated to group_controller
        
        Lifecycle: pre_run -> controller.invoke -> post_run
        
        Args:
            message: Message object (carries message_type for routing)
            session: Session instance (optional, auto-created if None)
        
        Returns:
            Processing result
        """
        if not self.group_controller:
            raise RuntimeError(
                f"{self.__class__.__name__} has no group_controller"
            )

        message = self._convert_message(message)
        session_id = message.context.conversation_id if message.context else "default"

        # If session not provided, use self._session.pre_run to create task session
        if session is None:
            task_session = await self._session.pre_run(session_id=session_id)
            need_cleanup = True
        else:
            if isinstance(session, Session):
                task_session = getattr(session, "_inner")
            else:
                task_session = session
            need_cleanup = False

        try:
            # Fully delegate to group_controller
            result = await self.group_controller.invoke(message, task_session)
            return result if result is not None else {"output": "processed"}
        finally:
            if need_cleanup:
                await task_session.post_run()

    async def stream(self, message, session: Session = None) -> AsyncIterator[Any]:
        """Streaming invocation - real streaming output
        
        Design: 
        1. Background task executes group_controller.invoke
        2. group_controller.send_to_agent calls single_agent.stream and forwards chunks to session
        3. This method reads from session.stream_iterator() in real-time and yields
        
        Streaming data source:
        - Sub-single_agent streaming output forwarded via shared session
        - Includes __interaction__, workflow_final and all types
        
        Args:
            message: Message object (carries message_type for routing)
            session: Session instance (optional, auto-created if None)
        
        Yields:
            Streaming output from sub-agents
        """
        if not self.group_controller:
            raise RuntimeError(
                f"{self.__class__.__name__} has no group_controller"
            )

        message = self._convert_message(message)
        session_id = message.context.conversation_id if message.context else "default"

        # If session not provided, use self._session.pre_run to create task session
        if session is None:
            task_session = await self._session.pre_run(session_id=session_id)
            need_cleanup = True
        else:
            if isinstance(session, Session):
                task_session = getattr(session, "_inner")
            else:
                task_session = session
            need_cleanup = False

        # Background task executes group_controller.invoke
        # send_to_agent calls single_agent.stream and writes chunks to task_session
        async def run_controller():
            try:
                await self.group_controller.invoke(message, task_session)
            finally:
                if need_cleanup:
                    await task_session.post_run()

        task = asyncio.create_task(run_controller())

        # Real streaming read: get chunks from stream_iterator in real-time
        async for chunk in task_session.stream_iterator():
            yield chunk

        # Wait for background task to complete
        await task
