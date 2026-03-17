# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""DeepAgent runtime-state helpers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, TYPE_CHECKING

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.deepagents.schema.task import TaskPlan

if TYPE_CHECKING:
    from openjiuwen.core.single_agent.rail.base import (
        AgentCallbackContext,
    )
    from openjiuwen.core.session.agent import Session


_SESSION_STATE_KEY = "deepagent"
_SESSION_RUNTIME_ATTR = "_deepagent_runtime_state"


@dataclass
class DeepAgentState:
    """Per-invoke mutable state.

    The object lives on ``ctx.session`` while an
    invoke/stream request is running.  A serializable
    subset can be checkpointed to session state.
    """

    iteration: int = 0
    task_plan: Optional[TaskPlan] = None

    def to_session_dict(self) -> Dict[str, Any]:
        """Convert to a JSON-friendly dict."""
        return {
            "iteration": int(self.iteration),
            "task_plan": (
                self.task_plan.to_dict()
                if self.task_plan is not None
                else None
            ),
        }

    @classmethod
    def from_session_dict(
        cls,
        data: Optional[Dict[str, Any]],
    ) -> "DeepAgentState":
        """Build state from session snapshot."""
        if not data:
            return cls()
        raw_plan = data.get("task_plan")
        task_plan = (
            TaskPlan.from_dict(raw_plan)
            if isinstance(raw_plan, dict)
            else None
        )
        return cls(
            iteration=int(
                data.get("iteration", 0) or 0
            ),
            task_plan=task_plan,
        )


def _require_session(
    ctx: "AgentCallbackContext",
) -> "Session":
    """Require callback context to carry session."""
    session = ctx.session
    if session is None:
        raise build_error(
            StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
            error_msg=(
                "ctx.session is required for "
                "deepagent runtime state."
            ),
        )
    return session


def _read_runtime_state(
    session: "Session",
) -> Optional[DeepAgentState]:
    """Read runtime state cached on session."""
    state = getattr(session, _SESSION_RUNTIME_ATTR, None)
    if state is None:
        return None
    if not isinstance(state, DeepAgentState):
        raise build_error(
            StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
            error_msg=(
                "Invalid deepagent runtime state "
                "type on session."
            ),
        )
    return state


def _write_runtime_state(
    session: "Session",
    state: DeepAgentState,
) -> None:
    """Write runtime state to session cache."""
    setattr(session, _SESSION_RUNTIME_ATTR, state)


def _clear_runtime_state(
    session: "Session",
) -> None:
    """Clear runtime state from session cache."""
    if hasattr(session, _SESSION_RUNTIME_ATTR):
        delattr(session, _SESSION_RUNTIME_ATTR)


def _load_persisted_state(
    session: "Session",
) -> DeepAgentState:
    """Load deepagent snapshot from session state."""
    data = session.get_state(_SESSION_STATE_KEY)
    if not isinstance(data, dict):
        return DeepAgentState()
    return DeepAgentState.from_session_dict(data)


def _save_persisted_state(
    session: "Session",
    state: DeepAgentState,
) -> None:
    """Persist deepagent snapshot into session."""
    session.update_state(
        {_SESSION_STATE_KEY: state.to_session_dict()}
    )


def load_state(
    ctx: "AgentCallbackContext",
) -> DeepAgentState:
    """Load deepagent runtime state from session.

    Runtime state is cached on ``ctx.session`` for the
    request scope.  If cache is empty, loads from
    persisted session state.
    """
    session = _require_session(ctx)
    state = _read_runtime_state(session)
    if state is None:
        loaded = _load_persisted_state(session)
        _write_runtime_state(session, loaded)
        return loaded
    return state


def save_state(
    ctx: "AgentCallbackContext",
    state: Optional[DeepAgentState] = None,
) -> None:
    """Save deepagent runtime state to session.

    When ``state`` is provided it becomes the current
    runtime snapshot.
    """
    session = ctx.session
    if session is None:
        return
    target = (
        state
        if state is not None
        else _read_runtime_state(session)
    )
    if target is None:
        return
    _write_runtime_state(session, target)
    _save_persisted_state(session, target)


def clear_state(
    ctx: "AgentCallbackContext",
    clear_persisted: bool = False,
) -> None:
    """Clear deepagent runtime cache from session.

    Set ``clear_persisted`` to True to also clear
    persisted snapshot.
    """
    session = ctx.session
    if session is None:
        return
    _clear_runtime_state(session)
    if clear_persisted:
        session.update_state({_SESSION_STATE_KEY: None})
