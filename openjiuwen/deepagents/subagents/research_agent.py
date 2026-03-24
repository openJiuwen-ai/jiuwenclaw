# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Factory function for creating ResearchAgent instances."""

from __future__ import annotations

from typing import Any, List, Optional, Dict

from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.tool import Tool, ToolCard, McpServerConfig
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.deepagents.deep_agent import DeepAgent
from openjiuwen.deepagents.factory import create_deep_agent
from openjiuwen.deepagents.prompts import resolve_language
from openjiuwen.deepagents.rails.filesystem_rail import FileSystemRail
from openjiuwen.deepagents.schema.config import SubAgentConfig
from openjiuwen.deepagents.schema.stop_condition import StopCondition
from openjiuwen.deepagents.schema.workspace import Workspace


DEFAULT_RESEARCH_AGENT_SYSTEM_PROMPT_EN = (
    "You are a research assistant responsible for conducting research around the topic provided by the user."
    "Only return the final research results."
)

DEFAULT_RESEARCH_AGENT_SYSTEM_PROMPT_CN = "你是研究助理，负责围绕用户输入的主题开展调研，仅需返回最终研究结果。"

DEFAULT_RESEARCH_AGENT_SYSTEM_PROMPT: Dict[str, str] = {
    "cn": DEFAULT_RESEARCH_AGENT_SYSTEM_PROMPT_CN,
    "en": DEFAULT_RESEARCH_AGENT_SYSTEM_PROMPT_EN,
}

DEFAULT_RESEARCH_AGENT_DESCRIPTION_EN = """Focuses on research and investigation tasks. 
When users want to investigate a specific issue, this agent can be used to execute research work. 
Provide only one topic to this researcher at a time."""

DEFAULT_RESEARCH_AGENT_DESCRIPTION_CN = (
    "专注于研究调查任务，当用户想要调查某问题时，可使用该代理执行研究工作。每次只给这位研究员一个主题。"
)

DEFAULT_RESEARCH_AGENT_DESCRIPTION: Dict[str, str] = {
    "cn": DEFAULT_RESEARCH_AGENT_DESCRIPTION_CN,
    "en": DEFAULT_RESEARCH_AGENT_DESCRIPTION_EN,
}


def create_research_agent(
    model: Model,
    *,
    card: Optional[AgentCard] = None,
    system_prompt: Optional[str] = None,
    tools: Optional[List[Tool | ToolCard]] = None,
    mcps: Optional[List[McpServerConfig]] = None,
    subagents: Optional[List[SubAgentConfig | DeepAgent]] = None,
    rails: Optional[List[AgentRail]] = None,
    stop_condition: Optional[StopCondition] = None,
    enable_task_loop: bool = False,
    max_iterations: int = 15,
    workspace: Optional[str | Workspace] = None,
    skills: Optional[List[str]] = None,
    backend: Optional[Any] = None,
    sys_operation: Optional[SysOperation] = None,
    language: Optional[str] = None,
    prompt_mode: Optional[str] = None,
    **config_kwargs: Any,
) -> DeepAgent:
    """Create and configure a predefined ResearchAgent instance.

    predefined ResearchAgent is equipped with FileSystemRail and web search tool.
    You are free to override the configuration.

    Args:
        model: Pre-constructed Model instance.
        card: Agent identity card. If None, a default
            card is created.
        system_prompt: System prompt for the inner
            ReActAgent.
        tools: Tool instances or tool cards to register on the agent.
        mcps: MCP server configs to register on the agent.
        subagents: Sub-agent specification, supports subagent using different model, tools and prompt.
        rails: AgentRail instances to register.
        stop_condition: Task-loop stop conditions.
        enable_task_loop: Enable outer task loop.
        max_iterations: Max ReAct iterations per
            invoke.
        workspace: Workspace path for file operations.
        skills: Skill definitions.
        backend: Backend protocol instance .
        sys_operation: System operation.
        **config_kwargs: Extra fields forwarded to
            DeepAgentConfig.

    Returns:
        Configured CodeAgent instance ready for
        invoke()/stream().
    """
    resolved_language = resolve_language(language)

    final_card = card or AgentCard(
        name="research_agent",
        description=DEFAULT_RESEARCH_AGENT_DESCRIPTION.get(resolved_language, DEFAULT_RESEARCH_AGENT_DESCRIPTION["cn"]),
    )
    final_prompt = system_prompt or DEFAULT_RESEARCH_AGENT_SYSTEM_PROMPT.get(
        resolved_language, DEFAULT_RESEARCH_AGENT_SYSTEM_PROMPT["cn"]
    )

    # Full override rule: if user passes tools/rails explicitly, do not inject defaults.
    final_rails = rails if rails is not None else [FileSystemRail(language=resolved_language)]

    return create_deep_agent(
        model=model,
        card=final_card,
        system_prompt=final_prompt,
        tools=tools,
        mcps=mcps,
        subagents=subagents,
        rails=final_rails,
        stop_condition=stop_condition,
        enable_task_loop=enable_task_loop,
        max_iterations=max_iterations,
        workspace=workspace,
        skills=skills,
        backend=backend,
        sys_operation=sys_operation,
        language=resolved_language,
        prompt_mode=prompt_mode,
        **config_kwargs,
    )
