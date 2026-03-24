# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Factory function for creating DeepAgent instances."""

from __future__ import annotations

import os
from typing import Any, List, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.tool import Tool, ToolCard, McpServerConfig
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import SysOperation, SysOperationCard, OperationMode, LocalWorkConfig
from openjiuwen.deepagents.deep_agent import DeepAgent
from openjiuwen.deepagents.rails import TaskPlanningRail, SkillRail, SubagentRail
from openjiuwen.deepagents.schema.config import (
    DeepAgentConfig,
    VisionModelConfig,
    SubAgentConfig
)
from openjiuwen.deepagents.schema.stop_condition import StopCondition
from openjiuwen.deepagents.schema.workspace import Workspace
from openjiuwen.deepagents.prompts import resolve_language


def _normalize_tools(
    tools: Optional[List[Tool | ToolCard]],
) -> tuple[List[ToolCard], List[Tool]]:
    """Split mixed tool inputs into cards and concrete tool instances."""
    normalized_cards: List[ToolCard] = []
    tool_instances: List[Tool] = []

    for tool in tools or []:
        if isinstance(tool, Tool):
            tool_instances.append(tool)
            normalized_cards.append(tool.card)
            continue
        if isinstance(tool, ToolCard):
            normalized_cards.append(tool)
            continue
        raise TypeError(
            "tools must contain Tool or ToolCard instances, "
            f"got {type(tool).__name__}"
        )

    return normalized_cards, tool_instances


def _register_tool_instances(
    tool_instances: List[Tool],
    *,
    tag: str,
) -> None:
    """Register concrete tool instances so ToolCards become executable."""
    for tool in tool_instances:
        existing_tool = Runner.resource_mgr.get_tool(tool.card.id)
        if existing_tool is not None:
            if existing_tool is not tool:
                raise ValueError(
                    "Tool id is already registered with a different tool instance: "
                    f"tool_id='{tool.card.id}', tool_name='{tool.card.name}'"
                )

            tag_result = Runner.resource_mgr.add_resource_tag(tool.card.id, tag)
            if tag_result.is_err():
                raise tag_result.msg()
            continue

        result = Runner.resource_mgr.add_tool(tool, tag=tag)
        if result.is_err():
            raise result.msg()


def create_deep_agent(
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
    vision_model_config: Optional[VisionModelConfig] = None,
    **config_kwargs: Any,
) -> DeepAgent:
    """Create and configure a DeepAgent instance.

    This is the primary entry point for constructing a
    DeepAgent. The function is synchronous; rails are
    queued and async-registered on the first invoke().

    Args:
        model: Pre-constructed Model instance.
        card: Agent identity card. If None, a default
            card is created.
        system_prompt: System prompt for the inner
            ReActAgent.
        tools: Tool instances or tool cards to register on the agent.
        mcps: MCP server configs to register on the agent.
        subagents: Sub-agent specification or sub-agent instance,
            supports subagent using different model, tools and prompt.
        rails: AgentRail instances to register.
        stop_condition: Task-loop stop conditions.
        enable_task_loop: Enable outer task loop (P1).
        max_iterations: Max ReAct iterations per
            invoke.
        workspace: Workspace path for file operations.
        skills: Skill definitions (P1).
        backend: Backend protocol instance (P2).
        sys_operation: System operation.
        vision_model_config: Shared vision-model
            configuration injected into all vision
            tools registered by DeepAgent rails.
        **config_kwargs: Extra fields forwarded to
            DeepAgentConfig.

    Returns:
        Configured DeepAgent instance ready for
        invoke()/stream().
    """
    if card is None:
        card = AgentCard(
            name="deep_agent",
            description="DeepAgent instance",
        )

    normalized_tools, tool_instances = _normalize_tools(tools)

    workspace_obj = Workspace(root_path=workspace or "./") if not workspace or isinstance(workspace, str) else workspace

    resolved_language = resolve_language(language)

    if not isinstance(sys_operation, SysOperation):
        sysop_card = SysOperationCard(
                id=f"{card.name}_{card.id}",
                mode=OperationMode.LOCAL,
                work_config=LocalWorkConfig(work_dir=workspace_obj.root_path),
            )
        add_result = Runner.resource_mgr.add_sys_operation(sysop_card)
        if add_result.is_err():
            logger.error(f"add_sys_operation failed: {add_result.msg()}")
        sys_operation_obj = Runner.resource_mgr.get_sys_operation(f"{card.name}_{card.id}")
    else:
        sys_operation_obj = sys_operation

    config = DeepAgentConfig(
        model=model,
        card=card,
        system_prompt=system_prompt,
        stop_condition=stop_condition,
        enable_task_loop=enable_task_loop,
        max_iterations=max_iterations,
        subagents=subagents,
        tools=normalized_tools or None,
        mcps=mcps,
        workspace=workspace_obj,
        skills=skills,
        backend=backend,
        sys_operation=sys_operation_obj,
        language=resolved_language,
        prompt_mode=prompt_mode,
        vision_model_config=vision_model_config,
    )

    # Forward extra kwargs to config fields
    for key, value in config_kwargs.items():
        if hasattr(config, key):
            setattr(config, key, value)
        else:
            logger.warning(f"Unknown DeepAgentConfig field '{key}', ignored")

    agent = DeepAgent(card)
    agent.configure(config)

    if tool_instances:
        _register_tool_instances(tool_instances, tag=card.id)

    # Register tools on the shared ability manager
    if normalized_tools:
        for tool in normalized_tools:
            agent.ability_manager.add(tool)

    # Queue rails for lazy async registration
    task_plan_flag: bool = False
    skill_flag: bool = False
    subagent_flag: bool = False
    if rails:
        for rail_inst in rails:
            if isinstance(rail_inst, TaskPlanningRail):
                task_plan_flag = True
            if isinstance(rail_inst, SkillRail):
                skill_flag = True
            if isinstance(rail_inst, SubagentRail):
                subagent_flag = True
            agent.add_rail(rail_inst)

    if enable_task_loop and not task_plan_flag:
        task_plan_rail = TaskPlanningRail(language=resolved_language)
        agent.add_rail(task_plan_rail)
    if skills and not skill_flag:
        skills_dir: List[str] = []
        for skill in skills:
            skill_dir = os.path.join(str(workspace_obj.root_path), skill)
            skills_dir.append(skill_dir)
        skill_rail = SkillRail(skills_dir=skills_dir, skill_mode="all", language=resolved_language)
        agent.add_rail(skill_rail)
    if subagents and not subagent_flag:
        subagent_rail = SubagentRail(language=resolved_language)
        agent.add_rail(subagent_rail)
    return agent
