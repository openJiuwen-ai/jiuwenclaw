# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import List, Any

from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.security.user_config import UserConfig
from openjiuwen.core.context_engine import ContextEngine
from openjiuwen.core.session.agent import Session
from openjiuwen.core.foundation.llm import (
    BaseMessage, AssistantMessage, UserMessage, ToolMessage
)
from openjiuwen.core.single_agent.legacy.config import AgentConfig


class MessageUtils:
    """Message utilities for adding and retrieving messages"""

    @staticmethod
    def should_add_user_message(
        query: str,
        context_engine: ContextEngine,
        session: Session
    ) -> bool:
        """Check if user message should be added
        
        Args:
            query: User input
            context_engine: Context engine
            session: Session instance
        
        Returns:
            bool: Whether to add user message
        """
        agent_context = context_engine.get_context(session_id=session.get_session_id())
        last_message = agent_context.get_messages(size=1)

        if not last_message:
            return True

        last_message = last_message[0]

        if last_message.role == 'user' and last_message.content == query:
            logger.info("Skipping duplicate user message")
            return False

        return True

    @staticmethod
    async def add_user_message(
        query: Any,
        context_engine: ContextEngine,
        session: Session
    ):
        """Add user message to chat history
        
        Args:
            query: User input
            context_engine: Context engine
            session: Session instance
        """
        if MessageUtils.should_add_user_message(query, context_engine, session):
            agent_context = context_engine.get_context(session_id=session.get_session_id())
            user_message = UserMessage(content=query)
            await agent_context.add_messages(user_message)
            if UserConfig.is_sensitive():
                logger.info("Added user message")
            else:
                logger.info(f"Added user message: {query}")

    @staticmethod
    async def add_ai_message(
        ai_message: AssistantMessage,
        context_engine: ContextEngine,
        session: Session
    ):
        """Add Assistant message to chat history
        
        Args:
            ai_message: Assistant message object
            context_engine: Context engine
            session: Session instance
        """
        if ai_message:
            agent_context = context_engine.get_context(session_id=session.get_session_id())
            await agent_context.add_messages(ai_message)

    @staticmethod
    async def add_tool_message(
        tool_message: ToolMessage,
        context_engine: ContextEngine,
        session: Session
    ):
        """Add tool message to chat history
        
        Args:
            tool_message: Tool message object
            context_engine: Context engine
            session: Session instance
        """
        if tool_message:
            agent_context = context_engine.get_context(session_id=session.get_session_id())
            await agent_context.add_messages(tool_message)

    @staticmethod
    async def add_workflow_message(
        message: BaseMessage,
        workflow_id: str,
        context_engine: ContextEngine,
        session: Session
    ):
        """Add message to workflow chat history
        
        Args:
            message: Message object
            workflow_id: Workflow ID
            context_engine: Context engine
            session: Session instance
        """
        workflow_context = context_engine.get_context(
            context_id=workflow_id,
            session_id=session.get_session_id()
        )
        await workflow_context.add_messages(message)

    @staticmethod
    def get_chat_history(
        context_engine: ContextEngine,
        session: Session,
        config: AgentConfig
    ) -> List[BaseMessage]:
        """Get chat history
        
        Args:
            context_engine: Context engine
            session: Session instance
            config: Agent config
        
        Returns:
            List[BaseMessage]: Chat history message list
        """
        agent_context = context_engine.get_context(session_id=session.get_session_id())
        chat_history = agent_context.get_messages()
        max_rounds = config.constrain.reserved_max_chat_rounds
        return chat_history[-2 * max_rounds:]




