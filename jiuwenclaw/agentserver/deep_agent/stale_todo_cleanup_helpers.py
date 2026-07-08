# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Cancel orphaned active todos before a fresh (non-resume) user turn."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.single_agent import create_agent_session
from openjiuwen.harness.tools.todo_resume import has_active_todo_items, is_resume_user_query

from jiuwenclaw.agentserver.deep_agent.interrupt_resume_helpers import (
    load_session_todo_items,
    set_todo_resume_snapshot_pending,
)
from jiuwenclaw.agentserver.deep_agent.plan_pause_helpers import (
    cancel_pending_todos_on_tool,
    clear_skip_invoke_task_update_sync,
    is_interrupt_recovery_injected,
    mark_skip_invoke_task_update_sync,
    post_agent_execute_for_session,
)

logger = logging.getLogger(__name__)


def should_cancel_stale_active_todos(request: Any, params: dict[str, Any]) -> bool:
    """Return True when this turn should isolate prior active todos on disk.

    Skips heartbeat, non-plan modes, supplement turns, structured replies, and
    explicit resume phrases (继续/接着做). Aligns with skill_prompt: a new user
    message replaces the prior task unless the user explicitly continues.
    """
    session_id = str(getattr(request, "session_id", "") or "")
    if session_id.startswith("heartbeat"):
        return False

    mode = str(params.get("mode", "agent.plan") or "agent.plan").strip()
    if mode != "agent.plan":
        return False

    if params.get("is_supplement"):
        return False

    query = params.get("query")
    if isinstance(query, InteractiveInput):
        return False

    if params.get("answers"):
        return False

    if is_resume_user_query(str(query or "")):
        return False

    return True


def _serialize_todos_for_log(todos: list[Any]) -> str:
    payload: list[dict[str, Any]] = []
    for todo in todos:
        status = todo.status
        status_value = status.value if hasattr(status, "value") else str(status)
        payload.append(
            {
                "id": getattr(todo, "id", ""),
                "content": getattr(todo, "content", ""),
                "activeForm": getattr(todo, "activeForm", ""),
                "status": status_value,
            }
        )
    return json.dumps(payload, ensure_ascii=False) if payload else "[]"


async def prepare_stale_todo_cleanup_for_request(
    request: Any,
    *,
    agent_card: Any,
    get_todo_modify_tool: Callable[[str], Any],
) -> bool:
    """Cancel active todos before a fresh user turn; log todo.json and set skip flag."""
    if agent_card is None:
        return False

    session_id = str(getattr(request, "session_id", "") or "").strip()
    if not session_id:
        return False

    params = request.params if isinstance(getattr(request, "params", None), dict) else None
    if params is None:
        return False

    session = create_agent_session(session_id=session_id, card=agent_card)
    await session.pre_run(inputs=None)
    try:
        # Drop skip flag left when a prior turn crashed after cleanup but before
        # TaskExecutionRail.after_invoke could clear it (e.g. resume/supplement).
        clear_skip_invoke_task_update_sync(session)

        if not should_cancel_stale_active_todos(request, params):
            await post_agent_execute_for_session(session)
            return False

        modify_tool = get_todo_modify_tool(session_id)
        if modify_tool is None:
            await post_agent_execute_for_session(session)
            return False

        if is_interrupt_recovery_injected(session):
            await post_agent_execute_for_session(session)
            return False

        todos = await load_session_todo_items(modify_tool, session_id)
        if not has_active_todo_items(todos):
            await post_agent_execute_for_session(session)
            return False

        todo_json_str = _serialize_todos_for_log(todos)
        logger.info(
            "[JiuWenClaw] 因旧任务已停止、新消息不是续跑，取消上一轮的 active todo; "
            "session_id=%s request_id=%s todo.json=%s",
            session_id,
            getattr(request, "request_id", ""),
            todo_json_str,
        )

        cancelled = await cancel_pending_todos_on_tool(modify_tool, session_id)
        if not cancelled:
            await post_agent_execute_for_session(session)
            return False

        set_todo_resume_snapshot_pending(session, pending=False)
        mark_skip_invoke_task_update_sync(session)
        await post_agent_execute_for_session(session)
        return True
    except Exception as exc:
        logger.warning(
            "[JiuWenClawDeepAdapter] prepare_stale_todo_cleanup_for_request failed "
            "session_id=%s: %s",
            session_id,
            exc,
        )
        return False
    finally:
        await session.post_run()
