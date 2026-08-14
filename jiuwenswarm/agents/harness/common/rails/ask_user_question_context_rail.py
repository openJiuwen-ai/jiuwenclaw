# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""AskUserQuestionContextRail - re-bind ask request ContextVars inside the agent.

dev-stable runs the single-agent turn in a background runner (``attach_output``
/ ``send_input``). ContextVars set by ``ask_user_question_request_scope`` in
the request handler do not propagate into that runner, so the push-based
``ask_user_question`` tool would see an empty ``stream_request_id`` and bail.

This rail runs ``before_tool_call`` *inside* the agent's own execution context
(same context the tool ``invoke`` runs in). For ``ask_user_question`` calls it
re-establishes the four ask ContextVars from the process-level active-request
store that the request handler scope published (keyed by ``id(agent)``).
"""

from __future__ import annotations

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenswarm.agents.harness.common.ask_user_question_registry import (
    AskUserQuestionRegistry,
    _agent_id_cv,
    _channel_id_cv,
    _interactive_ask_cv,
    _service_id_cv,
    _session_id_cv,
    _stream_request_id_cv,
)

_ASK_TOOL_NAMES = frozenset({"ask_user_question"})


class AskUserQuestionContextRail(DeepAgentRail):
    """Re-bind ask request ContextVars inside the agent runner before the tool fires."""

    priority: int = 90

    def __init__(self) -> None:
        super().__init__()

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        tool_name = getattr(ctx.inputs, "tool_name", None)
        if tool_name not in _ASK_TOOL_NAMES:
            return
        agent = ctx.agent
        if agent is None:
            return
        active = AskUserQuestionRegistry.get_instance().get_active_request(agent)
        if active is None:
            return
        _interactive_ask_cv.set(active.interactive_ask)
        _session_id_cv.set(active.session_id)
        _stream_request_id_cv.set(active.stream_request_id)
        _channel_id_cv.set(active.channel_id)
        _service_id_cv.set(active.service_id)
        _agent_id_cv.set(active.agent_id)
