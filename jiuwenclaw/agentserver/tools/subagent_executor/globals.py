# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Factory for per-adapter ForkAgentExecutor instances (no process-global singleton)."""

from __future__ import annotations

from collections.abc import Callable

from openjiuwen.core.foundation.llm import Model
from openjiuwen.harness import DeepAgent

from jiuwenclaw.utils import logger
from jiuwenclaw.agentserver.tools.subagent_executor.executor import ForkAgentExecutor

ResolveSubagentModelFn = Callable[..., tuple[Model, str | None]]


def create_fork_agent_executor(
    parent_agent: DeepAgent,
    model: Model,
    default_role_prompts: dict[str, str] | None = None,
    resolve_model: ResolveSubagentModelFn | None = None,
) -> ForkAgentExecutor:
    """Create a local subagent executor bound to one parent DeepAgent."""
    logger.info("[Subagent] Created local fork agent executor")
    return ForkAgentExecutor(
        parent_agent,
        model=model,
        default_role_prompts=default_role_prompts,
        resolve_model=resolve_model,
    )
