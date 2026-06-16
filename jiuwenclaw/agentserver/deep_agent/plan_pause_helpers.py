# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Helpers for plan-mode cancel (pause) and LLM-driven resume vs new-task routing."""

from __future__ import annotations

import json
import logging
from typing import Any

from openjiuwen.core.session.checkpointer import CheckpointerFactory
from openjiuwen.core.single_agent import create_agent_session
from openjiuwen.harness.schema.state import DeepAgentState
from openjiuwen.harness.schema.task import TaskStatus as PlanTaskStatus
from openjiuwen.harness.tools.todo import TodoItem, TodoStatus

logger = logging.getLogger(__name__)


def resolve_context_engine(agent: Any) -> Any:
    """Resolve context_engine from DeepAgent or nested react_agent."""
    engine = getattr(agent, "context_engine", None)
    if engine is not None:
        return engine
    react_agent = getattr(agent, "react_agent", None) or getattr(agent, "_react_agent", None)
    if react_agent is not None:
        return getattr(react_agent, "context_engine", None)
    return None


def resolve_actual_session(session: Any) -> Any:
    """Unwrap sub-session to the parent session when present."""
    if session is None:
        return None
    return getattr(session, "_parent", session) or session


def session_id_from_session(session: Any) -> str:
    """Best-effort session id for rails / tools (multi-session safe lookups)."""
    getter = getattr(session, "get_session_id", None)
    if callable(getter):
        return str(getter() or "")
    sid = getattr(session, "session_id", None)
    if callable(sid):
        return str(sid() or "")
    return str(sid or "")


PLAN_PAUSED_SESSION_KEY = "jiuwenclaw_plan_paused"
PLAN_PAUSED_SNAPSHOT_KEY = "jiuwenclaw_plan_paused_snapshot"

PAUSED_PLAN_DECISION_PROMPT_CN = """【系统：plan 暂停后的用户消息】
用户已 cancel；**未完成**待办已从 todo 文件移除（`todo_list` 通常仅含 completed）。取消时的计划状态见下方快照。

请先 `todo_list`，再结合**对话历史**、completed、快照与本条 **content**，**由你自行判断**意图并选用 todo 工具（勿依赖服务端预设结论）：

1. **续跑**：继续暂停前的同一目标 → 按快照用 `todo_create` / `todo_modify` 恢复未完成项（勿无谓整表覆盖 completed），再 `todo_start`；已完成项默认跳过，除非用户要求重做。
2. **全新任务**：与暂停计划无关的新需求 → 只为本条 `todo_create`；**不要**执行快照中的旧目标。
3. **调整原任务**：仍做同一目标但变更范围、约束或步骤 → `todo_modify` 更新计划后再 `todo_start`。
4. **非工作任务**：自然回复即可；**不要** `todo_start` 快照旧步骤，**不要** 为无实质工作内容的 message 建 todo 计划。

不要因刚 cancel 就默认续跑；不要同时推进快照旧目标与本条无关的新目标。
{snapshot_block}"""

PAUSED_PLAN_DECISION_PROMPT_EN = """[System: message after plan pause]
The user cancelled; **unfinished** todos were removed from the todo file (`todo_list` is usually completed-only). See the snapshot below for state at cancel.

Call `todo_list`, then using **chat history**, completed items, the snapshot, and this message's **content**, **decide yourself** the intent and which todo tools to use:

1. **Resume** the same goal: restore unfinished steps from the snapshot via `todo_create` / `todo_modify`, then `todo_start`; keep completed unless redo is needed.
2. **New task**: unrelated to the paused plan — `todo_create` for this content only; do not run old snapshot goals.
3. **Adjust the original task**: same goal but changed scope/constraints/steps — `todo_modify`, then `todo_start`.
4. **Non-work message**: reply naturally; do not `todo_start` old snapshot steps or create a work plan.

Do not assume resume because of a recent cancel; do not run old snapshot goals together with unrelated new content.
{snapshot_block}"""


