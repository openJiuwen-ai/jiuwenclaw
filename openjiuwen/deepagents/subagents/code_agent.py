# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Factory function for creating CodeAgent instances."""

from __future__ import annotations

from typing import Any, List, Optional, Dict

from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.deepagents.deep_agent import DeepAgent
from openjiuwen.deepagents.factory import create_deep_agent
from openjiuwen.deepagents.prompts.sections.tools import build_tool_card
from openjiuwen.deepagents.prompts import resolve_language
from openjiuwen.deepagents.rails.filesystem_rail import FileSystemRail
from openjiuwen.deepagents.schema.config import SubAgentConfig
from openjiuwen.deepagents.schema.stop_condition import StopCondition
from openjiuwen.deepagents.schema.workspace import Workspace


DEFAULT_CODE_AGENT_SYSTEM_PROMPT_EN = (
    "Rules: Use tools whenever possible (read/write/edit/grep/list/bash/code), don't guess file contents;"
    "make small, reversible changes; clarify data structures and interfaces before modifying code; "
    "provide testing/verification steps in your output."
)

DEFAULT_CODE_AGENT_SYSTEM_PROMPT_CN = (
    "规则：能用工具就用工具（读/写/编辑/grep/list/bash/code），不要猜文件内容；变更要小、可回滚；"
    "先澄清数据结构与接口，再动代码；输出给出测试/验证步骤。"
)

DEFAULT_CODE_AGENT_SYSTEM_PROMPT: Dict[str, str] = {
    "cn": DEFAULT_CODE_AGENT_SYSTEM_PROMPT_CN,
    "en": DEFAULT_CODE_AGENT_SYSTEM_PROMPT_EN,
}

DEFAULT_CODE_AGENT_DESCRIPTION_EN = """You are a senior software engineer and coding agent, 
    excel at translating tasks into runnable code and verifiable results."""

DEFAULT_CODE_AGENT_DESCRIPTION_CN = "资深软件工程师与代码代理。擅长把任务落到可运行的代码与可验证的结果。"

DEFAULT_CODE_AGENT_DESCRIPTION: Dict[str, str] = {
    "cn": DEFAULT_CODE_AGENT_DESCRIPTION_CN,
    "en": DEFAULT_CODE_AGENT_DESCRIPTION_EN,
}


def create_code_agent(
    model: Model,
    *,
    card: Optional[AgentCard] = None,
    system_prompt: Optional[str] = None,
    tools: Optional[List[ToolCard]] = None,
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
    """Create and configure a predefined CodeAgent instance.

    predefined CodeAgent is equipped with CodeTool and FileSystemRail. You are free to override the configuration.

    Args:
        model: Pre-constructed Model instance.
        card: Agent identity card. If None, a default
            card is created.
        system_prompt: System prompt for the inner
            ReActAgent.
        tools: Tool cards to register on the agent.
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
        name="code_agent",
        description=DEFAULT_CODE_AGENT_DESCRIPTION.get(resolved_language, DEFAULT_CODE_AGENT_DESCRIPTION["cn"]),
    )
    final_prompt = system_prompt or DEFAULT_CODE_AGENT_SYSTEM_PROMPT.get(
        resolved_language, DEFAULT_CODE_AGENT_SYSTEM_PROMPT["cn"]
    )

    # Full override rule: if user passes tools/rails explicitly, do not inject defaults.
    final_tools = tools if tools is not None else [build_tool_card("code", "CodeTool", resolved_language)]
    final_rails = rails if rails is not None else [FileSystemRail(language=resolved_language)]

    return create_deep_agent(
        model=model,
        card=final_card,
        system_prompt=final_prompt,
        tools=final_tools,
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
