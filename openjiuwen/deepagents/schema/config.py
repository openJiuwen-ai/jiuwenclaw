# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""DeepAgent configuration dataclass."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from typing import Any, List, Optional, Union

from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.single_agent.schema.agent_card import (
    AgentCard,
)
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.deepagents.schema.stop_condition import (
    StopCondition,
)
from openjiuwen.deepagents.schema.workspace import (
    Workspace,
)

DEFAULT_OPENAI_VISION_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENROUTER_VISION_MODEL = "google/gemini-2.5-pro"
DEFAULT_OPENAI_VISION_MODEL = "gpt-4.1-mini"


@dataclass
class VisionModelConfig:
    """Shared runtime configuration for all DeepAgent vision tools."""

    api_key: str = ""
    base_url: str = DEFAULT_OPENAI_VISION_BASE_URL
    model: str = DEFAULT_OPENAI_VISION_MODEL
    max_retries: int = 3

    @classmethod
    def from_env(cls) -> "VisionModelConfig":
        """Build a vision config from environment variables."""
        api_key = (
            os.getenv("VISION_API_KEY")
            or os.getenv("OPENROUTER_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or ""
        )
        base_url = (
            os.getenv("VISION_BASE_URL")
            or os.getenv("OPENROUTER_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or DEFAULT_OPENAI_VISION_BASE_URL
        )
        model = os.getenv("VISION_MODEL")

        if not model:
            if "openrouter.ai" in base_url:
                model = DEFAULT_OPENROUTER_VISION_MODEL
            else:
                model = DEFAULT_OPENAI_VISION_MODEL

        return cls(
            api_key=api_key,
            base_url=base_url,
            model=model,
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
        subagents: Sub-agent specifications or Sub-agent instance.
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
    subagents: Optional[List[SubAgentConfig | "DeepAgent"]] = None
    tools: Optional[List[ToolCard]] = None
    workspace: Optional[Workspace] = None
    skills: Optional[Union[str, List[str]]] = None
    backend: Optional[Any] = None
    sys_operation: Optional[SysOperation] = None
    completion_timeout: float = 600.0
    language: Optional[str] = None
    prompt_mode: Optional[str] = None
    vision_model_config: Optional[VisionModelConfig] = None

    # Progressive tool exposure config
    progressive_tool_enabled: bool = False
    progressive_tool_always_visible_tools: List[str] = field(default_factory=list)
    progressive_tool_default_visible_tools: List[str] = field(default_factory=list)
    progressive_tool_max_loaded_tools: int = 12


@dataclass
class SubAgentConfig:
    """Subagent 完整配置，支持自定义 system_prompt、tools、model。"""

    agent_card: AgentCard
    system_prompt: str
    tools: List[ToolCard] = field(default_factory=list)
    model: Optional[Model] = None
    rails: Optional[List[AgentRail]] = None
    skills: Optional[List[str]] = None