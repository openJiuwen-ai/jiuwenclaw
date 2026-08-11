# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Context variables for skill_turbo online execution.

Holds request-scoped state that skill_turbo_tool depends on:
  - subagent_parent_session (get/set)
  - effective_request_workspace_dir (get/set) — aligned with RuntimePromptRail
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from openjiuwen.core.session.agent import Session


# Context variable to pass parent session from tool execution to executor.
_subagent_parent_session: ContextVar[Optional["Session"]] = ContextVar(
    "subagent_parent_session", default=None
)

# Per-request workspace dir (same resolution as RuntimePromptRail: metadata
# effective_project_dir or adapter default). Used by skill_turbo_tool so the
# turbo node runs against the main agent's workspace for the current request.
_effective_request_workspace_dir: ContextVar[Optional[str]] = ContextVar(
    "effective_request_workspace_dir", default=None
)


def set_subagent_parent_session(session: Optional["Session"]) -> None:
    """Set the parent session context for subagent execution."""
    _subagent_parent_session.set(session)


def get_subagent_parent_session() -> Optional["Session"]:
    """Get the parent session from context."""
    return _subagent_parent_session.get()


def set_effective_request_workspace_dir(workspace_dir: Optional[str]) -> None:
    """Store workspace for the current request (aligned with RuntimePromptRail)."""
    _effective_request_workspace_dir.set(workspace_dir)


def get_effective_request_workspace_dir() -> Optional[str]:
    """Workspace dir for the current request, or None if not set."""
    return _effective_request_workspace_dir.get()
