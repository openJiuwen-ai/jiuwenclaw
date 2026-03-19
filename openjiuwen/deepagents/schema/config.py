# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""DeepAgent configuration dataclass."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Union

from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.single_agent.schema.agent_card import (
    AgentCard,
)
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.deepagents.schema.stop_condition import (
    StopCondition,
)
from openjiuwen.deepagents.schema.workspace import (
    Workspace,
)


@dataclass
class DeepAgentConfig:
    """Runtime configuration for DeepAgent.

    Attributes:
        model: Pre-constructed Model instance for LLM
            calls.
        card: Agent identity card.
        system_prompt: System prompt injected into the
            ReAct agent's prompt template.
        context_engine_config: Reserved for P1 context
            engineering configuration.
        enable_task_loop: Whether to enable the outer
            task loop (P1).
        stop_condition: Conditions to terminate the task
            loop.
        max_iterations: Maximum ReAct iterations per
            single invoke.
        subagents: Sub-agent cards (P1).
        tools: Tool cards mounted on the agent.
        workspace: Workspace path for file operations.
        skills: Skill definitions (P1).
        backend: Backend protocol instance (P2).
        sys_operation: System operation.
        completion_timeout: Max seconds to wait for a
            single task-loop iteration to complete.
            Used by the outer loop's wait_completion().
    """
    model: Optional[Model] = None
    card: Optional[AgentCard] = None
    system_prompt: Optional[str] = None
    context_engine_config: Optional[Any] = None
    enable_task_loop: bool = False
    stop_condition: Optional[StopCondition] = None
    max_iterations: int = 15
    subagents: Optional[List[AgentCard]] = None
    tools: Optional[List[ToolCard]] = None
    workspace: Optional[Workspace] = None
    skills: Optional[Union[str, List[str]]] = None
    backend: Optional[Any] = None
    sys_operation: Optional[SysOperation] = None
    completion_timeout: float = 600.0
