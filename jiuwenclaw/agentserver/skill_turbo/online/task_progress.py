# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Online task progress —— 对齐批量 skill_turbo 的 task.* 事件协议。

设计见：进度展示与 Schema 优化方案 v1.1 + 前端进度与思考流对齐优化方案 v1.1。
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from jiuwenclaw.agentserver.skill_turbo.online import flow_scheduler

logger = logging.getLogger(__name__)

_SOURCE = "skill_turbo"
_NODE_DISPLAY_CACHE: dict[str, dict[str, str]] = {}


@dataclass
class ProgressEmitTracker:
    """本轮 activate/execute 的 task.* 投递统计（O2/O5）。"""

    ok_count: int = 0
    warn_messages: list[str] = field(default_factory=list)
    mode: str = ""

    def note_ok(self) -> None:
        self.ok_count += 1

    def note_warn(self, message: str) -> None:
        self.warn_messages.append(message)

    @property
    def warning_summary(self) -> str | None:
        if not self.warn_messages:
            return None
        # 去重保序
        seen: set[str] = set()
        unique: list[str] = []
        for msg in self.warn_messages:
            if msg not in seen:
                seen.add(msg)
                unique.append(msg)
        return "; ".join(unique)


_emit_tracker_var: ContextVar[ProgressEmitTracker | None] = ContextVar(
    "online_task_progress_emit_tracker", default=None,
)


@contextmanager
def progress_emit_scope(mode: str = "") -> Iterator[ProgressEmitTracker]:
    """activate/execute 入口包裹：统计 emit_ok_count 与 WARNING。"""
    tracker = ProgressEmitTracker(mode=mode)
    token = _emit_tracker_var.set(tracker)
    try:
        yield tracker
    finally:
        _emit_tracker_var.reset(token)


def get_emit_tracker() -> ProgressEmitTracker | None:
    return _emit_tracker_var.get()


def make_deterministic_task_id(idx: int, plan_name: str) -> str:
    """确定性 task_id，resume 不漂移。"""
    return f"task_{idx:04d}_{plan_name}"


def resolve_display_name(
    plan_name: str,
    schema: dict[str, Any],
    *,
    turbo_dir: str | None = None,
) -> str:
    """展示名：NODE_DISPLAY_NAMES → plan_tasks.title → execution_flow.description → plan_name。"""
    names = _load_node_display_names(turbo_dir)
    if plan_name in names:
        return names[plan_name]

    for task in schema.get("plan_tasks") or []:
        if isinstance(task, dict) and task.get("plan_name") == plan_name:
            title = task.get("title")
            if isinstance(title, str) and title.strip():
                return title.strip()

    for entry in schema.get("execution_flow") or []:
        if isinstance(entry, dict) and entry.get("plan_name") == plan_name:
            desc = entry.get("description")
            if isinstance(desc, str) and desc.strip():
                return desc.strip()

    return plan_name


def _load_node_display_names(turbo_dir: str | None) -> dict[str, str]:
    if not turbo_dir:
        return {}
    key = str(Path(turbo_dir).resolve()) if turbo_dir else ""
    if key in _NODE_DISPLAY_CACHE:
        return _NODE_DISPLAY_CACHE[key]

    result: dict[str, str] = {}
    try:
        turbo_path = Path(turbo_dir)
        # pptx-craft: turbo/turbo_codes_create_ppt/ppt_common.py
        for candidate in turbo_path.glob("turbo_codes_*/ppt_common.py"):
            mapping = _exec_node_display_names(candidate)
            if mapping:
                result.update(mapping)
                break
        if not result:
            # skill_codes 风格：turbo_dir 旁或同级
            sibling = turbo_path / "ppt_common.py"
            if sibling.is_file():
                result.update(_exec_node_display_names(sibling))
    except Exception as exc:
        logger.debug("[OnlineTaskProgress] load NODE_DISPLAY_NAMES failed: %s", exc)

    _NODE_DISPLAY_CACHE[key] = result
    return result


def _exec_node_display_names(path: Path) -> dict[str, str]:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        f"_turbo_ppt_common_{path.stem}", path,
    )
    if spec is None or spec.loader is None:
        return {}
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    names = getattr(module, "NODE_DISPLAY_NAMES", None)
    if isinstance(names, dict):
        return {str(k): str(v) for k, v in names.items()}
    return {}


