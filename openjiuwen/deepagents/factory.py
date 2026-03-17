# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Factory function for creating DeepAgent instances."""
from __future__ import annotations

from typing import Any, List, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.deepagents.deep_agent import DeepAgent
from openjiuwen.deepagents.schema.config import DeepAgentConfig
from openjiuwen.deepagents.schema.stop_condition import StopCondition
from openjiuwen.deepagents.schema.workspace import Workspace


def create_deep_agent(
    model: Model,
    *,
    card: Optional[AgentCard] = None,
    system_prompt: Optional[str] = None,
    tools: Optional[List[ToolCard]] = None,
    subagents: Optional[List[AgentCard]] = None,
    rails: Optional[List[AgentRail]] = None,
    stop_condition: Optional[StopCondition] = None,
    enable_task_loop: bool = False,
    max_iterations: int = 15,
    workspace: Optional[str | Workspace] = None,
    skills: Optional[List[Any]] = None,
    backend: Optional[Any] = None,
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
        tools: Tool cards to register on the agent.
        subagents: Sub-agent cards (P1).
        rails: AgentRail instances to register.
        stop_condition: Task-loop stop conditions.
        enable_task_loop: Enable outer task loop (P1).
        max_iterations: Max ReAct iterations per
            invoke.
        workspace: Workspace path for file operations.
        skills: Skill definitions (P1).
        backend: Backend protocol instance (P2).
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

    workspace_obj = (
        Workspace(root_path=workspace or "./")
        if not workspace or isinstance(workspace, str)
        else workspace
    )

    config = DeepAgentConfig(
        model=model,
        card=card,
        system_prompt=system_prompt,
        stop_condition=stop_condition,
        enable_task_loop=enable_task_loop,
        max_iterations=max_iterations,
        subagents=subagents,
        tools=tools,
        workspace=workspace_obj,
        skills=skills,
        backend=backend,
    )

    # Forward extra kwargs to config fields
    for key, value in config_kwargs.items():
        if hasattr(config, key):
            setattr(config, key, value)
        else:
            logger.warning(f"Unknown DeepAgentConfig field '{key}', ignored")

    agent = DeepAgent(card)
    agent.configure(config)

    # Register tools on the shared ability manager
    if tools:
        for tool in tools:
            agent.ability_manager.add(tool)

    # Queue rails for lazy async registration
    if rails:
        for rail_inst in rails:
            agent.add_rail(rail_inst)

    return agent
