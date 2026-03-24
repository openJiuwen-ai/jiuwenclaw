# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Dict, Type

from openjiuwen.core.common.logging import LogManager
from openjiuwen.core.foundation.llm import Model

from openjiuwen.dev_tools.agent_builder.builders.base import BaseAgentBuilder
from openjiuwen.dev_tools.agent_builder.executor.history_manager import HistoryManager
from openjiuwen.dev_tools.agent_builder.utils.enums import AgentType

logger = LogManager.get_logger("agent_builder")


class AgentBuilderFactory:
    """Agent builder factory for creating builder instances.

    Responsible for creating different types of builder instances.
    Uses registration mechanism to support extending new agent types.
    """

    _builders: Dict[AgentType, Type[BaseAgentBuilder]] = {}

    @classmethod
    def create(
        cls,
        agent_type: AgentType,
        llm: Model,
        history_manager: HistoryManager,
    ) -> BaseAgentBuilder:
        """
        Create a builder instance.
        """
        if not cls._builders:
            from openjiuwen.dev_tools.agent_builder.builders.llm_agent.builder import (
                LlmAgentBuilder,
            )
            from openjiuwen.dev_tools.agent_builder.builders.workflow.builder import (
                WorkflowBuilder,
            )

            cls._builders = {
                AgentType.LLM_AGENT: LlmAgentBuilder,
                AgentType.WORKFLOW: WorkflowBuilder,
            }

        builder_class = cls._builders.get(agent_type)

        if builder_class is None:
            error_msg = f"Unsupported agent type: {agent_type}"
            logger.error(
                "Unsupported agent type",
                agent_type=agent_type.value,
            )
            raise ValueError(error_msg)

        logger.debug(
            "Creating builder instance",
            agent_type=agent_type.value,
            builder_class=builder_class.__name__,
        )

        return builder_class(llm, history_manager)

    @classmethod
    def register(
        cls,
        agent_type: AgentType,
        builder_class: Type[BaseAgentBuilder],
    ) -> None:
        """
        Register a new builder type.
        """
        if not issubclass(builder_class, BaseAgentBuilder):
            raise TypeError(
                f"Builder class must inherit from BaseAgentBuilder, got: {builder_class.__name__}"
            )

        cls._builders[agent_type] = builder_class
        logger.info(
            "Registered new builder type",
            agent_type=agent_type.value,
            builder_class=builder_class.__name__,
        )

    @classmethod
    def get_supported_types(cls) -> list[AgentType]:
        """
        Get list of supported agent types.
        """
        return list(cls._builders.keys())

    @classmethod
    def clear_registry(cls) -> None:
        """Clear all registered builder classes (e.g. for tests or reset)."""
        cls._builders = {}

    @classmethod
    def get_registered_builders(cls) -> Dict[AgentType, Type[BaseAgentBuilder]]:
        """Return a shallow copy of the agent-type to builder-class mapping."""
        return dict(cls._builders)