async def init_progress(
    ctx: Any,
    schema: dict[str, Any],
    parent_session: Any,
    *,
    request_id: str = "",
) -> bool:
    """仅当 task_progress 为空时初始化全量 pending，并发首帧 task.update。

    Returns:
        True 若新建并尝试 emit；False 若已有快照（resume 不重建）。
    """
    existing = getattr(ctx, "task_progress", None) or {}
    if existing:
        return False

    plan_names = flow_scheduler.get_execution_flow_plan_names(schema)
    progress: dict[str, dict[str, Any]] = {}
    for idx, plan_name in enumerate(plan_names):
        progress[plan_name] = {
            "task_id": make_deterministic_task_id(idx, plan_name),
            "task_content": resolve_display_name(
                plan_name, schema, turbo_dir=getattr(ctx, "turbo_dir", None),
            ),
            "task_index": idx,
            "source": _SOURCE,
            "status": "pending",
            "plan_name": plan_name,
            "started_at": None,
            "finished_at": None,
        }
    ctx.task_progress = progress
    await _emit_task_update_async(ctx, parent_session, request_id=request_id)
    logger.info(
        "[OnlineTaskProgress] init_progress tasks=%d",
        len(progress),
    )
    return True


def prepare_resume_progress(ctx: Any) -> None:
    """崩溃/resume：in_progress → pending，避免永久卡住。"""
    progress = getattr(ctx, "task_progress", None) or {}
    for state in progress.values():
        if state.get("status") == "in_progress":
            state["status"] = "pending"
            state["started_at"] = None


async def sync_from_completed(
    ctx: Any,
    schema: dict[str, Any],
    parent_session: Any,
    *,
    request_id: str = "",
) -> bool:
    """将 ctx.completed 中仍为 pending/in_progress 的项标为 completed；有变化才 emit。"""
    progress = getattr(ctx, "task_progress", None) or {}
    if not progress:
        return False
    changed = False
    now = time.time()
    for plan_name in list(getattr(ctx, "completed", set()) or set()):
        state = progress.get(plan_name)
        if state is None:
            continue
        if state.get("status") in ("pending", "in_progress"):
            state["status"] = "completed"
            state["finished_at"] = now
            changed = True
    if changed:
        await _emit_task_update_async(ctx, parent_session, request_id=request_id)
    return changed


async def mark_started(
    ctx: Any,
    plan_name: str,
    parent_session: Any,
    *,
    request_id: str = "",
) -> bool:
    progress = getattr(ctx, "task_progress", None) or {}
    state = progress.get(plan_name)
    if state is None:
        return False
    if state.get("status") == "in_progress":
        return False
    now = time.time()
    state["status"] = "in_progress"
    state["started_at"] = now
    await _emit_task_start_async(ctx, state, parent_session, request_id=request_id)
    await _emit_task_update_async(ctx, parent_session, request_id=request_id)
    return True


async def mark_completed(
    ctx: Any,
    plan_name: str,
    parent_session: Any,
    *,
    failed: bool = False,
    error: str | None = None,
    request_id: str = "",
) -> bool:
    progress = getattr(ctx, "task_progress", None) or {}
    state = progress.get(plan_name)
    if state is None:
        return False
    new_status = "failed" if failed else "completed"
    if state.get("status") == new_status and not failed:
        return False
    now = time.time()
    started = state.get("started_at") or now
    duration_ms = int((now - float(started)) * 1000)
    state["status"] = new_status
    state["finished_at"] = now
    await _emit_task_complete_async(
        state,
        parent_session,
        status=new_status,
        duration_ms=duration_ms,
        error=error if failed else None,
        request_id=request_id,
    )
    await _emit_task_update_async(ctx, parent_session, request_id=request_id)
    return True


async def finalize_progress(
    ctx: Any,
    parent_session: Any,
    *,
    request_id: str = "",
) -> bool:
    """任务结束：未完成项标 failed，发最终 update（不清空）。"""
    progress = getattr(ctx, "task_progress", None) or {}
    if not progress:
        return False
    changed = False
    now = time.time()
    for state in progress.values():
        if state.get("status") in ("pending", "in_progress"):
            state["status"] = "failed"
            state["finished_at"] = now
            changed = True
    if changed:
        await _emit_task_update_async(ctx, parent_session, request_id=request_id)
    return changed


