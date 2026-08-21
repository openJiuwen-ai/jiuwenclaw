# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Request-local executor management for subagent execution."""

from __future__ import annotations

from contextvars import ContextVar, Token

from openjiuwen.core.foundation.llm import Model
from openjiuwen.harness import DeepAgent

from jiuwenclaw.utils import logger
from jiuwenclaw.agentserver.tools.subagent_executor.executor import ForkAgentExecutor


_current_executor: ContextVar[ForkAgentExecutor | None] = ContextVar(
    "current_subagent_executor",
    default=None,
)


def get_fork_agent_executor() -> ForkAgentExecutor | None:
    """Get the executor bound to the current asynchronous request context."""
    return _current_executor.get()


def set_fork_agent_executor(
    executor: ForkAgentExecutor | None,
) -> Token[ForkAgentExecutor | None]:
    """Bind an executor to the current asynchronous request context."""
    return _current_executor.set(executor)


def reset_fork_agent_executor(token: Token[ForkAgentExecutor | None]) -> None:
    """Restore the executor binding represented by ``token``."""
    _current_executor.reset(token)


def init_subagent_executor(
    parent_agent: DeepAgent,
    model: Model,
    default_role_prompts: dict[str, str] | None = None,
) -> ForkAgentExecutor:
    """Initialize the subagent executor with parent agent and model.

    Args:
        parent_agent: Parent DeepAgent instance
        model: Model instance for creating subagents
        default_role_prompts: Default role prompts (used when role_id not found)
    """
    executor = ForkAgentExecutor(
        parent_agent,
        model=model,
        default_role_prompts=default_role_prompts,
    )
    set_fork_agent_executor(executor)
    logger.info("[Subagent] Initialized request-local subagent executor")
    return executor