def _format_snapshot_block(language: str, snapshot: str) -> str:
    text = snapshot.strip()
    if not text:
        return ""
    if language in ("en", "english"):
        return f"\nPlan state at cancel (reference):\n{text}\n"
    return f"\n取消时计划状态（参考）：\n{text}\n"


def build_paused_plan_decision_prompt(language: str, *, snapshot: str = "") -> str:
    snapshot_block = _format_snapshot_block(language, snapshot)
    template = (
        PAUSED_PLAN_DECISION_PROMPT_EN
        if language in ("en", "english")
        else PAUSED_PLAN_DECISION_PROMPT_CN
    )
    return template.format(snapshot_block=snapshot_block)


def build_paused_plan_decision_prompt_from_session_snapshot(
    language: str,
    snapshot: dict[str, Any] | None,
) -> str:
    return build_paused_plan_decision_prompt(
        language,
        snapshot=format_plan_pause_handoff_snapshot(snapshot),
    )


def read_plan_pause_from_session(session: Any) -> tuple[bool, dict[str, Any] | None]:
    """Read persisted plan_paused flag and optional snapshot from session state."""
    paused = session.get_state(PLAN_PAUSED_SESSION_KEY)
    if paused is not True:
        return False, None
    snapshot = session.get_state(PLAN_PAUSED_SNAPSHOT_KEY)
    if isinstance(snapshot, dict):
        return True, snapshot
    return True, None


def write_plan_pause_to_session(
    session: Any,
    *,
    paused: bool,
    snapshot: dict[str, Any] | None = None,
) -> None:
    """Write plan_paused flag (and optional snapshot) into session state."""
    updates: dict[str, Any] = {PLAN_PAUSED_SESSION_KEY: paused}
    if snapshot is not None:
        updates[PLAN_PAUSED_SNAPSHOT_KEY] = snapshot
    elif not paused:
        updates[PLAN_PAUSED_SNAPSHOT_KEY] = None
    session.update_state(updates)


def clear_plan_pause_on_session(session: Any) -> None:
    write_plan_pause_to_session(session, paused=False, snapshot=None)


def _session_id_from_session(session: Any) -> str:
    return session_id_from_session(session)


async def _resolve_session_for_checkpoint(
    instance: Any,
    session_id: str,
    *,
    card: Any,
) -> tuple[Any, bool]:
    """Return (session, owned). owned=True means caller must pre_run/post_run."""
    loop_session = getattr(instance, "loop_session", None)
    if loop_session is not None and _session_id_from_session(loop_session) == session_id:
        return loop_session, False
    session = create_agent_session(session_id=session_id, card=card)
    return session, True


async def persist_checkpoint_for_session(
    instance: Any,
    session_id: str,
    *,
    card: Any,
    session: Any | None = None,
) -> None:
    """Persist context + agent state before abort (mirrors StreamEventRail early checkpoint)."""
    if not session_id or instance is None:
        return

    context_engine = resolve_context_engine(instance)
    if context_engine is None:
        logger.error(
            "[plan_pause] skip pre-abort checkpoint: no context_engine session_id=%s",
            session_id,
        )
        return

    owned = False
    reused_session = session is not None
    if session is None:
        session, owned = await _resolve_session_for_checkpoint(instance, session_id, card=card)
    try:
        if owned:
            await session.pre_run(inputs=None)
        actual_session = getattr(session, "_parent", session) or session
        await context_engine.save_contexts(actual_session)
        await post_agent_execute_for_session(session)
        logger.info(
            "[plan_pause] pre-abort checkpoint saved session_id=%s owned=%s reused_session=%s",
            session_id,
            owned,
            reused_session,
        )
    except Exception as exc:
        logger.error(
            "[plan_pause] pre-abort checkpoint failed session_id=%s: %s",
            session_id,
            exc,
            exc_info=True,
        )
    finally:
        if owned:
            await session.post_run()