async def compensate_task_update_if_needed(
    ctx: Any,
    parent_session: Any,
    *,
    had_state_change: bool,
    request_id: str = "",
) -> bool:
    """execute 边界补偿：本轮有状态变更但 emit_ok_count==0 时再发一次全量 update。"""
    tracker = get_emit_tracker()
    if not had_state_change:
        return False
    if tracker is not None and tracker.ok_count > 0:
        return False
    progress = getattr(ctx, "task_progress", None) or {}
    if not progress:
        return False
    logger.info(
        "[OnlineTaskProgress] compensate emit task.update (prior emit_ok_count=%s)",
        0 if tracker is None else tracker.ok_count,
    )
    return await _emit_task_update_async(ctx, parent_session, request_id=request_id)


def _public_tasks(ctx: Any) -> list[dict[str, Any]]:
    progress = getattr(ctx, "task_progress", None) or {}
    tasks = []
    for state in sorted(progress.values(), key=lambda s: int(s.get("task_index", 0))):
        tasks.append({
            "task_id": state.get("task_id"),
            "task_content": state.get("task_content"),
            "task_index": state.get("task_index"),
            "source": state.get("source", _SOURCE),
            "status": state.get("status"),
        })
    return tasks


def _stats(tasks: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total_tasks": len(tasks),
        "completed_tasks": sum(1 for t in tasks if t.get("status") == "completed"),
        "in_progress_tasks": sum(1 for t in tasks if t.get("status") == "in_progress"),
        "pending_tasks": sum(1 for t in tasks if t.get("status") == "pending"),
        "failed_tasks": sum(1 for t in tasks if t.get("status") == "failed"),
    }


async def _emit_task_update_async(
    ctx: Any,
    parent_session: Any,
    *,
    request_id: str = "",
) -> bool:
    tasks = _public_tasks(ctx)
    stats = _stats(tasks)
    payload = {
        "event_type": "task.update",
        "tasks": tasks,
        **stats,
        "parent_request_id": request_id or "",
        "timestamp": time.time(),
    }
    return await _write_stream(parent_session, "task.update", payload)


async def _emit_task_start_async(
    ctx: Any,
    state: dict[str, Any],
    parent_session: Any,
    *,
    request_id: str = "",
) -> bool:
    tasks = _public_tasks(ctx)
    payload = {
        "event_type": "task.start",
        "task_id": state.get("task_id"),
        "task_content": state.get("task_content"),
        "task_index": state.get("task_index"),
        "total_tasks": len(tasks),
        "parent_request_id": request_id or "",
        "timestamp": time.time(),
        "source": _SOURCE,
    }
    return await _write_stream(parent_session, "task.start", payload)


async def _emit_task_complete_async(
    state: dict[str, Any],
    parent_session: Any,
    *,
    status: str,
    duration_ms: int,
    error: str | None,
    request_id: str = "",
) -> bool:
    payload: dict[str, Any] = {
        "event_type": "task.complete",
        "task_id": state.get("task_id"),
        "task_content": state.get("task_content"),
        "task_index": state.get("task_index"),
        "status": status,
        "duration_ms": duration_ms,
        "timestamp": time.time(),
    }
    if error:
        payload["error"] = error
    return await _write_stream(parent_session, "task.complete", payload)


