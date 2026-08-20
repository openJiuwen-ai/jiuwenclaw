# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Context variables for subagent execution.

Provides context isolation and state passing between parent agent and subagents.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from openjiuwen.core.session.agent import Session
    from openjiuwen.core.context_engine.base import ModelContext
    from jiuwenclaw.agentserver.tools.subagent_executor.executor import ForkAgentExecutor


# Context variable to pass parent session from tool execution to executor
_subagent_parent_session: ContextVar[Optional["Session"]] = ContextVar(
    "subagent_parent_session", default=None
)

# Context variable to pass current agent's context for fork context retrieval
# This stores the actual ModelContext object (not agent) to get current messages
_current_agent_context: ContextVar[Optional["ModelContext"]] = ContextVar(
    "current_agent_context", default=None
)

# Context variable to store current agent's subagent_id (for nested session_id)
# Used when subagent creates fork_agent: fork's session_id includes parent subagent's id
# Format: "subagent_1222fc63" or "fork_295a9e7" (the suffix part of session_id)
_current_agent_subagent_id: ContextVar[Optional[str]] = ContextVar(
    "current_agent_subagent_id", default=None
)

# Per-request workspace dir (same resolution as RuntimePromptRail: metadata
# effective_project_dir or adapter default). Used by fork/spawn so subagent
# prompts and Workspace match the main agent for the current request.
_effective_request_workspace_dir: ContextVar[Optional[str]] = ContextVar(
    "effective_request_workspace_dir", default=None
)

# Per-request output dir for agent file isolation (from metadata.output_dir).
# Used by send_file_to_user tool and agent to know where to save output files.
_effective_request_output_dir: ContextVar[Optional[str]] = ContextVar(
    "effective_request_output_dir", default=None
)

# Per-adapter ForkAgentExecutor for spawn/fork tools (set by StreamEventRail or execute_spawn/fork).
_current_fork_agent_executor: ContextVar[Optional["ForkAgentExecutor"]] = ContextVar(
    "current_fork_agent_executor", default=None
)

# Per-request send_file routing context (session_id / request_id / channel_id / metadata).
# send_file_to_user 工具按全局名注册成单例，并发请求间会互相覆盖实例字段。
# 此 ContextVar 按 async 上下文隔离，工具执行时据此动态解析当前请求的路由信息，
# 避免「最后一次注册的 session」串扰到其它并发请求（与 output_dir 同款隔离机制）。
_send_file_request_context: ContextVar[Optional[dict]] = ContextVar(
    "send_file_request_context", default=None
)


def set_subagent_parent_session(session: Optional["Session"]) -> None:
    """Set the parent session context for subagent execution."""
    _subagent_parent_session.set(session)


def get_subagent_parent_session() -> Optional["Session"]:
    """Get the parent session from context."""
    return _subagent_parent_session.get()


def set_current_agent_context(context: Optional["ModelContext"]) -> None:
    """Set the current agent's context for fork context retrieval.

    This stores the actual ModelContext object, so fork_agent can get messages
    directly from the running context (not from storage which may be empty for subagent).

    Args:
        context: ModelContext object from agent's invoke loop
    """
    _current_agent_context.set(context)


def get_current_agent_context() -> Optional["ModelContext"]:
    """Get the current agent's context from context variable.

    Returns the ModelContext that is currently in use (main agent or spawn subagent).
    Used by fork_agent to get fork messages from the correct running context.
    """
    return _current_agent_context.get()


def set_current_agent_subagent_id(subagent_id: Optional[str]) -> None:
    """Set the current agent's subagent_id for nested session_id construction.

    This stores the suffix part of session_id (e.g., "subagent_1222fc63" or "fork_295a9e7").
    Used when subagent creates fork_agent to construct nested session_id.

    Args:
        subagent_id: The subagent_id of the current agent (suffix of session_id)
    """
    _current_agent_subagent_id.set(subagent_id)


def get_current_agent_subagent_id() -> Optional[str]:
    """Get the current agent's subagent_id from context variable.

    Returns the suffix part of session_id for the current agent.
    Used to construct nested session_id when creating fork from subagent.
    """
    return _current_agent_subagent_id.get()


def set_effective_request_workspace_dir(workspace_dir: Optional[str]) -> None:
    """Store workspace for the current request (aligned with RuntimePromptRail)."""
    _effective_request_workspace_dir.set(workspace_dir)


def get_effective_request_workspace_dir() -> Optional[str]:
    """Workspace dir for the current request, or None if not set."""
    return _effective_request_workspace_dir.get()


def set_effective_request_output_dir(output_dir: Optional[str]) -> None:
    """Store output_dir for the current request (from metadata.output_dir)."""
    _effective_request_output_dir.set(output_dir)


def get_effective_request_output_dir() -> Optional[str]:
    """Output dir for the current request, or None if not set."""
    return _effective_request_output_dir.get()


def set_send_file_request_context(
    *,
    request_id: Optional[str] = None,
    session_id: Optional[str] = None,
    channel_id: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> Token:
    """Bind send_file routing context for the current async request.

    返回 Token 供调用方在请求结束时 ``reset_send_file_request_context`` 恢复。
    仅记录非空字段，避免覆盖为 None；调用方应在请求入口设置、finally 中重置。
    """
    ctx: dict = {}
    if request_id is not None:
        ctx["request_id"] = request_id
    if session_id is not None:
        ctx["session_id"] = session_id
    if channel_id is not None:
        ctx["channel_id"] = channel_id
    if metadata is not None:
        ctx["metadata"] = dict(metadata)
    return _send_file_request_context.set(ctx)


def get_send_file_request_context() -> Optional[dict]:
    """Return send_file routing context for the current request, or None if unset."""
    return _send_file_request_context.get()


def reset_send_file_request_context(token: Token) -> None:
    """Restore the previous send_file routing context binding."""
    _send_file_request_context.reset(token)


def set_current_fork_agent_executor(
    executor: Optional["ForkAgentExecutor"],
) -> Token:
    """Bind the local ForkAgentExecutor for the current async context."""
    return _current_fork_agent_executor.set(executor)


def get_current_fork_agent_executor() -> Optional["ForkAgentExecutor"]:
    """Return the ForkAgentExecutor for the current tool execution context."""
    return _current_fork_agent_executor.get()


def reset_current_fork_agent_executor(token: Token) -> None:
    """Restore the previous ForkAgentExecutor binding."""
    _current_fork_agent_executor.reset(token)


def _get_llm_trace_session_id_var() -> ContextVar[str]:
    """Get LLM trace session ID context var (lazy import to avoid circular dependency)."""
    from jiuwenclaw.agentserver.deep_agent.interface_deep import _LLM_TRACE_SESSION_ID
    return _LLM_TRACE_SESSION_ID