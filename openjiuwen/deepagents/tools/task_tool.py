# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""TaskTool implementation for subagent delegation."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, AsyncIterator, List, Optional


if TYPE_CHECKING:
    from openjiuwen.deepagents.deep_agent import DeepAgent

from openjiuwen.deepagents.schema.config import SubAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool import Input, Output, Tool, ToolCard
from openjiuwen.core.session.agent import Session
from openjiuwen.deepagents.tools.base_tool import ToolOutput
from openjiuwen.deepagents.prompts.sections.tools.task_tool import (
    GENERAL_PURPOSE_AGENT_DESC,
)
from openjiuwen.deepagents.prompts.sections.tools import build_tool_card


class TaskTool(Tool):
    """Tool for delegating tasks to ephemeral subagents with isolated context.

    This tool creates a new subagent instance, assigns it an independent
    session to prevent context pollution, and returns the subagent's
    final output after task completion.
    """

    def __init__(
        self,
        card: ToolCard,
        parent_agent: "DeepAgent",
        language: str = "cn",
    ):
        """Initialize TaskTool.

        Args:
            card: Tool metadata card.
            parent_agent: Parent DeepAgent instance used to clone config
                and create subagents.
            language: Language for prompts ('cn' or 'en').
        """
        super().__init__(card)
        self.parent_agent = parent_agent
        self.language = language

    async def invoke(self, inputs: Input, **kwargs) -> ToolOutput:
        """Execute task by delegating to a subagent.

        Args:
            inputs: input_params containing subagent_type and task description.
            **kwargs: Additional parameters, including 'session' for parent session context.

        Returns:
            subagent's final result.

        Raises:
            ToolError: If subagent creation or execution fails.
        """
        parent_session = kwargs.get("session", None)
        if not isinstance(parent_session, Session):
            raise build_error(
                StatusCode.TOOL_TASK_TOOL_INVOKED,
                reason="TaskTool requires a valid session in kwargs",
            )

        # Parse inputs
        if isinstance(inputs, dict):
            subagent_type = inputs.get("subagent_type")
            task_description = inputs.get("task_description")
        else:
            raise build_error(
                StatusCode.TOOL_TASK_TOOL_INVOKED,
                reason=f"Invalid inputs type: {type(inputs)}",
            )

        if not subagent_type or not task_description:
            raise build_error(
                StatusCode.TOOL_TASK_TOOL_INVOKED,
                reason="Both 'subagent_type' and 'task' are required",
            )

        logger.info(f"[TaskTool] Creating subagent: {subagent_type}, parent_session={parent_session.get_session_id()}")

        # Create subagent instance
        subagent = self._create_subagent(subagent_type)

        # Create isolated session_id for subagent
        parent_session_id = parent_session.get_session_id()
        sub_session_id = f"{parent_session_id}_sub_{subagent_type}_{uuid.uuid4().hex[:8]}"

        logger.info(f"[TaskTool] Invoking subagent with isolated session: {sub_session_id}, query: {task_description}")

        try:
            # Invoke subagent with isolated session_id
            result = await subagent.invoke({"query": task_description, "conversation_id": sub_session_id})

            output = result.get("output", "")

            return ToolOutput(success=True, data={"output": output}, error=None)

        except Exception as e:
            logger.error(f"[TaskTool] Subagent: {subagent_type} execution failed, error={e}")
            raise build_error(
                StatusCode.TOOL_TASK_TOOL_INVOKED,
                reason=f"Subagent {subagent_type} execution failed: {e}",
            ) from e

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        pass

    def _create_subagent(self, subagent_type: str) -> "DeepAgent":
        """Create a subagent instance based on subagent_type.

        Args:
            subagent_type: Type of subagent to create or subagent name.

        Returns:
            Configured DeepAgent instance.

        Raises:
            ToolError: If subagent creation fails.
        """
        parent_config = self.parent_agent.deep_config
        if parent_config is None:
            raise build_error(
                StatusCode.TOOL_TASK_TOOL_INVOKED,
                reason="Parent agent has no deep_config",
            )

        # Find matching SubAgentConfig
        spec_or_agent = self._find_subagent_spec(subagent_type)
        if not spec_or_agent:
            raise build_error(
                StatusCode.TOOL_TASK_TOOL_INVOKED,
                reason=f"Subagent spec not found for type: {subagent_type}",
            )

        from openjiuwen.deepagents.deep_agent import DeepAgent

        if isinstance(spec_or_agent, DeepAgent):
            logger.info("[TaskTool] Imported subagent instance already")
            return spec_or_agent

        # overrides main agent's configuration using subagents'
        subagent_model = spec_or_agent.model if spec_or_agent and spec_or_agent.model else parent_config.model

        from openjiuwen.deepagents.factory import create_deep_agent

        logger.info(f"[TaskTool] Creating subagent: type={subagent_type}")

        # Create subagent instance
        subagent = create_deep_agent(
            model=subagent_model,
            card=spec_or_agent.agent_card,
            system_prompt=spec_or_agent.system_prompt,
            tools=spec_or_agent.tools or [],
            rails=spec_or_agent.rails,
            max_iterations=parent_config.max_iterations,
            workspace=parent_config.workspace,
            sys_operation=parent_config.sys_operation,
            skills=spec_or_agent.skills,
        )

        return subagent

    def _find_subagent_spec(self, subagent_type: str) -> Optional["SubAgentConfig | DeepAgent"]:
        """Find SubAgentConfig matching subagent_type.

        Args:
            subagent_type: Type of subagent to find.

        Returns:
            Matching SubAgentConfig or None.
        """
        parent_config = self.parent_agent.deep_config
        if not parent_config or not parent_config.subagents:
            logger.warning("[TaskTool] No subagents found, skipping")
            return None

        from openjiuwen.deepagents.deep_agent import DeepAgent

        for spec in parent_config.subagents:
            if isinstance(spec, SubAgentConfig) and spec.agent_card.name == subagent_type:
                return spec
            if isinstance(spec, DeepAgent):
                card = getattr(spec, "card", None)
                if getattr(card, "name", None) == subagent_type:
                    return spec

        # default general-purpose subagent, using the same system prompt, tools, model, skills as main agent
        if subagent_type == "general-purpose":
            default_desc = GENERAL_PURPOSE_AGENT_DESC.get(self.language, GENERAL_PURPOSE_AGENT_DESC["cn"])
            return SubAgentConfig(
                agent_card=AgentCard(
                    name="general-purpose",
                    description=default_desc,
                ),
                system_prompt=parent_config.system_prompt,
                tools=parent_config.tools,
                model=parent_config.model,
                skills=parent_config.skills,
            )

        return None


def create_task_tool(
    parent_agent: "DeepAgent",
    available_agents: str,
    language: str = "cn",
) -> List[Tool]:
    """Create TaskTool instance for the given parent agent.

    Args:
        parent_agent: Parent DeepAgent instance.
        available_agents: Formatted string describing available subagent types.
        language: Language for tool parameters ('cn' or 'en').

    Returns:
        List containing a single TaskTool instance.
    """
    # 使用统一的 build_tool_card，传递格式化参数
    card = build_tool_card(
        name="task_tool",
        tool_id="task_tool",
        language=language,
        format_args={"available_agents": available_agents},
    )

    return [TaskTool(card=card, parent_agent=parent_agent, language=language)]


__all__ = [
    "TaskTool",
    "create_task_tool",
]