async def _write_stream(
    parent_session: Any,
    event_type: str,
    payload: dict[str, Any],
) -> bool:
    tracker = get_emit_tracker()
    mode = tracker.mode if tracker is not None else ""
    parent_none = parent_session is None
    tasks_n = len(payload.get("tasks") or []) if isinstance(payload, dict) else 0
    session_id = ""
    if parent_session is not None:
        getter = getattr(parent_session, "get_session_id", None)
        if callable(getter):
            try:
                session_id = str(getter() or "")
            except Exception:
                session_id = ""

    logger.info(
        "[OnlineTaskProgress] emit type=%s session=%s tasks=%d parent_none=%s mode=%s",
        event_type,
        session_id or "-",
        tasks_n,
        parent_none,
        mode or "-",
    )

    if parent_session is None:
        warn = (
            f"parent_session is None; skip {event_type}"
            + (f" mode={mode}" if mode else "")
        )
        logger.warning("[OnlineTaskProgress] %s", warn)
        if tracker is not None:
            tracker.note_warn(warn)
        return False

    write = getattr(parent_session, "write_stream", None)
    if not callable(write):
        warn = f"parent_session has no write_stream; skip {event_type}"
        logger.warning("[OnlineTaskProgress] %s", warn)
        if tracker is not None:
            tracker.note_warn(warn)
        return False

    try:
        from openjiuwen.core.session.stream.base import OutputSchema

        await write(OutputSchema(type=event_type, index=0, payload=payload))
    except Exception:
        logger.warning(
            "[OnlineTaskProgress] write_stream fail type=%s",
            event_type,
            exc_info=True,
        )
        if tracker is not None:
            tracker.note_warn(f"write_stream fail type={event_type}")
        return False

    # F3：区分真入队 vs emitter 已关闭静默丢弃（writer 不抛错）
    emitter_closed = _probe_emitter_closed(parent_session)
    if emitter_closed:
        logger.warning(
            "[OnlineTaskProgress] emit_discarded type=%s reason=emitter_closed "
            "emitter_closed=true queued=false",
            event_type,
        )
        if tracker is not None:
            tracker.note_warn(f"emit_discarded type={event_type} emitter_closed")
        return False

    logger.info(
        "[OnlineTaskProgress] emit_queued type=%s emitter_closed=false queued=true",
        event_type,
    )
    if tracker is not None:
        tracker.note_ok()
    return True


def _probe_emitter_closed(session: Any) -> bool:
    """探测 session stream emitter 是否已关闭。

    TODO: 优先改为 Session 公共 API（如 is_stream_closed）；首期经 _inner 探测。
    """
    try:
        is_closed = getattr(session, "is_stream_closed", None)
        if callable(is_closed):
            return bool(is_closed())
        inner = getattr(session, "_inner", None)
        if inner is None:
            return False
        mgr = inner.stream_writer_manager()
        emitter = mgr.stream_emitter()
        return bool(emitter.is_closed())
    except Exception:
        return False


def build_task_update_payload(
    ctx: Any,
    *,
    request_id: str = "",
) -> dict[str, Any]:
    """从 TurboContext 构建全量 task.update payload（供 F2 flush 复用）。"""
    tasks = _public_tasks(ctx)
    stats = _stats(tasks)
    return {
        "event_type": "task.update",
        "tasks": tasks,
        **stats,
        "parent_request_id": request_id or "",
        "timestamp": time.time(),
    }


async def flush_task_update_to_session(
    session: Any,
    ctx: Any,
    *,
    request_id: str = "",
) -> bool:
    """将 ContextStore 中的 task_progress 全量 flush 到指定 session。"""
    progress = getattr(ctx, "task_progress", None) or {}
    if not progress:
        return False
    payload = build_task_update_payload(ctx, request_id=request_id)
    return await _write_stream(session, "task.update", payload)


def extract_request_id(request_metadata: dict[str, Any] | None) -> str:
    if not isinstance(request_metadata, dict):
        return ""
    for key in ("request_id", "parent_request_id", "req_id"):
        val = request_metadata.get(key)
        if val:
            return str(val)
    return ""


def extract_channel_id(request_metadata: dict[str, Any] | None) -> str:
    if not isinstance(request_metadata, dict):
        return ""
    for key in ("channel_id", "channel"):
        val = request_metadata.get(key)
        if val:
            return str(val)
    return ""


__all__ = [
    "ProgressEmitTracker",
    "progress_emit_scope",
    "get_emit_tracker",
    "make_deterministic_task_id",
    "resolve_display_name",
    "init_progress",
    "prepare_resume_progress",
    "sync_from_completed",
    "mark_started",
    "mark_completed",
    "finalize_progress",
    "compensate_task_update_if_needed",
    "build_task_update_payload",
    "flush_task_update_to_session",
    "extract_request_id",
    "extract_channel_id",
]