async def post_agent_execute_for_session(session: Any) -> None:
    """Flush session state to checkpointer without post_run."""
    actual_session = getattr(session, "_parent", session) or session
    inner = getattr(actual_session, "_inner", actual_session)
    await CheckpointerFactory.get_checkpointer().post_agent_execute(inner)


def format_paused_plan_snapshot(todos: list[TodoItem]) -> str:
    if not todos:
        return ""
    lines: list[str] = []
    for index, todo in enumerate(todos, start=1):
        lines.append(f"{index}. [{todo.status.value}] {todo.content}")
    return "\n".join(lines)


def split_todos_for_pause_handoff(
    todos: list[TodoItem],
) -> tuple[list[TodoItem], list[TodoItem]]:
    """Split unfinished todos from completed (used for optional cancel snapshot)."""
    archived: list[TodoItem] = []
    kept: list[TodoItem] = []
    for todo in todos:
        if todo.status in (TodoStatus.PENDING, TodoStatus.IN_PROGRESS):
            archived.append(todo)
        elif todo.status == TodoStatus.COMPLETED:
            kept.append(todo)
    return archived, kept


def todos_to_snapshot_payload(todos: list[TodoItem]) -> list[dict[str, str]]:
    return [
        {
            "id": todo.id,
            "content": todo.content,
            "status": todo.status.value,
        }
        for todo in todos
    ]


def build_plan_pause_snapshot_payload(
    archived: list[TodoItem],
    kept: list[TodoItem],
) -> dict[str, list[dict[str, str]]]:
    return {
        "completed": todos_to_snapshot_payload(kept),
        "unfinished": todos_to_snapshot_payload(archived),
    }


def format_plan_pause_handoff_snapshot(snapshot: dict[str, Any] | None) -> str:
    if not isinstance(snapshot, dict):
        return ""
    lines: list[str] = []
    completed = snapshot.get("completed")
    if isinstance(completed, list) and completed:
        lines.append("已完成步骤：")
        for index, item in enumerate(completed, start=1):
            if isinstance(item, dict):
                content = str(item.get("content") or "").strip()
                if content:
                    lines.append(f"  {index}. [completed] {content}")
    unfinished = snapshot.get("unfinished")
    if isinstance(unfinished, list) and unfinished:
        lines.append("暂停时未完成步骤：")
        for index, item in enumerate(unfinished, start=1):
            if isinstance(item, dict):
                content = str(item.get("content") or "").strip()
                status = str(item.get("status") or "pending").strip()
                if content:
                    lines.append(f"  {index}. [{status}] {content}")
    return "\n".join(lines)


def repair_task_plan_after_pause(state: DeepAgentState) -> bool:
    """Reset in-progress / abort-cancelled plan steps to pending for resume."""
    plan = state.task_plan
    if plan is None or not plan.tasks:
        return False

    changed = False
    for task in plan.tasks:
        if task.status == PlanTaskStatus.IN_PROGRESS:
            task.status = PlanTaskStatus.PENDING
            changed = True
        elif (
            task.status == PlanTaskStatus.FAILED
            and (task.result_summary or "").strip().lower() == "cancelled"
        ):
            task.status = PlanTaskStatus.PENDING
            task.result_summary = ""
            changed = True

    if plan.current_task_id:
        current = plan.get_task(plan.current_task_id)
        if current is not None and current.status == PlanTaskStatus.PENDING:
            plan.current_task_id = None
            changed = True

    return changed


def clear_task_plan_on_state(state: DeepAgentState) -> bool:
    """Clear task_plan so TaskLoop does not auto-bind a stale task_id on next run."""
    if state.task_plan is None:
        return False
    state.task_plan = None
    return True


def pause_todo_items(todos: list[TodoItem]) -> bool:
    """Mark in_progress todos as pending; return True if any item changed."""
    changed = False
    for todo in todos:
        if todo.status == TodoStatus.IN_PROGRESS:
            todo.status = TodoStatus.PENDING
            changed = True
    return changed


