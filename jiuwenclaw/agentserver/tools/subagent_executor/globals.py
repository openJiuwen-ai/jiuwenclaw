# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Factory for per-adapter ForkAgentExecutor instances (no process-global singleton)."""

from __future__ import annotations

from openjiuwen.core.foundation.llm import Model
from openjiuwen.harness import DeepAgent

from jiuwenclaw.utils import logger
from jiuwenclaw.agentserver.tools.subagent_executor.executor import ForkAgentExecutor


def create_fork_agent_executor(
    parent_agent: DeepAgent,
    model: Model,
    default_role_prompts: dict[str, str] | None = None,
) -> ForkAgentExecutor:
    """Create a local subagent executor bound to one parent DeepAgent.

    Args:
        parent_agent: Parent DeepAgent instance
        model: Model instance for creating subagents
        default_role_prompts: Default role prompts (used when role_id not found)
    """
    logger.info("[Subagent] Created local fork agent executor")
    return ForkAgentExecutor(
        parent_agent,
        model=model,
        default_role_prompts=default_role_prompts,
    )
