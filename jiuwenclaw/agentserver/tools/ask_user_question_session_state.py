# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Session flags for ask_user_question text_only pause / resume."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

SESSION_AWAITING_TEXT_ONLY_ASK_KEY = "jiuwenclaw_awaiting_text_only_ask_reply"
SESSION_TEXT_ONLY_RESUME_QUERY_KEY = "jiuwenclaw_text_only_resume_user_query"


def _session_label(session: Any) -> str:
    getter = getattr(session, "get_session_id", None)
    if callable(getter):
        return str(getter() or "")
    return str(getattr(session, "session_id", "") or "")


def session_awaiting_text_only_ask_reply(session: Any) -> bool:
    """Return True when the session is waiting for a plain-text ask_user reply."""
    try:
        return bool(session.get_state(SESSION_AWAITING_TEXT_ONLY_ASK_KEY))
    except Exception:
        return False


def mark_session_awaiting_text_only_ask_reply(session: Any) -> None:
    """Record that the next plain user message should resume a text_only ask."""
    session.update_state({SESSION_AWAITING_TEXT_ONLY_ASK_KEY: True})
    logger.info(
        "[ask_user_question] marked session awaiting text_only reply session_id=%s",
        _session_label(session),
    )


def consume_session_awaiting_text_only_ask_reply(session: Any) -> bool:
    """Clear the awaiting flag and return whether it was set."""
    was_awaiting = session_awaiting_text_only_ask_reply(session)
    session.update_state({SESSION_AWAITING_TEXT_ONLY_ASK_KEY: False})
    if was_awaiting:
        logger.info(
            "[ask_user_question] consumed awaiting text_only reply session_id=%s",
            _session_label(session),
        )
    return was_awaiting


def store_text_only_resume_user_query(session: Any, user_query: str) -> None:
    """Persist the user's plain-text reply for the next task iteration."""
    query = str(user_query or "").strip()
    if not query:
        return
    session.update_state({SESSION_TEXT_ONLY_RESUME_QUERY_KEY: query})
    logger.info(
        "[ask_user_question] stored text_only resume user query session_id=%s query_len=%d",
        _session_label(session),
        len(query),
    )


def pop_text_only_resume_user_query(session: Any) -> str | None:
    """Return and clear the stored resume query, if any."""
    try:
        raw = session.get_state(SESSION_TEXT_ONLY_RESUME_QUERY_KEY)
    except Exception:
        return None
    session.update_state({SESSION_TEXT_ONLY_RESUME_QUERY_KEY: None})
    query = str(raw or "").strip()
    if not query:
        return None
    logger.info(
        "[ask_user_question] applying text_only resume user query session_id=%s query_len=%d",
        _session_label(session),
        len(query),
    )
    return query


def clear_pending_follow_ups(deep_agent: Any, session: Any) -> list[str]:
    """Clear DeepAgent pending_follow_ups; return the removed messages."""
    state = deep_agent.load_state(session)
    cleared = list(state.pending_follow_ups)
    if not cleared:
        return []
    state.pending_follow_ups = []
    deep_agent.save_state(session, state)
    logger.info(
        "[ask_user_question] cleared pending_follow_ups for text_only resume: %s",
        cleared,
    )
    return cleared
