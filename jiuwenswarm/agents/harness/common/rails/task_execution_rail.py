# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""TaskExecutionRail — Emit task.start/task.complete/task.update lifecycle events.

Tracks todo status transitions (pending->in_progress->completed) and emits
lifecycle events to the frontend. Binds the current task_id via ContextVar
so downstream tool/artifact events can be attributed to the active task.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    InvokeInputs,
    ToolCallInputs,
)
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.workspace.workspace import WorkspaceNode

from jiuwenswarm.common.utils import logger

_ACTIVE_TASK_ID: ContextVar[str | None] = ContextVar(
    "active_task_id", default=None
)


def get_current_task_id() -> str | None:
    """Return current task id for stream payload correlation."""
    return _ACTIVE_TASK_ID.get()


# 图像产物扩展名白名单
_IMAGE_ARTIFACT_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
})

# 文件路径检测的正则表达式模式（仿 PR#1440；调用方按扩展名白名单过滤）
_FILE_PATH_PATTERNS = [
    # Windows绝对路径 (D:\path, D:/path)
    re.compile(r'[A-Za-z]:[/\\][^\s\]\}\)\,\'\"`<>，。；、：]+'),
    # Unix绝对路径 (/path/to/file)
    re.compile(r'/[^\s\]\}\)\,\'\"`<>，。；、：]+'),
    # 相对路径带有扩展名 (./path, path/file.ext)
    re.compile(
        r'(?<![/\\])(?:\.{1,2}[/\\])?(?:[^\s\]\}\)\,\'\"`<>，。；、：]+[/\\])+'
        r'[^\s\]\}\)\,\'\"`<>，。；、：]+\.[a-zA-Z0-9]{1,10}'
    ),
]

_PATH_TRAILING_CHARS = "'\"`\\]\\}\\),.;:，。；、："
_PYTHON_SCRIPT_EXTENSIONS = frozenset({".py", ".pyw"})


def _clean_path_candidate(path_str: str) -> str:
    """清理正则提取到的路径候选首尾非法字符。"""
    return path_str.strip().strip(_PATH_TRAILING_CHARS).strip()


def _parse_tool_args_payload(tool_args: Any) -> dict[str, Any]:
    if tool_args is None:
        return {}
    payload: Any = tool_args
    if isinstance(tool_args, str):
        try:
            payload = json.loads(tool_args)
        except (TypeError, ValueError):
            return {}
    if isinstance(payload, dict):
        return payload
    return {}


def _tool_result_to_text(tool_result: Any) -> str:
    if tool_result is None:
        return ""
    if isinstance(tool_result, str):
        return tool_result
    if isinstance(tool_result, dict):
        return json.dumps(tool_result, ensure_ascii=False)
    if hasattr(tool_result, "__dict__"):
        return str(tool_result)
    return str(tool_result)


def _extract_raw_paths_from_result_text(tool_result: Any) -> list[str]:
    """从工具输出结果中正则提取路径候选（不按扩展名过滤）。"""
    result_text = _tool_result_to_text(tool_result)
    if not result_text:
        return []

    seen: set[str] = set()
    paths: list[str] = []
    for pattern in _FILE_PATH_PATTERNS:
        for match in pattern.findall(result_text):
            cleaned = _clean_path_candidate(match)
            if not cleaned:
                continue
            identity = cleaned.replace("\\", "/").lower()
            if identity in seen:
                continue
            seen.add(identity)
            paths.append(cleaned)
    return paths


def _extract_file_paths_from_write_tool(
    tool_name: str,
    tool_args: Any,
    tool_result: Any,
) -> list[str]:
    """从 write/edit 类工具参数或结果中提取产物路径。"""
    paths: list[str] = []
    payload = _parse_tool_args_payload(tool_args)
    for key in ("path", "file_path", "target_file", "abs_file_path"):
        value = str(payload.get(key) or "").strip()
        if value:
            paths.append(value)

    if paths:
        return list(dict.fromkeys(paths))

    if tool_name in {"write_file", "edit_file", "write", "write_text_file"}:
        for candidate in _extract_raw_paths_from_result_text(tool_result):
            if Path(candidate).suffix.lower() in _PYTHON_SCRIPT_EXTENSIONS:
                paths.append(candidate)
    return list(dict.fromkeys(paths))


def _extract_image_paths_from_tool_result(tool_result: Any) -> list[str]:
    """从工具输出结果中提取图像产物路径。

    仿 PR#1440 的正则提取思路，范围限定为图像扩展名白名单。
    处理字符串、字典、对象三类结果。
    """
    return [
        path
        for path in _extract_raw_paths_from_result_text(tool_result)
        if Path(path).suffix.lower() in _IMAGE_ARTIFACT_EXTENSIONS
    ]


