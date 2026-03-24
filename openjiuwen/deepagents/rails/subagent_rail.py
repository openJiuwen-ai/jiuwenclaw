# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SubagentRail — registers task tool on DeepAgent for subagent delegation."""

from __future__ import annotations

from typing import List, TYPE_CHECKING

from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.deepagents.prompts.sections.task_tool import (
    build_task_section,
)
from openjiuwen.deepagents.prompts.sections.tools.task_tool import (
    GENERAL_PURPOSE_AGENT_DESC,
)
from openjiuwen.deepagents.rails.base import DeepAgentRail
from openjiuwen.deepagents.schema.config import SubAgentConfig
from openjiuwen.deepagents.tools.task_tool import create_task_tool

if TYPE_CHECKING:
    from openjiuwen.deepagents.deep_agent import DeepAgent


class SubagentRail(DeepAgentRail):
    """Rail that registers task tool for subagent delegation.

    This rail enables the main agent to delegate complex, multi-steps
    tasks to ephemeral subagents with isolated context windows.
    """

    priority = 95

    def __init__(self, language: str = "cn") -> None:
        """Initialize SubagentRail.

        Args:
            language: Language for prompts ('cn' or 'en').
        """
        super().__init__()
        self.tools = None
        self.language = language
        self.system_prompt_builder = None

    def init(self, agent) -> None:
        """Register task tool on the agent.

        Args:
            agent: DeepAgent instance to register tools on.
        """
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

        # Skip registration if no subagents are configured
        if not agent.deep_config.subagents:
            logger.info("[SubagentRail] No subagents configured, skipping task tool registration")
            return

        # Build available_agents description for tool registration
        available_agents = self._build_available_agents_description(agent.deep_config.subagents)

        # Create and register task tool (使用统一的 build_tool_card)
        tools = create_task_tool(
            parent_agent=agent,
            available_agents=available_agents,
            language=self.language,
        )
        self.tools = tools

        # Register tools in resource manager and ability manager
        Runner.resource_mgr.add_tool(list(tools))
        for tool in tools:
            agent.ability_manager.add(tool.card)

        logger.info(f"[SubagentRail] Registered task tool with {len(agent.deep_config.subagents)} subagent(s)")

    def uninit(self, agent) -> None:
        """Remove task tool from the agent.

        Args:
            agent: DeepAgent instance to remove tools from.
        """
        if self.tools and hasattr(agent, "ability_manager"):
            for tool in self.tools:
                name = getattr(tool, "name", None)
                if name:
                    agent.ability_manager.remove(name)
                tool_id = tool.card.id
                if tool_id:
                    Runner.resource_mgr.remove_tool(tool_id)

            logger.info("[SubagentRail] Unregistered task tool")

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Update system_prompt_builder with task-tool section before model call."""
        if not self.tools or self.system_prompt_builder is None:
            return

        task_section = build_task_section(language=self.language)
        if task_section is not None:
            self.system_prompt_builder.add_section(task_section)
        else:
            self.system_prompt_builder.remove_section("task_tool")

    def _build_available_agents_description(self, subagents: List[SubAgentConfig | "DeepAgent"]) -> str:
        """Build description of available subagents for tool registration.

        Returns:
            Formatted string describing available subagent types.
        """
        default_desc = GENERAL_PURPOSE_AGENT_DESC.get(self.language, GENERAL_PURPOSE_AGENT_DESC["cn"])

        if not subagents:
            return f'"general-purpose": {default_desc}'

        # Build available subagent types
        lines = []
        has_general_purpose = False

        for spec in subagents:
            agent_name, agent_desc = self._extract_agent_meta(spec)
            if agent_name == "general-purpose":
                has_general_purpose = True
            lines.append(f'"{agent_name}": {agent_desc}')

        # Add default general-purpose if not explicitly defined
        if not has_general_purpose:
            lines.insert(0, f'"general-purpose": {default_desc}')

        return "\n".join(lines)

    def _extract_agent_meta(self, spec: SubAgentConfig | "DeepAgent") -> tuple[str, str]:
        if isinstance(spec, SubAgentConfig):
            return spec.agent_card.name, spec.agent_card.description

        card = getattr(spec, "card", None)
        name = getattr(card, "name", None) or "general-purpose"
        description = getattr(card, "description", None) or "DeepAgent instance"
        return name, description

__all__ = [
    "SubagentRail",
]
