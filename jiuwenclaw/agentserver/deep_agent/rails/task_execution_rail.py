# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
import logging
import re
import time
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs, ToolCallInputs
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.workspace.workspace import WorkspaceNode

from jiuwenclaw.utils import get_agent_sessions_dir

logger = logging.getLogger(__name__)

_ARTIFACT_SEND_CACHE: dict[str, float] = {}
_ARTIFACT_SEND_CACHE_WINDOW = 120.0
_ARTIFACT_MTIME_BUFFER = 1.0


def _is_recently_sent(path: str) -> bool:
    normalized_path = path.replace("\\", "/").lower()
    last_sent_time = _ARTIFACT_SEND_CACHE.get(normalized_path)
    if last_sent_time is None:
        return False
    return (time.time() - last_sent_time) < _ARTIFACT_SEND_CACHE_WINDOW


def _mark_as_sent(path: str) -> None:
    normalized_path = path.replace("\\", "/").lower()
    _ARTIFACT_SEND_CACHE[normalized_path] = time.time()
    # 清理过期缓存（超过时间窗口的记录）
    expired_keys = [
        k for k, v in _ARTIFACT_SEND_CACHE.items()
        if (time.time() - v) > _ARTIFACT_SEND_CACHE_WINDOW * 2
    ]
    for k in expired_keys:
        _ARTIFACT_SEND_CACHE.pop(k, None)

_ARTIFACT_PREVIEW_EXTENSIONS = frozenset({
    ".md", ".html", ".pptx", ".docx", ".xlsx", ".xlsm", ".csv", ".tsv",
    ".pdf", ".txt", ".json", ".yaml", ".yml", ".xml",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
})