async def pause_pending_todos_on_tool(modify_tool: Any, session_id: str) -> bool:
    """Persist in_progress -> pending on the session todo file."""
    file_path = modify_tool.file_path_for_session(session_id)
    todos = await modify_tool.load_todos(file_path)
    if not todos:
        return False
    if not pause_todo_items(todos):
        return False
    await modify_tool.save_todos(todos, file_path)
    return True


async def snapshot_and_isolate_unfinished_todos(modify_tool: Any, session_id: str) -> dict[str, Any] | None:
    """Snapshot cancel state, then remove unfinished todos from file (keep completed only).

    Prevents todo_list from exposing runnable pending items on the next turn, so a
    clearly new user message cannot parallelize with old plan steps via todo_start.
    """
    file_path = modify_tool.file_path_for_session(session_id)
    todos = await modify_tool.load_todos(file_path)
    if not todos:
        return None

    pause_todo_items(todos)
    archived, kept = split_todos_for_pause_handoff(todos)
    snapshot = build_plan_pause_snapshot_payload(archived, kept)

    ids_to_delete: list[str] = []
    seen: set[str] = set()
    for todo in archived:
        if todo.id not in seen:
            seen.add(todo.id)
            ids_to_delete.append(todo.id)
    for todo in todos:
        if todo.status == TodoStatus.CANCELLED and todo.id not in seen:
            seen.add(todo.id)
            ids_to_delete.append(todo.id)

    if ids_to_delete:
        await delete_todos_via_modify_tool(modify_tool, ids_to_delete, session_id=session_id)

    return snapshot


async def delete_todos_via_modify_tool(modify_tool: Any, ids: list[str], *, session_id: str) -> None:
    """Delete todos through TodoModifyTool public invoke API."""
    if not ids:
        return
    await modify_tool.invoke({"action": "delete", "ids": ids}, session_id=session_id)


async def cancel_todos_via_modify_tool(modify_tool: Any, ids: list[str], *, session_id: str) -> None:
    """Cancel todos through TodoModifyTool public invoke API."""
    if not ids:
        return
    await modify_tool.invoke({"action": "cancel", "ids": ids}, session_id=session_id)


async def cancel_pending_todos_on_tool(modify_tool: Any, session_id: str) -> bool:
    """Mark all non-completed todos cancelled (isolate old plan for a new-task turn)."""
    file_path = modify_tool.file_path_for_session(session_id)
    todos = await modify_tool.load_todos(file_path)
    if not todos:
        return False

    done_statuses = {
        TodoStatus.COMPLETED.value,
        TodoStatus.CANCELLED.value,
    }
    ids_to_cancel = [todo.id for todo in todos if todo.status.value not in done_statuses]
    if not ids_to_cancel:
        return False

    await cancel_todos_via_modify_tool(modify_tool, ids_to_cancel, session_id=session_id)
    return True


def merge_supplementary_into_request_params(
    params: dict[str, Any],
    supplementary: str,
) -> None:
    """Append system supplementary text to request.params for build_user_prompt."""
    if not supplementary.strip():
        return
    existing = params.get("supplementary_info")
    if isinstance(existing, str) and existing.strip():
        params["supplementary_info"] = f"{existing.strip()}\n\n{supplementary.strip()}"
    else:
        params["supplementary_info"] = supplementary.strip()


def append_supplementary_to_inputs_query(inputs: dict[str, Any], supplementary: str) -> None:
    """Fallback: inject supplementary into an already-built user prompt JSON payload."""
    if not supplementary.strip():
        return
    query = inputs.get("query")
    if not isinstance(query, str):
        return
    prefixes = ("你收到一条消息：\n", "You receive a new message:\n")
    for prefix in prefixes:
        if not query.startswith(prefix):
            continue
        try:
            payload = json.loads(query[len(prefix):])
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        existing = payload.get("supplementary_info")
        if isinstance(existing, str) and existing.strip():
            payload["supplementary_info"] = f"{existing.strip()}\n\n{supplementary.strip()}"
        else:
            payload["supplementary_info"] = supplementary.strip()
        inputs["query"] = prefix + json.dumps(payload, ensure_ascii=False)
        return
