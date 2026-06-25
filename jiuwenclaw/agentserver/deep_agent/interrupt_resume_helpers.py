# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Helpers for connection-interrupt resume (distinct from plan-mode cancel pause)."""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen.core.single_agent import create_agent_session
from openjiuwen.harness.tools.todo import TodoModifyTool, TodoItem
from openjiuwen.harness.tools.todo_resume import (
    TODO_RESUME_SNAPSHOT_PENDING_KEY,
    build_interrupt_resume_decision_prompt,
    format_todo_snapshot_lines,
    has_active_todo_items,
    is_resume_user_query,
)

from jiuwenclaw.agentserver.deep_agent.plan_pause_helpers import (
    is_interrupt_recovery_injected,
    mark_interrupt_recovery_injected,
    merge_supplementary_into_request_params,
    post_agent_execute_for_session,
    read_plan_pause_from_session,
)

logger = logging.getLogger(__name__)


def set_todo_resume_snapshot_pending(session: Any, *, pending: bool) -> None:
    session.update_state({TODO_RESUME_SNAPSHOT_PENDING_KEY: bool(pending)})


async def load_session_todo_items(
    modify_tool: TodoModifyTool,
    session_id: str,
) -> list[TodoItem]:
    file_path = modify_tool.file_path_for_session(session_id)
    try:
        todos = await modify_tool.load_todos(file_path)
        return todos if isinstance(todos, list) else []
    except Exception as exc:
        logger.debug(
            "[interrupt_resume] load todos failed session_id=%s: %s",
            session_id,
            exc,
        )
        return []


async def prepare_interrupt_resume_for_request(
    adapter: Any,
    request: Any,
) -> None:
    """Before agent.plan run: inject resume guidance when user continues an interrupted task."""
    instance = getattr(adapter, "_instance", None)
    if instance is None:
        return

    session_id = str(getattr(request, "session_id", "") or "").strip()
    if not session_id:
        return

    params = request.params if isinstance(getattr(request, "params", None), dict) else None
    if params is None:
        return

    mode = str(params.get("mode", "agent.plan") or "agent.plan").strip()
    if mode != "agent.plan":
        return

    query = str(params.get("query", "") or "")
    if not is_resume_user_query(query):
        return

    get_modify_tool = getattr(adapter, "_get_todo_modify_tool", None)
    if not callable(get_modify_tool):
        return

    modify_tool = get_modify_tool(session_id)
    if modify_tool is None:
        return

    todos = await load_session_todo_items(modify_tool, session_id)
    if not has_active_todo_items(todos):
        return

    resolve_language = getattr(adapter, "_resolve_runtime_language", None)
    language = resolve_language() if callable(resolve_language) else "cn"

    session = create_agent_session(session_id=session_id, card=instance.card)
    await session.pre_run(inputs=None)
    try:
        # 哨兵：已有其他恢复机制注入，跳过
        if is_interrupt_recovery_injected(session):
            return

        paused, _snapshot = read_plan_pause_from_session(session)
        if paused:
            return

        snapshot_text = format_todo_snapshot_lines(todos)
        decision = build_interrupt_resume_decision_prompt(
            language,
            snapshot=snapshot_text,
        )
        merge_supplementary_into_request_params(params, decision)
        set_todo_resume_snapshot_pending(session, pending=True)
        mark_interrupt_recovery_injected(session)
        await post_agent_execute_for_session(session)

        logger.info(
            "[JiuWenClawDeepAdapter] interrupt-resume decision prompt injected session=%s tasks=%d",
            session_id,
            len(todos),
        )
    except Exception as exc:
        logger.warning(
            "[JiuWenClawDeepAdapter] prepare_interrupt_resume_for_request failed session_id=%s: %s",
            session_id,
            exc,
        )
    finally:
        await session.post_run()