# 文件路径检测的正则表达式模式
_FILE_PATH_PATTERNS = [
    # {workspace} 变量模式
    re.compile(r'\{workspace\}[/\\]?[^\s\]\}\)\,\'\"`<>，。；、：]+', re.IGNORECASE),
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

# 需要排除的路径模式（非产物文件），系统敏感目录基于时间戳校验过滤
_ALWAYS_EXCLUDED_PATH_PATTERNS = [
    re.compile(r'SKILL\.md', re.IGNORECASE),
    re.compile(r'AGENT\.md', re.IGNORECASE),
    re.compile(r'[/\\]node_modules[/\\]', re.IGNORECASE),
    re.compile(r'[/\\]path[/\\]to[/\\]', re.IGNORECASE),  # /path/to/output 示例
    re.compile(r'/user/specified', re.IGNORECASE),  # 示例路径
    re.compile(r'\{skill_root\}', re.IGNORECASE),  # skill 变量引用
    re.compile(r'[/\\]\.jiuwenclaw[/\\]', re.IGNORECASE),
    re.compile(r'^\./[^/\\]+[/\\]SKILL\.md$', re.IGNORECASE),  # skill 子目录引用
    re.compile(r'^\./[^/\\]+[/\\]AGENT\.md$', re.IGNORECASE),  # skill 子目录引用
]

_STRUCTURED_PATH_FIELD_KEYWORDS = frozenset({
    "path",
    "file",
    "files",
    "output",
    "outputs",
    "artifact",
    "artifacts",
    "generated",
    "result",
})

_PATH_TRAILING_CHARS = "'\"`\\]\\}\\),.;:，。；、："


def _is_excluded_path(path_str: str) -> bool:
    """检查路径是否应该被排除（非产物文件）。

    1. 无条件排除规则（SKILL.md, node_modules 等）
    2. 扩展名不在支持列表中 → 排除
    
    Args:
        path_str: 待检查的路径字符串
    
    Returns:
        True 如果是排除的路径，False 否则
    """
    # Step 1: 无条件排除规则
    for pattern in _ALWAYS_EXCLUDED_PATH_PATTERNS:
        if pattern.search(path_str):
            return True
    
    # Step 2: 扩展名检查
    path_lower = path_str.lower()
    for ext in _ARTIFACT_PREVIEW_EXTENSIONS:
        if path_lower.endswith(ext):
            return False

    return True


def _clean_path_candidate(path_str: str) -> str:
    """清理正则或结构化字段中提取到的路径候选。"""
    return path_str.strip().strip(_PATH_TRAILING_CHARS).strip()


def _path_identity(path_str: str) -> str:
    """生成用于去重的路径标识，兼容 Windows/Unix 分隔符和变量大小写。"""
    return path_str.replace("\\", "/").lower()


def _iter_structured_path_values(value: Any, parent_key: str = "") -> list[str]:
    """从结构化工具结果中递归提取路径字段值。"""
    paths: list[str] = []
    key_lower = parent_key.lower()
    key_is_path_like = any(keyword in key_lower for keyword in _STRUCTURED_PATH_FIELD_KEYWORDS)

    if isinstance(value, dict):
        for child_key, child_value in value.items():
            paths.extend(_iter_structured_path_values(child_value, str(child_key)))
        return paths

    if isinstance(value, (list, tuple, set)):
        for item in value:
            paths.extend(_iter_structured_path_values(item, parent_key))
        return paths

    if key_is_path_like and value is not None:
        candidate = _clean_path_candidate(str(value))
        if candidate:
            paths.append(candidate)

    return paths


def _extract_artifact_paths_from_tool_result(
    tool_result: Any,
    workspace_base: str | Path | None = None,
    tool_start_time: float | None = None,
) -> list[dict[str, Any]]:
    """从工具输出结果中提取文件路径并验证是否为有效的工件。
    
    检测逻辑：
    1. 从工具输出字符串或字典中提取文件路径
    2. 检查路径是否在workspace目录下（或{workspace}变量）
    3. 验证文件扩展名是否支持预览
    4. 返回工件信息列表（路径、文件名、扩展名、文件大小等）
    
    Args:
        tool_result: 工具执行结果（字符串、字典或对象）
        workspace_base: 工作空间基准路径（可选，用于验证路径合法性）
    
    Returns:
        工件信息列表，每个元素包含：
        - path: 文件绝对路径
        - name: 文件名
        - extension: 文件扩展名
        - size: 文件大小（字节），若文件不存在则为None
        - exists: 文件是否存在
        
    """
    artifacts: list[dict[str, Any]] = []
    
    # 将工具结果转换为字符串进行正则匹配
    result_text = ""
    result_dict: dict[str, Any] | None = None
    
    if tool_result is None:
        return artifacts
    
    # 处理字符串结果
    if isinstance(tool_result, str):
        result_text = tool_result
    # 处理字典结果
    elif isinstance(tool_result, dict):
        result_dict = tool_result
        result_text = json.dumps(tool_result, ensure_ascii=False)
    # 处理对象结果
    elif hasattr(tool_result, '__dict__'):
        result_dict = tool_result.__dict__
        result_text = str(tool_result)
    else:
        result_text = str(tool_result)
    
    # 从字典结果中提取常见路径字段
    seen_artifact_paths: set[str] = set()
    if result_dict is not None:
        potential_paths = _iter_structured_path_values(result_dict)

        for p in potential_paths:
            if not p:
                continue
            if _is_excluded_path(p):
                continue

            artifact = _validate_and_build_artifact(p, workspace_base, tool_start_time=tool_start_time)
            if not artifact:
                continue

            identity = _path_identity(artifact.get("path", p))
            if identity in seen_artifact_paths:
                continue

            artifacts.append(artifact)
            seen_artifact_paths.add(identity)
    
    # 使用正则表达式从文本中提取路径
    seen_paths: set[str] = set()
    for pattern in _FILE_PATH_PATTERNS:
        matches = pattern.findall(result_text)
        for match in matches:
            # 清理路径（去除尾部的非法字符）
            cleaned_path = _clean_path_candidate(match)
            if not cleaned_path:
                continue
            candidate_identity = _path_identity(cleaned_path)
            if candidate_identity in seen_paths:
                continue
            seen_paths.add(candidate_identity)

            # 排除非产物路径（skill文件、示例路径等）
            if _is_excluded_path(cleaned_path):
                continue

            artifact = _validate_and_build_artifact(cleaned_path, workspace_base, tool_start_time=tool_start_time)
            if not artifact:
                continue

            identity = _path_identity(artifact.get("path", cleaned_path))
            if identity not in seen_artifact_paths:
                artifacts.append(artifact)
                seen_artifact_paths.add(identity)
    
    return artifacts


def _validate_and_build_artifact(
    path_str: str,
    workspace_base: str | Path | None = None,
    tool_start_time: float | None = None,
) -> dict[str, Any] | None:
    """验证路径并构建工件信息字典。
    
    Args:
        path_str: 待验证的路径字符串
        workspace_base: 工作空间基准路径
    
    Returns:
        工件信息字典，若路径无效则返回None
    """
    path_str = path_str.strip()
    if not path_str:
        return None
    
    # 处理 {workspace} 变量
    has_workspace_var = "{workspace}" in path_str.lower() or path_str.startswith("{workspace")
    if has_workspace_var:
        if workspace_base:
            actual_path = re.sub(r"\{workspace\}", str(workspace_base), path_str, flags=re.IGNORECASE)
        else:
            # 无法确定实际路径，但仍然记录为潜在工件
            actual_path = path_str
    else:
        actual_path = path_str
    
    # 解析路径
    try:
        path_obj = Path(actual_path)
    except Exception:
        return None
    
    # 获取扩展名
    extension = path_obj.suffix.lower()
    
    # 检查是否支持预览
    if extension not in _ARTIFACT_PREVIEW_EXTENSIONS:
        return None
    
    # 检查文件是否存在
    exists = False
    size: int | None = None
    
    # 判断是否为绝对路径
    is_absolute = path_obj.is_absolute()
    
    if not has_workspace_var:
        try:
            target_path = None
            # 首先尝试直接检查路径（绝对路径或相对于当前工作目录）
            if path_obj.exists() and path_obj.is_file():
                target_path = path_obj
            # 如果是相对路径且直接检查失败，尝试用 workspace_base 构建完整路径
            elif not is_absolute and workspace_base:
                workspace_path = Path(workspace_base)
                candidate = workspace_path / path_obj
                if candidate.exists() and candidate.is_file():
                    target_path = candidate
                    logger.debug("[ArtifactDetector] Resolved relative path: '%s' -> '%s'", path_str, str(candidate))
            
            if target_path:
                stat_result = target_path.stat()
                
                # 时间戳校验逻辑
                if tool_start_time is not None:
                    if stat_result.st_mtime < (tool_start_time - _ARTIFACT_MTIME_BUFFER):
                        return None
                
                exists = True
                size = stat_result.st_size
                path_obj = target_path
                
        except Exception as e:
            logger.debug("[ArtifactDetector] Path existence check failed: '%s', error=%s", path_str, e)

    if exists:
        logger.info(
            "[ArtifactDetector] 产物已生成: name='%s', path='%s', size=%d bytes, extension='%s'",
            path_obj.name, str(path_obj), size or 0, extension
        )
    
    return {
        "path": str(path_obj),
        "original_path": path_str,
        "name": path_obj.name,
        "extension": extension,
        "size": size,
        "exists": exists,
    }


_ACTIVE_TASK_ID: ContextVar[str | None] = ContextVar("active_task_id", default=None)


def get_current_task_id() -> str | None:
    """Return current task id for stream payload correlation."""
    return _ACTIVE_TASK_ID.get()


@dataclass
class TaskExecutionContext:
    task_id: str
    task_content: str
    task_index: int
    total_tasks: int
    parent_request_id: str
    start_time: float
    source: Literal["todo", "skill_step"]
    status: Literal["running", "succeeded", "failed", "skipped"] = "running"


class TaskExecutionRail(DeepAgentRail):
    """Emit task.start/task.complete around todo and skill_step execution status transitions.

    Tool Categories:
    - TODO_TOOLS: all todo_* tools that modify todo.json - triggers todo state change detection
    - SKILL_STEP_TOOLS: skill_step - triggers skill_step state change detection
    - BUSINESS_TOOLS: all other tools - auto-starts first pending skill_step task
    """

    priority = 85

    # 所有修改 todo.json 的工具（包括创建、修改状态、插入、删除、批量操作）
    TODO_TOOLS = frozenset({
        "todo_create", "todo_start", "todo_complete", "todo_complete_batch",
        "todo_insert", "todo_remove", "todo_modify", "todo_list"
    })
    SKILL_STEP_TOOLS = frozenset({"skill_step"})
    SKILL_COMPLETE_TOOLS = frozenset({"skill_complete"})
    ALL_TASK_TOOLS = TODO_TOOLS | SKILL_STEP_TOOLS

    def __init__(self) -> None:
        super().__init__()
        self._todo_map: dict[str, dict[str, Any]] = {}
        self._todo_map_before_tool: dict[str, dict[str, Any]] = {}
        self._skill_step_map: dict[str, dict[str, Any]] = {}
        self._skill_step_map_before_tool: dict[str, dict[str, Any]] = {}
        self._active_tasks: dict[str, TaskExecutionContext] = {}
        self._todo_started: set[str] = set()
        self._skill_step_started: set[str] = set()
        self._deep_agent: Any | None = None
        self._current_task_id: str | None = None

    def get_current_task_id(self) -> str | None:
        return self._current_task_id

    def init(self, agent: Any) -> None:
        self._deep_agent = agent

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        self._todo_map = {}
        self._todo_map_before_tool = {}
        self._skill_step_map = {}
        self._skill_step_map_before_tool = {}
        self._active_tasks = {}
        self._todo_started = set()
        self._skill_step_started = set()
        self._current_task_id = None
        _ACTIVE_TASK_ID.set(None)
        if isinstance(ctx.inputs, InvokeInputs):
            await self._init_task_tracking(ctx.session)

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        setattr(ctx, '_tool_start_time', time.time())
        if not isinstance(ctx.inputs, ToolCallInputs):
            return
        tool_name = ctx.inputs.tool_name

        if tool_name in self.TODO_TOOLS:
            self._todo_map_before_tool = dict(self._todo_map)
            return

        if tool_name in self.SKILL_STEP_TOOLS:
            return

        if tool_name in self.SKILL_COMPLETE_TOOLS:
            self._clear_skill_step_cache()
            return

        await self._maybe_start_pending_skill_step_task(ctx)
        self._bind_context_to_in_progress_task()

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        if not isinstance(ctx.inputs, ToolCallInputs):
            return
        tool_name = ctx.inputs.tool_name

        await self._detect_and_emit_artifact_generated(ctx)

        if tool_name in self.TODO_TOOLS:
            await self._sync_todo_and_emit_transitions(ctx)
            return

        if tool_name in self.SKILL_STEP_TOOLS:
            await self._start_first_uncompleted_skill_step(ctx)
            # 发送完整任务列表更新（包含 todo + skill_step）
            parent_request_id = self._extract_request_id(ctx)
            await self._emit_task_update_event(ctx.session, parent_request_id)
            return

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        self._todo_map_before_tool = {}
        self._skill_step_map_before_tool = {}
        self._bind_context_to_in_progress_task()

    async def _detect_and_emit_artifact_generated(self, ctx: AgentCallbackContext) -> None:
        """检测工具输出中的工件并发送生成消息到前端。
        
        artifact 检测和发送。
        
        Args:
            ctx: Agent回调上下文，包含session和工具调用信息
        """
        # Import here to avoid circular dependency at module load time
        from jiuwenclaw.agentserver.deep_agent.artifact_emitter import (
            emit_artifact_generated,
            ArtifactEmitContext,
        )
        
        session = ctx.session
        if session is None:
            logger.debug("[ArtifactEmitter] Skip: session is None")
            return
        if not isinstance(ctx.inputs, ToolCallInputs):
            logger.debug("[ArtifactEmitter] Skip: not ToolCallInputs")
            return
        
        artifact_ctx = ArtifactEmitContext(
            session=session,
            tool_result=ctx.inputs.tool_result,
            tool_name=ctx.inputs.tool_name,
            workspace_base=self._get_workspace_base_path(),
            tool_start_time=getattr(ctx, '_tool_start_time', None),
            task_id=self.get_current_task_id() or get_current_task_id(),
            log_prefix="[ArtifactEmitter]",
        )
        
        await emit_artifact_generated(artifact_ctx)

    def _get_workspace_base_path(self) -> Path | None:
        """获取工作空间基准路径。
        
        用于验证文件路径是否在合法的工作目录中。
        优先使用Rail配置的workspace，其次使用agent工作空间。
        
        Returns:
            工作空间路径，若未配置则返回None
        """
        # 优先使用Rail配置的workspace
        if self.workspace is not None:
            try:
                return self.workspace.get_node_path(WorkspaceNode.TODO)
            except Exception as e:
                logger.warning(
                    "获取workspace节点路径失败，将尝试使用agent工作空间: %s",
                    str(e)
                )

        try:
            from jiuwenclaw.utils import get_agent_workspace_dir
            return get_agent_workspace_dir()
        except Exception as e:
            logger.warning(
                "获取agent工作空间目录失败，将返回None: %s",
                str(e)
            )

        return None

    async def _init_task_tracking(self, session: Session | None) -> None:
        if session is None:
            return
        session_id = session.get_session_id()

        try:
            todo_items = self._load_todo_from_json(session_id)
            if todo_items:
                self._todo_map = self._build_map_from_todo_items(todo_items)
                logger.info("[TaskExecutionRail] Loaded todo.json: %d tasks", len(todo_items))
        except Exception as exc:
            logger.debug("[TaskExecutionRail] Failed to load todo.json: %s", exc)

        try:
            skill_step_items = self._load_skill_step_from_markdown(session_id)
            if skill_step_items:
                self._skill_step_map = self._build_map_from_skill_step_items(skill_step_items)
                logger.info("[TaskExecutionRail] Loaded skill_step.md: %d tasks", len(skill_step_items))
        except Exception as exc:
            logger.debug("[TaskExecutionRail] Failed to load skill_step.md: %s", exc)

    def _load_todo_from_json(self, session_id: str) -> list[dict[str, Any]]:
        todo_path = self._get_todo_workspace_path(session_id)
        if not todo_path.exists():
            return []
        with open(todo_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []

    def _load_skill_step_from_markdown(self, session_id: str) -> list[dict[str, Any]]:
        skill_step_path = self._get_skill_step_workspace_path(session_id)
        if not skill_step_path.exists():
            return []
        items: list[dict[str, Any]] = []
        with open(skill_step_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                status = self._parse_markdown_status(line)
                rest = (
                    line.replace("- [x]", "")
                    .replace("- [-]", "")
                    .replace("- [>]", "")
                    .replace("- [ ]", "")
                    .strip()
                )
                if "." in rest:
                    idx_str, _, task_text = rest.partition(".")
                    task_text = task_text.split("|")[0].strip()
                    try:
                        idx = int(idx_str.strip())
                        items.append({"idx": idx, "content": task_text, "status": status})
                    except ValueError:
                        pass
        return sorted(items, key=lambda x: x["idx"])

    def _parse_markdown_status(self, line: str) -> str:
        if "[-]" in line:
            return "cancelled"
        if "[>]" in line:
            return "in_progress"
        if "[x]" in line.lower() or "[√]" in line:
            return "completed"
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 2:
            status_str = parts[1].strip().lower()
            if status_str in ("running", "in_progress"):
                return "in_progress"
            if status_str in ("waiting", "pending"):
                return "pending"
            if status_str == "completed":
                return "completed"
            if status_str == "cancelled":
                return "cancelled"
        return "pending"

    def _build_map_from_todo_items(self, items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        mapped: dict[str, dict[str, Any]] = {}
        total = len(items)
        for index, item in enumerate(items):
            task_id = item.get("id", str(index))
            status = item.get("status", "pending")
            normalized_status = status.lower() if isinstance(status, str) else str(status).lower()
            mapped[task_id] = {
                "content": item.get("content", item.get("activeForm", "")),
                "status": normalized_status,
                "index": index,
                "total": total,
            }
        return mapped

    def _build_map_from_skill_step_items(self, items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        mapped: dict[str, dict[str, Any]] = {}
        total = len(items)
        for index, item in enumerate(items):
            task_id = str(item.get("idx", index))
            mapped[task_id] = {
                "content": item.get("content", ""),
                "status": item.get("status", "pending"),
                "index": index,
                "total": total,
            }
        return mapped

    async def _sync_todo_and_emit_transitions(self, ctx: AgentCallbackContext) -> None:
        if ctx.session is None:
            return
        session_id = ctx.session.get_session_id()
        parent_request_id = self._extract_request_id(ctx)

        try:
            todo_items = self._load_todo_from_json(session_id)
        except Exception as exc:
            logger.warning("[TaskExecutionRail] Failed to load todo.json: %s", exc)
            return

        current_map = self._build_map_from_todo_items(todo_items)
        previous_map = self._todo_map_before_tool or self._todo_map

        for task_id, current in current_map.items():
            prev = previous_map.get(task_id)
            prev_status = prev.get("status", "") if prev else ""
            curr_status = current.get("status", "")

            if curr_status == "in_progress" and prev_status not in ("in_progress", "completed"):
                if task_id not in self._todo_started:
                    await self._emit_task_start_event(
                        ctx.session, task_id, current, parent_request_id, source="todo"
                    )
                    self._todo_started.add(task_id)
            elif prev_status == "in_progress" and curr_status == "completed":
                await self._emit_task_complete_event(ctx.session, task_id, current, status="succeeded")

        self._todo_map = current_map
        self._todo_map_before_tool = {}
        self._bind_context_to_in_progress_task()

        await self._emit_task_update_event(ctx.session, parent_request_id)

    def _clear_skill_step_cache(self) -> None:
        """Clear cached skill_step data when skill_complete is called."""
        self._skill_step_map = {}
        self._skill_step_map_before_tool = {}
        self._skill_step_started = set()
        for full_task_id in list(self._active_tasks):
            if full_task_id.startswith("skill_step:"):
                self._active_tasks.pop(full_task_id, None)
        _ACTIVE_TASK_ID.set(None)
        self._current_task_id = None

    async def _maybe_start_pending_skill_step_task(self, ctx: AgentCallbackContext) -> None:
        if ctx.session is None:
            return

        if not self._skill_step_map:
            return

        has_in_progress = any(
            task.get("status") == "in_progress" for task in self._skill_step_map.values()
        )
        if has_in_progress:
            return

        pending_task_id = None
        pending_task = None
        for task_id, task in self._skill_step_map.items():
            if task.get("status") == "pending":
                pending_task_id = task_id
                pending_task = task
                break

        if pending_task_id is None or pending_task_id in self._skill_step_started:
            return

        parent_request_id = self._extract_request_id(ctx)
        await self._emit_task_start_event(
            ctx.session, pending_task_id, pending_task, parent_request_id, source="skill_step"
        )
        self._skill_step_started.add(pending_task_id)
        self._skill_step_map[pending_task_id]["status"] = "in_progress"

        await self._emit_task_update_event(ctx.session, parent_request_id)

    async def _start_first_uncompleted_skill_step(self, ctx: AgentCallbackContext) -> None:
        """Read skill_step.md, complete finished tasks, emit task.start for next uncompleted, bind context."""
        if ctx.session is None:
            return
        session_id = ctx.session.get_session_id()

        try:
            skill_step_items = self._load_skill_step_from_markdown(session_id)
        except Exception as exc:
            logger.warning("[TaskExecutionRail] Failed to load skill_step.md: %s", exc)
            return

        current_map = self._build_map_from_skill_step_items(skill_step_items) if skill_step_items else {}

        # [fix]保留内存中的 in_progress 状态
        for task_id, mem_task in self._skill_step_map.items():
            if mem_task.get("status") == "in_progress":
                if task_id in current_map:
                    if current_map[task_id].get("status") not in ("completed", "cancelled"):
                        current_map[task_id]["status"] = "in_progress"

        self._skill_step_map = current_map

        # Complete any active skill_step tasks that are now done or removed from file
        for full_task_id in list(self._active_tasks):
            if not full_task_id.startswith("skill_step:"):
                continue
            task_id = full_task_id.split(":", 1)[1]
            curr = current_map.get(task_id)
            if curr is None or curr.get("status") in ("completed", "cancelled"):
                context = self._active_tasks[full_task_id]
                await self._emit_task_complete_event(
                    ctx.session, task_id,
                    {"content": context.task_content, "index": context.task_index, "total": context.total_tasks},
                    status="succeeded" if curr is not None and curr.get("status") == "completed" else "skipped",
                )
        self._skill_step_started -= set(current_map.keys())

        parent_request_id = self._extract_request_id(ctx)

        # First uncompleted step: mark in_progress in memory, emit task.start (before stream chunks
        # for this step), then bind context via _emit_task_start_event. Keeps _maybe_start_pending_skill_step_task
        # from double-starting (has_in_progress).
        for task_id, task in current_map.items():
            status = task.get("status", "")
            if status not in ("completed", "cancelled"):
                self._skill_step_map[task_id]["status"] = "in_progress"
                await self._emit_task_start_event(
                    ctx.session,
                    task_id,
                    self._skill_step_map[task_id],
                    parent_request_id,
                    source="skill_step",
                )
                self._skill_step_started.add(task_id)
                logger.info(
                    "==========[TaskExecutionRail] start skill_step task: id=skill_step:%s",
                    task_id,
                )
                return

        # No skill_step.md or all tasks completed — clear skill_step context
        self._bind_context_to_in_progress_task()

    async def _sync_skill_step_and_emit_transitions(self, ctx: AgentCallbackContext) -> None:
        if ctx.session is None:
            return
        session_id = ctx.session.get_session_id()
        parent_request_id = self._extract_request_id(ctx)

        try:
            skill_step_items = self._load_skill_step_from_markdown(session_id)
        except Exception as exc:
            logger.warning("[TaskExecutionRail] Failed to load skill_step.md: %s", exc)
            return

        current_map = self._build_map_from_skill_step_items(skill_step_items)
        previous_map = self._skill_step_map_before_tool or self._skill_step_map

        for task_id, prev_task in self._skill_step_map.items():
            if prev_task.get("status") == "in_progress":
                curr_task = current_map.get(task_id)
                if curr_task and curr_task.get("status") == "pending":
                    current_map[task_id]["status"] = "in_progress"

        for task_id, current in current_map.items():
            prev = previous_map.get(task_id)
            prev_status = prev.get("status", "") if prev else ""
            curr_status = current.get("status", "")

            if curr_status == "completed" and prev_status in ("pending", "in_progress", ""):
                if task_id not in self._skill_step_started:
                    await self._emit_task_start_event(
                        ctx.session, task_id, current, parent_request_id, source="skill_step"
                    )
                    self._skill_step_started.add(task_id)
                await self._emit_task_complete_event(ctx.session, task_id, current, status="succeeded")

        self._skill_step_map = current_map
        self._skill_step_map_before_tool = {}
        self._bind_context_to_in_progress_task()

    async def _emit_task_start_event(
        self,
        session: Session,
        task_id: str,
        task: dict[str, Any],
        parent_request_id: str,
        source: Literal["todo", "skill_step"],
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
        self._current_task_id = full_task_id

        logger.info("[TaskExecutionRail] task.start: %s - %s", full_task_id, context.task_content)

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

    async def _emit_task_complete_event(
        self,
        session: Session,
        task_id: str,
        task: dict[str, Any],
        *,
        status: Literal["succeeded", "failed", "skipped"],
        error: str | None = None,
    ) -> None:
        for source in ["todo", "skill_step"]:
            full_task_id = f"{source}:{task_id}"
            context = self._active_tasks.get(full_task_id)
            if context:
                break
        else:
            return

        timestamp = time.time()
        duration_ms = int((timestamp - context.start_time) * 1000)

        if get_current_task_id() == full_task_id:
            _ACTIVE_TASK_ID.set(None)
            self._current_task_id = None

        logger.info("[TaskExecutionRail] task.complete: %s - %s (%dms)", full_task_id, status, duration_ms)

        await session.write_stream(
            OutputSchema(
                type="task.complete",
                index=0,
                payload={
                    "task_id": context.task_id,
                    "task_content": context.task_content,
                    "status": status,
                    "duration_ms": duration_ms,
                    "error": error,
                    "timestamp": timestamp,
                    "source": context.source,
                },
            )
        )
        self._active_tasks.pop(full_task_id, None)

    async def _emit_task_update_event(
        self,
        session: Session,
        parent_request_id: str | None = None,
    ) -> None:
        """发送完整任务列表更新消息。

        包含 todo.json 和 skill_step.md 中的所有任务。
        在 todo 工具调用和 skill_step 工具调用后都会发送。

        Args:
            session: Session 对象，用于发送流消息
            parent_request_id: 关联的请求ID
        """
        session_id = session.get_session_id()

        todo_items = self._load_todo_from_json(session_id)
        todo_tasks = self._format_tasks_for_update(todo_items, source="todo")

        # skill_step 任务：合并文件状态和内存状态
        skill_step_items = self._load_skill_step_from_markdown(session_id)
        
        # 合并内存状态
        for task_id, mem_task in self._skill_step_map.items():
            if mem_task.get("status") == "in_progress":
                for item in skill_step_items:
                    if str(item.get("idx", "")) == task_id:
                        item["status"] = "in_progress"
                        break
        
        skill_step_tasks = self._format_tasks_for_update(skill_step_items, source="skill_step")

        all_tasks = todo_tasks + skill_step_tasks

        total = len(all_tasks)
        completed = sum(1 for t in all_tasks if t.get("status") == "completed")
        in_progress = sum(1 for t in all_tasks if t.get("status") == "in_progress")
        pending = sum(1 for t in all_tasks if t.get("status") == "pending")

        payload = {
            "tasks": all_tasks,
            "total_tasks": total,
            "completed_tasks": completed,
            "in_progress_tasks": in_progress,
            "pending_tasks": pending,
            "timestamp": time.time(),
        }

        if parent_request_id:
            payload["parent_request_id"] = parent_request_id

        await session.write_stream(
            OutputSchema(
                type="task.update",
                index=0,
                payload=payload,
            )
        )

        logger.info(
            "[TaskExecutionRail] task.update: %d tasks (%d todo, %d skill_step) - %d "
            "completed, %d in_progress, %d pending",
            total, len(todo_tasks), len(skill_step_tasks), completed, in_progress, pending
        )

        task_summaries = []
        for task in all_tasks:
            source = task.get("source", "unknown")
            task_id = task.get("task_id", "?")
            content = task.get("task_content", "")[:30]
            status = task.get("status", "unknown")
            task_summaries.append(f"[{source}:{task_id}] {content} ({status})")

        logger.info("[TaskExecutionRail] task.update tasks: %s", ", ".join(task_summaries))

    def _format_tasks_for_update(
        self,
        items: list[dict[str, Any]],
        source: Literal["todo", "skill_step"],
    ) -> list[dict[str, Any]]:
        """格式化任务列表用于 task.update 消息。

        Args:
            items: 原始任务数据列表
            source: 任务来源标识

        Returns:
            格式化后的任务列表
        """
        formatted = []
        for item in items:
            task_id = str(item.get("id", item.get("idx", "")))
            task = {
                "task_id": task_id,
                "task_content": item.get("content", item.get("activeForm", "")),
                "task_index": item.get("index", item.get("idx", 0)),
                "source": source,
                "status": item.get("status", "pending"),
            }

            # 添加执行时间信息（如果有活跃任务上下文）
            full_task_id = f"{source}:{task_id}"
            context = self._active_tasks.get(full_task_id)
            if context:
                task["start_time"] = context.start_time

            formatted.append(task)

        return formatted

    def _bind_context_to_in_progress_task(self) -> None:
        # 优先绑定 skill_step 任务，再绑定 todo 任务
        for task_id, task in self._skill_step_map.items():
            if task.get("status") == "in_progress":
                full_task_id = f"skill_step:{task_id}"
                _ACTIVE_TASK_ID.set(full_task_id)
                self._current_task_id = full_task_id
                return

        for task_id, task in self._todo_map.items():
            if task.get("status") == "in_progress":
                full_task_id = f"todo:{task_id}"
                _ACTIVE_TASK_ID.set(full_task_id)
                self._current_task_id = full_task_id
                return

        _ACTIVE_TASK_ID.set(None)
        self._current_task_id = None

    def _get_todo_workspace_path(self, session_id: str) -> Path:
        if self.workspace is not None:
            return Path(self.workspace.get_node_path(WorkspaceNode.TODO)) / session_id / "todo.json"
        return get_agent_sessions_dir() / session_id / "todo.json"

    def _get_skill_step_workspace_path(self, session_id: str) -> Path:
        return get_agent_sessions_dir() / session_id / "skill_step.md"

    @staticmethod
    def _extract_request_id(ctx: AgentCallbackContext) -> str:
        value = getattr(ctx.inputs, "request_id", "")
        return str(value) if value else ""