@dataclass
class TaskExecutionContext:
    task_id: str
    task_content: str
    task_index: int
    total_tasks: int
    parent_request_id: str
    start_time: float
    source: Literal["todo"]
    status: Literal["running", "succeeded", "failed", "skipped"] = "running"


class TaskExecutionRail(DeepAgentRail):
    """Emit task.start/task.complete/task.update around todo execution transitions.

    TODO_TOOLS (todo_create, todo_modify, todo_list, todo_get) trigger todo
    state change detection via _sync_todo_and_emit_transitions. Non-todo tools
    bind the current in-progress todo task via the _ACTIVE_TASK_ID ContextVar.
    """

    _BINDING_IN_PROGRESS = frozenset({"in_progress"})
    _BINDING_PENDING = frozenset({"pending", "waiting"})
    _TODO_DONE_STATUSES = frozenset({"completed", "cancelled"})

    priority = 85
    # Do NOT inherit this rail into a general-purpose subagent. init() binds
    # ``self._deep_agent`` to the agent handed in, and the subagent's
    # _ensure_initialized re-runs init_rail with the *child* — rebinding the
    # shared instance to the subagent forever (the parent never re-inits).
    # After that _get_todo_workspace_path resolves todo.json under the child's
    # empty sub_agents workspace, _load_todo_from_json returns [], the todo map
    # stays empty, and no pending->in_progress transition is ever detected again
    # — so every later parent stage's task.start stops firing (e.g. PPT stage4+
    # missing from history.json after a general-purpose subagent ran in stage3).
    inherit_to_subagents = False

    TODO_TOOLS = frozenset({
        "todo_create", "todo_get", "todo_list", "todo_modify",
    })
    SKILL_COMPLETE_TOOLS = frozenset({"skill_complete"})

    # 触发图像产物后处理 hook 的工具
    IMAGE_TOOLS = frozenset({"generate_image"})
    FILE_ARTIFACT_TOOLS = frozenset({
        "write_file",
        "edit_file",
        "write",
        "write_text_file",
    })

    def __init__(self) -> None:
        super().__init__()
        self._todo_map: dict[str, dict[str, Any]] = {}
        self._todo_map_before_tool: dict[str, dict[str, Any]] = {}
        self._active_tasks: dict[str, TaskExecutionContext] = {}
        self._todo_started: set[str] = set()
        self._deep_agent: Any | None = None

    def get_current_task_id(self) -> str | None:
        return _ACTIVE_TASK_ID.get()

    def init(self, agent: Any) -> None:
        self._deep_agent = agent

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        session_id = ""
        if ctx.session is not None:
            try:
                session_id = str(ctx.session.get_session_id() or "")
            except Exception:
                logger.debug(
                    "[TaskExecutionRail] before_invoke: "
                    "failed to get session_id",
                    exc_info=True,
                )
        logger.info(
            "[TaskExecutionRail] before_invoke reset tracking: "
            "session_id=%s prev_todo_map_size=%d prev_active_tasks=%s",
            session_id,
            len(self._todo_map),
            list(self._active_tasks.keys()),
        )
        self._todo_map = {}
        self._todo_map_before_tool = {}
        self._active_tasks = {}
        self._todo_started = set()
        _ACTIVE_TASK_ID.set(None)
        if isinstance(ctx.inputs, InvokeInputs):
            await self._init_task_tracking(ctx.session)
            has_active_tasks = any(
                t.get("status") in ("pending", "in_progress")
                for t in self._todo_map.values()
            )
            if has_active_tasks:
                parent_request_id = self._extract_request_id(ctx)
                await self._emit_task_update_event(
                    ctx.session, parent_request_id
                )
        self._bind_context_to_in_progress_task()

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Bind task_id before LLM calls."""
        self._bind_context_to_in_progress_task()

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        if not isinstance(ctx.inputs, ToolCallInputs):
            return
        tool_name = ctx.inputs.tool_name

        if tool_name in self.TODO_TOOLS:
            session_id = ""
            if ctx.session is not None:
                try:
                    session_id = str(
                        ctx.session.get_session_id() or ""
                    )
                except Exception:
                    logger.debug(
                        "[TaskExecutionRail] before_tool_call: "
                        "failed to get session_id",
                        exc_info=True,
                    )
            logger.info(
                "[TaskExecutionRail] todo snapshot before_tool: "
                "session=%s todo_map_size=%d active_tasks=%s",
                session_id,
                len(self._todo_map),
                list(self._active_tasks.keys()),
            )
            self._todo_map_before_tool = dict(self._todo_map)
            return

        if tool_name in self.SKILL_COMPLETE_TOOLS:
            if self._has_incomplete_todos(self._todo_map):
                if self._skill_complete_auto_flush_enabled():
                    try:
                        await self._flush_incomplete_todos_on_skill_complete(ctx)
                    except Exception as exc:
                        logger.warning(
                            "[TaskExecutionRail] skill_complete "
                            "auto-flush failed, falling back to block: %s",
                            exc,
                            exc_info=True,
                        )
                        self._apply_skill_complete_block(ctx)
                else:
                    self._apply_skill_complete_block(ctx)
            return

        self._bind_context_to_in_progress_task()

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        if not isinstance(ctx.inputs, ToolCallInputs):
            return
        tool_name = ctx.inputs.tool_name

        if tool_name in self.TODO_TOOLS:
            await self._sync_todo_and_emit_transitions(ctx)
            return

        if tool_name in self.IMAGE_TOOLS:
            await self._trigger_image_artifact_hook(ctx)
            return

        if tool_name in self.FILE_ARTIFACT_TOOLS:
            await self._trigger_artifact_post_process_hook(ctx)
            return

    async def _trigger_image_artifact_hook(
        self, ctx: AgentCallbackContext
    ) -> None:
        """图像产物落盘后触发 IMAGE_ARTIFACT_POST_PROCESS 扩展 hook。

        从 tool_result 解析图像路径，构建 ImageArtifactHookContext 并触发
        扩展回调；扩展可在 handler 中对文件做原地后处理（如加水印）。
        ExtensionRegistry 未初始化或扩展抛错时仅记 warning，不阻断主流程。
        """
        session = ctx.session
        if session is None:
            return
        try:
            session_id = session.get_session_id()
        except Exception:
            logger.debug(
                "[TaskExecutionRail] image artifact hook: "
                "failed to get session_id",
                exc_info=True,
            )
            return

        tool_result = getattr(ctx.inputs, "tool_result", None)
        image_paths = _extract_image_paths_from_tool_result(tool_result)
        if not image_paths:
            return

        task_id = _ACTIVE_TASK_ID.get()
        tool_name = ctx.inputs.tool_name

        try:
            from jiuwenswarm.extensions.registry import ExtensionRegistry
            from jiuwenswarm.extensions.hook_event import (
                AgentServerHookEvents,
            )
            from jiuwenswarm.extensions.hooks_context import (
                ImageArtifactHookContext,
            )
        except ImportError as exc:
            logger.warning(
                "[TaskExecutionRail] skip image artifact hook, "
                "import failed: %s",
                exc,
            )
            return

        hook_ctx = ImageArtifactHookContext(
            session_id=session_id,
            tool_name=tool_name,
            task_id=task_id,
            artifact_paths=image_paths,
        )
        try:
            await ExtensionRegistry.get_instance().trigger(
                AgentServerHookEvents.IMAGE_ARTIFACT_POST_PROCESS,
                hook_ctx,
            )
        except RuntimeError:
            logger.warning(
                "[TaskExecutionRail] skip image artifact hook: "
                "ExtensionRegistry not initialized",
            )
            return
        except Exception as exc:
            logger.warning(
                "[TaskExecutionRail] image artifact hook failed "
                "session_id=%s tool=%s error=%s",
                session_id,
                tool_name,
                exc,
            )
            return

        logger.info(
            "[TaskExecutionRail] image artifact hook done "
            "session_id=%s tool=%s count=%d",
            session_id,
            tool_name,
            len(image_paths),
        )

    async def _trigger_artifact_post_process_hook(
        self, ctx: AgentCallbackContext
    ) -> None:
        """文件产物落盘后触发 ARTIFACT_POST_PROCESS 扩展 hook。"""
        session = ctx.session
        if session is None:
            return
        try:
            session_id = session.get_session_id()
        except Exception:
            logger.debug(
                "[TaskExecutionRail] artifact post-process hook: "
                "failed to get session_id",
                exc_info=True,
            )
            return

        tool_args = getattr(ctx.inputs, "tool_args", None)
        tool_result = getattr(ctx.inputs, "tool_result", None)
        artifact_paths = _extract_file_paths_from_write_tool(
            ctx.inputs.tool_name,
            tool_args,
            tool_result,
        )
        if not artifact_paths:
            return

        task_id = _ACTIVE_TASK_ID.get()
        tool_name = ctx.inputs.tool_name

        try:
            from jiuwenswarm.extensions.registry import ExtensionRegistry
            from jiuwenswarm.extensions.hook_event import (
                AgentServerHookEvents,
            )
            from jiuwenswarm.extensions.hooks_context import (
                ArtifactPostProcessHookContext,
            )
        except ImportError as exc:
            logger.warning(
                "[TaskExecutionRail] skip artifact post-process hook, "
                "import failed: %s",
                exc,
            )
            return

        hook_ctx = ArtifactPostProcessHookContext(
            session_id=session_id,
            tool_name=tool_name,
            task_id=task_id,
            artifact_paths=artifact_paths,
        )
        try:
            await ExtensionRegistry.get_instance().trigger(
                AgentServerHookEvents.ARTIFACT_POST_PROCESS,
                hook_ctx,
            )
        except RuntimeError:
            logger.warning(
                "[TaskExecutionRail] skip artifact post-process hook: "
                "ExtensionRegistry not initialized",
            )
            return
        except Exception as exc:
            logger.warning(
                "[TaskExecutionRail] artifact post-process hook failed "
                "session_id=%s tool=%s error=%s",
                session_id,
                tool_name,
                exc,
            )
            return

        logger.info(
            "[TaskExecutionRail] artifact post-process hook done "
            "session_id=%s tool=%s count=%d",
            session_id,
            tool_name,
            len(artifact_paths),
        )

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        self._todo_map_before_tool = {}
        self._bind_context_to_in_progress_task()

    # ------------------------------------------------------------------
    # Task list state loading
    # ------------------------------------------------------------------

    async def _init_task_tracking(
        self, session: Session | None
    ) -> None:
        if session is None:
            return
        session_id = session.get_session_id()
        try:
            todo_items = self._load_todo_from_json(session_id)
            if todo_items:
                self._todo_map = self._build_map_from_todo_items(
                    todo_items
                )
                logger.info(
                    "[TaskExecutionRail] Loaded todo.json "
                    "session_id=%s tasks=%d",
                    session_id,
                    len(todo_items),
                )
        except Exception as exc:
            logger.debug(
                "[TaskExecutionRail] Failed to load todo.json: %s",
                exc,
            )

    def _load_todo_from_json(
        self, session_id: str
    ) -> list[dict[str, Any]]:
        todo_path = self._get_todo_workspace_path(session_id)
        if todo_path is None or not todo_path.exists():
            return []
        with open(todo_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []

    def _get_todo_workspace_path(
        self, session_id: str
    ) -> Path | None:
        """Resolve todo.json path from the deep agent's workspace config."""
        da = self._deep_agent
        if da is None:
            return None
        try:
            deep_config = da.deep_config
            workspace_path = Path(
                deep_config.workspace.get_node_path(WorkspaceNode.TODO)
            )
            return workspace_path / session_id / "todo.json"
        except Exception as exc:
            logger.debug(
                "[TaskExecutionRail] Failed to resolve todo "
                "workspace path: %s",
                exc,
            )
            return None

    def _build_map_from_todo_items(
        self, items: list[dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        mapped: dict[str, dict[str, Any]] = {}
        total = len(items)
        for index, item in enumerate(items):
            task_id = item.get("id", str(index))
            status = item.get("status", "pending")
            if isinstance(status, str):
                normalized_status = status.lower()
            else:
                normalized_status = str(status).lower()
            mapped[task_id] = {
                "content": item.get(
                    "content", item.get("activeForm", "")
                ),
                "status": normalized_status,
                "index": index,
                "total": total,
            }
        return mapped

    @staticmethod
    def _has_incomplete_todos(
        todo_map: dict[str, dict[str, Any]]
    ) -> bool:
        if not todo_map:
            return False
        return any(
            str(task.get("status", "pending")).lower()
            not in TaskExecutionRail._TODO_DONE_STATUSES
            for task in todo_map.values()
        )

    # ------------------------------------------------------------------
    # skill_complete auto-flush
    # ------------------------------------------------------------------

    def _apply_skill_complete_block(self, ctx: AgentCallbackContext) -> None:
        """Legacy fallback: block skill_complete when todos are incomplete.

        Sends the model a [SKILL_COMPLETE_BLOCKED] message asking it to call
        todo_modify first. Preserved verbatim so auto-flush failures or the
        disabled-flag path behave exactly like today.
        """
        tc = ctx.inputs.tool_call
        tool_call_id = str(getattr(tc, "id", "") or "")
        msg = (
            "[SKILL_COMPLETE_BLOCKED] todo.json 中仍有未完成任务，"
            "请先用 todo_modify 将全部已完成项标为 completed。"
        )
        ctx.extra["_skip_tool"] = True
        ctx.inputs.tool_result = msg
        ctx.inputs.tool_msg = ToolMessage(
            content=msg, tool_call_id=tool_call_id,
        )

    @staticmethod
    def _skill_complete_auto_flush_enabled() -> bool:
        """Whether skill_complete auto-flushes incomplete todos instead of
        bouncing to the model.

        Env var ``SKILL_COMPLETE_AUTO_FLUSH`` (default "1" / enabled). Set to
        "0"/"false"/"no"/"off" to revert to the legacy block behavior. Reading
        an env var (rather than threading config through interface_deep.py)
        keeps this change local to a single file and matches the repo's env
        convention (TODO_PROGRESS_REPEAT, JIUWENCLAW_EARLY_CHECKPOINT).
        """
        raw = os.environ.get("SKILL_COMPLETE_AUTO_FLUSH", "1")
        return str(raw).strip().lower() not in ("0", "false", "no", "off")

    def _persist_todo_statuses(
        self,
        session_id: str,
        overrides: dict[str, str],
    ) -> int:
        """Atomically update item statuses in the persisted task-list file.

        Only the ``status`` field of items whose id appears in ``overrides``
        is changed; all other fields and all other items are preserved. The
        write is atomic (tmp file + os.replace) so a crash cannot leave a
        half-written file. id resolution matches
        _build_map_from_todo_items: item.get("id", str(index)).

        Returns the number of overrides that matched a task item. Raises
        RuntimeError when the file is missing/not a JSON list, or when fewer
        overrides matched than requested (stale in-memory task map vs. the
        on-disk file). Callers MUST treat a raise as "the flush did not fully
        land" and fail closed (fall back to _apply_skill_complete_block)
        instead of letting skill_complete through on an unverified state.
        """
        todo_path = self._get_todo_workspace_path(session_id)
        if todo_path is None or not todo_path.exists():
            raise RuntimeError(
                f"todo.json not found at {todo_path!r}; cannot auto-flush"
            )
        with open(todo_path, "r", encoding="utf-8") as f:
            items = json.load(f)
        if not isinstance(items, list):
            raise RuntimeError("todo.json is not a JSON list; cannot auto-flush")

        changed = 0
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            tid = item.get("id", str(index))
            if tid in overrides:
                item["status"] = overrides[tid]
                changed += 1
        if changed != len(overrides):
            raise RuntimeError(
                f"auto-flush matched {changed}/{len(overrides)} todo ids; "
                "in-memory todo map is stale relative to todo.json — "
                "failing closed"
            )

        todo_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path_str = tempfile.mkstemp(
            prefix=".todo-flush-",
            suffix=".tmp",
            dir=str(todo_path.parent),
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path_str, str(todo_path))
        except Exception:
            try:
                os.unlink(tmp_path_str)
            except OSError:
                pass
            raise
        return changed

    @staticmethod
    def _flush_target_status(incomplete_status: str) -> str:
        """Map an incomplete item status to its flush target. Extensible policy.

        Everything flushes to "completed" because the shared diff emitter
        (_sync_todo_and_emit_transitions) handles ->completed transitions
        (in_progress->completed => task.complete; pending->completed =>
        task.start then task.complete).
        """
        return "completed"

    def _resolve_todo_modify_tool(self) -> Any | None:
        """Return the registered ``todo_modify`` tool so we can share its lock.

        Mirrors ``JiuWenSwarmDeepAdapter._cancel_pending_todos``: resolve the
        tool card from the deep agent's ``ability_manager``, then the live
        tool instance via ``Runner.resource_mgr``. That instance carries the
        shared ``TodoLockManager`` the model's own ``todo_modify`` calls use,
        so a read-modify-write guarded by its ``operation(session_id)`` is
        mutually exclusive with a concurrent ``todo_modify`` — closing the
        lost-update window that ``os.replace`` alone cannot (parallel_tool_calls
        can emit ``todo_modify`` + ``skill_complete`` in the same turn).

        Returns None when the tool/runner is unreachable (cold path, tests);
        callers fall back to an unlocked-but-still-verified flush.
        """
        da = self._deep_agent
        if da is None:
            return None
        ability_manager = getattr(da, "ability_manager", None)
        if ability_manager is None:
            return None
        try:
            from openjiuwen.core.runner import Runner

            tool_card = ability_manager.get("todo_modify")
            if tool_card is None:
                return None
            return Runner.resource_mgr.get_tool(tool_card.id)
        except Exception:
            logger.debug(
                "[TaskExecutionRail] resolve todo_modify tool failed",
                exc_info=True,
            )
            return None

    def _verify_no_incomplete_after_flush(self, session_id: str) -> None:
        """Reload the persisted task-list and assert no incomplete items remain.

        Fail-closed gate: any incomplete item seen after persist (a stale id
        map that skipped some overrides, or a concurrent writer that re-added
        a pending/in_progress item before we released the lock) surfaces as a
        raise so the caller falls back to ``_apply_skill_complete_block``
        instead of letting ``skill_complete`` proceed on an unverified state.
        """
        reloaded = self._build_map_from_todo_items(
            self._load_todo_from_json(session_id)
        )
        if self._has_incomplete_todos(reloaded):
            incomplete = [
                tid
                for tid, task in reloaded.items()
                if str(task.get("status", "pending")).lower()
                not in self._TODO_DONE_STATUSES
            ]
            raise RuntimeError(
                "skill_complete auto-flush left incomplete todos after "
                f"persist: {incomplete} (session={session_id}); failing closed"
            )

    async def _flush_incomplete_todos_on_skill_complete(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Auto-finalize incomplete todos so skill_complete proceeds without
        a model round.

        Reuses the shared ``TodoLockManager`` from the registered
        ``todo_modify`` tool so the read-modify-write is mutually exclusive
        with a concurrent ``todo_modify`` (parallel_tool_calls can issue both
        in one turn; without the lock, the two write paths would race and
        ``os.replace`` only prevents half-files, not lost updates). Under the
        lock we persist the flushed statuses, reload, and FAIL CLOSED: if any
        incomplete task remains (persistence miss, stale id map, or a
        concurrent writer) we raise so ``before_tool_call`` falls back to
        ``_apply_skill_complete_block`` instead of letting skill_complete
        through. The subsequent ``_sync_todo_and_emit_transitions`` reuses the
        existing diff+emit pipeline so the same task.start/complete events the
        model would have produced via ``todo_modify`` are emitted. No new event
        types.
        """
        if ctx.session is None:
            return
        session_id = ctx.session.get_session_id()
        # Snapshot pre-flush state so the diff sees the transition
        self._todo_map_before_tool = dict(self._todo_map)

        overrides: dict[str, str] = {}
        for task_id, task in self._todo_map.items():
            status = str(task.get("status", "pending")).lower()
            if status in self._TODO_DONE_STATUSES:
                continue
            overrides[task_id] = self._flush_target_status(status)

        if not overrides:
            return

        modify_tool = self._resolve_todo_modify_tool()
        lock_manager = (
            getattr(modify_tool, "_lock_manager", None)
            if modify_tool is not None
            else None
        )

        if lock_manager is not None:
            # Hold the session lock across persist + verify so a concurrent
            # todo_modify cannot interleave a stale-snapshot write that reverts
            # the flush (lost update). asyncio.Lock is non-reentrant, so we do
            # raw stdlib I/O here rather than calling the tool's
            # load_todos/save_todos (which would re-acquire and deadlock).
            async with lock_manager.operation(session_id):
                self._persist_todo_statuses(session_id, overrides)
                self._verify_no_incomplete_after_flush(session_id)
        else:
            # Shared lock unreachable (ability_manager/Runner not ready, or
            # todo_modify not registered). Persist + verify anyway; the
            # post-write verify still fails closed on a reverted state. The
            # mutual-exclusion window vs a concurrent todo_modify is not closed
            # on this path, so log it for observability.
            logger.warning(
                "[TaskExecutionRail] shared todo lock unavailable; "
                "auto-flush proceeding without mutual exclusion "
                "(session=%s)",
                session_id,
            )
            self._persist_todo_statuses(session_id, overrides)
            self._verify_no_incomplete_after_flush(session_id)

        # Reuse the existing diff+emit pipeline (reads the task-list file,
        # emits events) — outside the lock to avoid blocking todo_modify
        # during stream writes.
        await self._sync_todo_and_emit_transitions(ctx)

    # ------------------------------------------------------------------
    # State transition detection + event emission
    # ------------------------------------------------------------------

    async def _sync_todo_and_emit_transitions(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Diff todo state before vs after a todo tool call and emit events.

        - pending -> in_progress  => task.start
        - in_progress -> completed => task.complete
        - pending -> completed (skipped in_progress) => task.start then
          task.complete. Frontend hidePending hides pending rows, and the
          left task list falls back to task.start segments while streaming;
          without start/complete the stage is invisible until the frozen
          completed snapshot appears after the run finishes.
        Always emits task.update (full snapshot) at the end.
        """
        if ctx.session is None:
            return
        session_id = ctx.session.get_session_id()
        parent_request_id = self._extract_request_id(ctx)

        try:
            todo_items = self._load_todo_from_json(session_id)
        except Exception as exc:
            logger.warning(
                "[TaskExecutionRail] Failed to load todo.json: %s",
                exc,
            )
            return

        current_map = self._build_map_from_todo_items(todo_items)
        previous_map = self._todo_map_before_tool or self._todo_map

        completed_in_batch: list[str] = []
        for task_id, current in current_map.items():
            prev = previous_map.get(task_id)
            prev_status = prev.get("status", "") if prev else ""
            curr_status = current.get("status", "")

            if (
                curr_status == "in_progress"
                and prev_status not in ("in_progress", "completed")
            ):
                if task_id not in self._todo_started:
                    await self._emit_task_start_event(
                        ctx.session,
                        task_id,
                        current,
                        parent_request_id,
                        source="todo",
                    )
                    self._todo_started.add(task_id)
            elif (
                curr_status == "completed"
                and prev_status != "completed"
            ):
                completed_in_batch.append(task_id)
                if (
                    prev_status != "in_progress"
                    and task_id not in self._todo_started
                ):
                    logger.info(
                        "[TaskExecutionRail] pending→completed: "
                        "emit task.start+task.complete: %s "
                        "prev_status=%r session_id=%s",
                        task_id,
                        prev_status,
                        session_id,
                    )
                    await self._emit_task_start_event(
                        ctx.session,
                        task_id,
                        current,
                        parent_request_id,
                        source="todo",
                    )
                    self._todo_started.add(task_id)
                await self._emit_task_complete_event(
                    ctx.session,
                    task_id,
                    current,
                    status="succeeded",
                    parent_request_id=parent_request_id,
                )

        self._todo_map = current_map
        self._todo_map_before_tool = {}
        self._bind_context_after_todo_sync(
            completed_in_batch, current_map
        )
        await self._emit_task_update_event(
            ctx.session, parent_request_id
        )

    async def _emit_task_start_event(
        self,
        session: Session,
        task_id: str,
        task: dict[str, Any],
        parent_request_id: str,
        source: Literal["todo"],
    ) -> None:
        full_task_id = f"{source}:{task_id}"

        if full_task_id in self._active_tasks:
            _ACTIVE_TASK_ID.set(full_task_id)
            return

        context = TaskExecutionContext(
            task_id=full_task_id,
            task_content=str(task.get("content", "")),
            task_index=int(task.get("index", 0)),
            total_tasks=int(task.get("total", 0)),
            parent_request_id=parent_request_id,
            start_time=time.time(),
            source=source,
        )
        self._active_tasks[full_task_id] = context
        _ACTIVE_TASK_ID.set(full_task_id)

        logger.info(
            "[TaskExecutionRail] task.start: %s - %s",
            full_task_id,
            context.task_content,
        )

        try:
            await session.write_stream(
                OutputSchema(
                    type="task.start",
                    index=0,
                    payload={
                        "task_id": context.task_id,
                        "task_content": context.task_content,
                        "task_index": context.task_index,
                        "total_tasks": context.total_tasks,
                        "parent_request_id": context.parent_request_id,
                        "timestamp": context.start_time,
                        "source": source,
                    },
                )
            )
        except Exception:
            logger.debug(
                "[TaskExecutionRail] task.start emit failed",
                exc_info=True,
            )

    async def _emit_task_complete_event(
        self,
        session: Session,
        task_id: str,
        task: dict[str, Any],
        *,
        status: Literal["succeeded", "failed", "skipped"],
        error: str | None = None,
        parent_request_id: str = "",
    ) -> None:
        full_task_id = f"todo:{task_id}"
        context = self._active_tasks.pop(full_task_id, None)
        timestamp = time.time()

        if context:
            duration_ms = int(
                (timestamp - context.start_time) * 1000
            )
            payload_task_id = context.task_id
            task_content = context.task_content
            source = context.source
        else:
            duration_ms = 0
            payload_task_id = full_task_id
            task_content = str(task.get("content", ""))
            source = "todo"

        if get_current_task_id() == full_task_id:
            _ACTIVE_TASK_ID.set(None)

        logger.info(
            "[TaskExecutionRail] task.complete: %s - %s (%dms)",
            full_task_id,
            status,
            duration_ms,
        )

        try:
            await session.write_stream(
                OutputSchema(
                    type="task.complete",
                    index=0,
                    payload={
                        "task_id": payload_task_id,
                        "task_content": task_content,
                        "status": status,
                        "duration_ms": duration_ms,
                        "error": error,
                        "timestamp": timestamp,
                        "source": source,
                        "parent_request_id": parent_request_id,
                    },
                )
            )
        except Exception:
            logger.debug(
                "[TaskExecutionRail] task.complete emit failed",
                exc_info=True,
            )

    async def _emit_task_update_event(
        self,
        session: Session,
        parent_request_id: str | None = None,
    ) -> None:
        """Send full task list snapshot (all todos) to the frontend."""
        session_id = session.get_session_id()
        todo_items = self._load_todo_from_json(session_id)
        todo_tasks = self._format_tasks_for_update(
            todo_items, source="todo"
        )

        all_tasks = todo_tasks
        total = len(all_tasks)
        completed = sum(
            1 for t in all_tasks
            if t.get("status") == "completed"
        )
        in_progress = sum(
            1 for t in all_tasks
            if t.get("status") == "in_progress"
        )
        pending = sum(
            1 for t in all_tasks
            if t.get("status") == "pending"
        )

        payload: dict[str, Any] = {
            "tasks": all_tasks,
            "total_tasks": total,
            "completed_tasks": completed,
            "in_progress_tasks": in_progress,
            "pending_tasks": pending,
            "timestamp": time.time(),
        }

        if parent_request_id:
            payload["parent_request_id"] = parent_request_id

        try:
            await session.write_stream(
                OutputSchema(
                    type="task.update",
                    index=0,
                    payload=payload,
                )
            )
        except Exception:
            logger.debug(
                "[TaskExecutionRail] task.update emit failed",
                exc_info=True,
            )

        logger.info(
            "[TaskExecutionRail] task.update: %d tasks - "
            "%d completed, %d in_progress, %d pending",
            total,
            completed,
            in_progress,
            pending,
        )

    def _format_tasks_for_update(
        self,
        items: list[dict[str, Any]],
        source: Literal["todo"],
    ) -> list[dict[str, Any]]:
        """Format todo items into task dicts for task.update payload."""
        formatted: list[dict[str, Any]] = []
        for item in items:
            task_id = str(
                item.get("id", item.get("idx", ""))
            )
            task: dict[str, Any] = {
                "task_id": task_id,
                "task_content": item.get(
                    "content", item.get("activeForm", "")
                ),
                "task_index": item.get(
                    "index", item.get("idx", 0)
                ),
                "source": source,
                "status": item.get("status", "pending"),
            }
            full_task_id = f"{source}:{task_id}"
            context = self._active_tasks.get(full_task_id)
            if context:
                task["start_time"] = context.start_time
            formatted.append(task)
        return formatted

    # ------------------------------------------------------------------
    # Task binding (ContextVar management)
    # ------------------------------------------------------------------

    def _task_candidates_by_status(
        self,
        allowed: frozenset[str],
    ) -> list[tuple[int, str]]:
        candidates: list[tuple[int, str]] = []
        for task_id, task in self._todo_map.items():
            if str(task.get("status", "")).lower() in allowed:
                candidates.append(
                    (int(task.get("index", 0)), task_id)
                )
        candidates.sort(key=lambda item: (item[0], item[1]))
        return candidates

    def _pick_task_id_for_binding(self) -> str | None:
        """Pick the first in_progress task, else first pending."""
        active = self._task_candidates_by_status(
            self._BINDING_IN_PROGRESS
        )
        if active:
            return active[0][1]
        pending = self._task_candidates_by_status(
            self._BINDING_PENDING
        )
        if pending:
            return pending[0][1]
        return None

    def _pick_next_pending_after(
        self, completed_task_id: str
    ) -> str | None:
        completed = self._todo_map.get(completed_task_id)
        if not completed:
            return self._pick_task_id_for_binding()
        completed_index = int(completed.get("index", 0))
        pending: list[tuple[int, str]] = []
        for task_id, task in self._todo_map.items():
            if (
                str(task.get("status", "")).lower()
                in self._BINDING_PENDING
            ):
                task_index = int(task.get("index", 0))
                if task_index > completed_index:
                    pending.append((task_index, task_id))
        if not pending:
            return None
        pending.sort(key=lambda item: (item[0], item[1]))
        return pending[0][1]

    def _set_active_task_binding(
        self, raw_task_id: str | None
    ) -> None:
        if raw_task_id:
            full_task_id = f"todo:{raw_task_id}"
            _ACTIVE_TASK_ID.set(full_task_id)
            logger.debug(
                "[TaskExecutionRail] task_id binding: %s",
                full_task_id,
            )
            return
        _ACTIVE_TASK_ID.set(None)

    def _bind_context_after_todo_sync(
        self,
        completed_in_batch: list[str],
        current_map: dict[str, dict[str, Any]],
    ) -> None:
        """Re-bind task_id after todo.json changed.

        in_progress wins over 'next pending after completed' so S3
        in_progress + S4 pending does not bind to S4 when S1/S2 complete
        in the same batch.
        """
        in_progress = self._task_candidates_by_status(
            self._BINDING_IN_PROGRESS
        )
        if in_progress:
            self._set_active_task_binding(in_progress[0][1])
            return
        if completed_in_batch:
            anchor_id = max(
                completed_in_batch,
                key=lambda tid: int(
                    current_map.get(tid, {}).get("index", 0)
                ),
            )
            next_id = self._pick_next_pending_after(anchor_id)
            if next_id:
                self._set_active_task_binding(next_id)
                return
        self._bind_context_to_in_progress_task()

    def _bind_context_to_in_progress_task(self) -> None:
        """Bind stream/artifact task_id to in_progress, else first pending."""
        self._set_active_task_binding(
            self._pick_task_id_for_binding()
        )

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_request_id(ctx: AgentCallbackContext) -> str:
        value = getattr(ctx.inputs, "request_id", None)
        if value:
            return str(value).strip()
        if isinstance(ctx.inputs, dict):
            raw = ctx.inputs.get("request_id")
            if raw:
                return str(raw).strip()
        # ToolCallInputs usually has no request_id; fall back to the active
        # perf request context so task.* UI payloads still carry
        # parent_request_id when the rail is enabled.
        try:
            from jiuwenswarm.perf.context import (
                extract_session_id_from_callback,
                get_request_context,
            )

            session_id = None
            if ctx.session is not None:
                try:
                    session_id = str(ctx.session.get_session_id() or "").strip() or None
                except Exception:
                    session_id = extract_session_id_from_callback(ctx)
            else:
                session_id = extract_session_id_from_callback(ctx)
            req_ctx = get_request_context(session_id=session_id)
            if req_ctx:
                return str(req_ctx.get("request_id") or "").strip()
        except Exception:
            logger.debug(
                "[TaskExecutionRail] request_id fallback failed",
                exc_info=True,
            )
        return ""
