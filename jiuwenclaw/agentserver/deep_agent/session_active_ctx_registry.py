# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Per-session AgentCallbackContext registry for multi-session concurrent invokes."""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext

from jiuwenclaw.agentserver.deep_agent.plan_pause_helpers import (
    resolve_actual_session,
    session_id_from_session,
)

logger = logging.getLogger(__name__)


def session_id_from_callback(ctx: AgentCallbackContext) -> str:
    return session_id_from_session(resolve_actual_session(ctx.session))


class SessionActiveCtxRegistry:
    """Maps session_id → active invoke callback context (one concurrent invoke per session)."""

    def __init__(self) -> None:
        self._by_session: dict[str, AgentCallbackContext] = {}

    def __len__(self) -> int:
        return len(self._by_session)

    def pin(self, ctx: AgentCallbackContext) -> str:
        session_id = session_id_from_callback(ctx)
        if not session_id:
            logger.warning("[SessionActiveCtxRegistry] pin skipped: empty session_id")
            return ""
        previous = self._by_session.get(session_id)
        if previous is not None and previous is not ctx:
            logger.debug(
                "[SessionActiveCtxRegistry] replace pinned ctx session_id=%s",
                session_id,
            )
        self._by_session[session_id] = ctx
        return session_id

    def pop(self, ctx: AgentCallbackContext) -> None:
        session_id = session_id_from_callback(ctx)
        if not session_id:
            return
        if self._by_session.get(session_id) is ctx:
            self._by_session.pop(session_id, None)

    def release(self, session_id: str) -> None:
        if session_id:
            self._by_session.pop(session_id, None)

    def resolve(self, *, session: Any = None) -> AgentCallbackContext | None:
        session_id = session_id_from_session(resolve_actual_session(session))
        if not session_id:
            return None
        return self._by_session.get(session_id)

    def clear(self) -> None:
        self._by_session.clear()
