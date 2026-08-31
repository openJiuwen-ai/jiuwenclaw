# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""TaskExecutionRail — Emit task.start/task.complete/task.update lifecycle events.

Tracks todo status transitions (pending->in_progress->completed) and emits
lifecycle events to the frontend. Binds the current task_id via ContextVar
so downstream tool/artifact events can be attributed to the active task.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, NamedTuple

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

# 非产物路径黑名单
# 供 TaskExecutionRail 与 SkillTurboArtifactRail 共用
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
    # bash/powershell 大输出落盘目录（供模型查阅，非用户产物）
    re.compile(r'[/\\][^/\\]*bash_outputs[/\\]', re.IGNORECASE),
    re.compile(r'[/\\][^/\\]*powershell_outputs[/\\]', re.IGNORECASE),
]


def _is_excluded_path(path_str: str) -> bool:
    """检查路径是否应排除（非产物）。

    与 enterprise_dev 对齐：用黑名单排除已知非产物路径模式，而非按扩展名
    白名单收紧——真实交付物扩展名众多（csv/html/md 等），白名单会漏报。
    正文回退扫描另由 _ARTIFACT_PATH_PATTERNS 限定路径形态。
    """
    for pattern in _ALWAYS_EXCLUDED_PATH_PATTERNS:
        if pattern.search(path_str):
            return True
    return False

# ---------------------------------------------------------------------------
# 产物检测工具白名单（供 TaskExecutionRail 与 SkillTurboArtifactRail 共用）
# ---------------------------------------------------------------------------
# 图像生成工具（generate_image 为 jiuwenswarm 工具名）
IMAGE_TOOL_NAMES = frozenset({"generate_image"})
# write/edit 类工具：产物路径从 tool_args 或结果中提取
WRITE_TOOL_NAMES = frozenset({
    "write_file", "edit_file", "write", "write_text_file",
})
# 代码执行类工具：产物路径从 stdout/stderr 正则提取
CODE_EXEC_TOOL_NAMES = frozenset({
    "bash", "exec_command", "mcp_exec_command",
})
# invoke_tool：按需工具的间接调用入口，需解包内部工具名和结果
INVOKE_TOOL_NAMES = frozenset({"invoke_tool"})
# send_file_to_user：产物路径从 tool_args 显式提取
SEND_FILE_TOOL_NAMES = frozenset({"send_file_to_user"})

# invoke_tool 解包后的只读查询类内部工具（不产文件）跳过产物检测：
# 其大文本结果（如 evaluate_script ~800K HTML）会触发 _ARTIFACT_PATH_PATTERNS
# findall 爆炸 + 逐条 stat()，阻塞事件循环数百秒（实测 633s → WS 1006）。
READONLY_INNER_TOOLS = frozenset({
    # chrome-devtools / 浏览器自动化只读查询
    "evaluate_script", "list_pages", "get_page_content", "get_page_text",
    "snapshot", "take_snapshot", "get_console_logs", "get_cookies",
    "get_network_log", "screenshot",  # 截图产物由调用方另行处理
    # 通用只读探查
    "search_skill", "tools_search",
})
# 触发产物检测的全部工具（对齐 clowder-ai artifact_emitter 白名单思路）
ARTIFACT_DETECTION_TOOL_NAMES = frozenset(
    IMAGE_TOOL_NAMES | WRITE_TOOL_NAMES | CODE_EXEC_TOOL_NAMES
    | INVOKE_TOOL_NAMES | SEND_FILE_TOOL_NAMES
)

# mtime 校验容差（秒）：覆盖 FAT32 2 秒时间戳粒度等文件系统精度问题
_MTIME_TOLERANCE_S = 2.0

# 产物路径检测超时（秒）：防止 stat() 对不可达网络路径同步阻塞 event loop
# （公开常量：供 SkillTurboArtifactRail 跨模块复用）
ARTIFACT_DETECT_TIMEOUT_S = 2.0

# 正文回退扫描的单行最大长度
_BODY_SCAN_MAX_LINE_LEN = 8192

# 结构化提取时识别为路径字段的键名关键词
_STRUCTURED_PATH_FIELD_KEYWORDS = frozenset({
    "path", "file", "files", "output", "outputs",
    "artifact", "artifacts", "generated", "result",
})


def _is_unc_path(path_str: str) -> bool:
    """检查是否为 UNC 网络路径（\\\\host\\share），避免 stat() 同步阻塞。"""
    return path_str.startswith("\\\\") or path_str.startswith("//")

# 文件路径检测的正则表达式模式（仿 PR#1440；调用方按黑名单排除过滤）
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

# 产物路径正文回退扫描正则（宽松策略，允许空格，匹配任意绝对路径）
# 与 _FILE_PATH_PATTERNS 的区别：\s → \r\n（允许空格，仅换行截断）
# 且要求路径以扩展名结尾，避免匹配到无扩展名的目录名
_ARTIFACT_PATH_PATTERNS = [
    # Windows绝对路径，允许空格（停在换行/引号/括号等边界）
    # (?<![A-Za-z]) 排除 URL 协议：https:// 中的 s: 会被误认为盘符
    re.compile(
        r'(?<![A-Za-z])[A-Za-z]:[/\\][^\r\n\]\}\)\'\"`<>，。；、：]+'
        r'\.[a-zA-Z0-9]{1,10}'
    ),
    # Unix绝对路径，允许空格
    # (?<![:/]) 排除 URL：避免 //host 被 normpath 转为 UNC 网络路径后
    # stat() 同步阻塞 event loop（对齐问题201的22秒阻塞根因）
    re.compile(
        r'(?<![:/])/[^\r\n\]\}\)\'\"`<>，。；、：]+'
        r'\.[a-zA-Z0-9]{1,10}'
    ),
]

# 产物路径正则扫描的文本长度上限：超过直接跳过 findall，避免超大正文
# （如 evaluate_script ~800K HTML）爆炸匹配 + 逐条 stat() 阻塞事件循环。
# 64K 覆盖正常 code/bash stdout 的产物路径声明。
_ARTIFACT_SCAN_MAX_TEXT_BYTES = 64 * 1024


def _clean_path_candidate(path_str: str) -> str:
    """清理正则提取到的路径候选首尾非法字符。"""
    return path_str.strip().strip(_PATH_TRAILING_CHARS).strip()


def _iter_structured_path_values(value: Any, parent_key: str = "") -> list[str]:
    """从结构化工具结果中递归提取路径字段值。

    遍历 dict/list/tuple/set，当键名含 path/file/output/artifact 等关键词时
    取其字符串值作为路径候选。structured 提取命中后可跳过正文正则扫描。
    """
    paths: list[str] = []
    key_lower = parent_key.lower()
    key_is_path_like = any(
        keyword in key_lower for keyword in _STRUCTURED_PATH_FIELD_KEYWORDS
    )

    if isinstance(value, dict):
        for child_key, child_value in value.items():
            paths.extend(
                _iter_structured_path_values(child_value, str(child_key))
            )
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


def _scan_body_text_for_paths(
    result_text: str,
    *,
    cancel_event: threading.Event | None = None,
) -> list[str]:
    """逐行扫描正文提取路径候选。

    逐行处理避免超长单行正则灾难；单行超 _BODY_SCAN_MAX_LINE_LEN 跳过。
    """
    # 纵深防御：主防线是 detect_artifact_paths 的 READONLY_INNER_TOOLS 短路，
    # 这里兜底非 invoke_tool 通道直接走正文扫描的超大输出（633s stat 风暴）。
    if len(result_text) > _ARTIFACT_SCAN_MAX_TEXT_BYTES:
        logger.warning(
            "[TaskExecutionRail] artifact scan skipped: result text too "
            "large len=%d max=%d (super-large tool output would block "
            "event loop on stat() storm)",
            len(result_text), _ARTIFACT_SCAN_MAX_TEXT_BYTES,
        )
        return []
    candidates: list[str] = []
    seen: set[str] = set()
    for line in result_text.splitlines():
        if cancel_event is not None and cancel_event.is_set():
            break
        if len(line) > _BODY_SCAN_MAX_LINE_LEN:
            continue
        for pattern in _ARTIFACT_PATH_PATTERNS:
            for match in pattern.findall(line):
                cleaned = _clean_path_candidate(match)
                if not cleaned:
                    continue
                identity = cleaned.replace("\\", "/").lower()
                if identity in seen:
                    continue
                seen.add(identity)
                candidates.append(cleaned)
    return candidates


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


# 工具结果中承载文本输出的字段名（stdout/stderr 等，取原始文本避免 JSON 转义）
_RESULT_TEXT_KEYS = ("stdout", "stderr", "output", "result")


def _collect_result_text_fields(payload: dict[str, Any]) -> list[str]:
    """收集 dict 中 stdout/stderr/output/result 字段的字符串值。"""
    parts: list[str] = []
    for key in _RESULT_TEXT_KEYS:
        val = payload.get(key)
        if isinstance(val, str) and val:
            parts.append(val)
    return parts


def _tool_result_to_text(tool_result: Any) -> str:
    if tool_result is None:
        return ""
    if isinstance(tool_result, str):
        return tool_result
    if isinstance(tool_result, dict):
        # 优先取 stdout/stderr 等字段的原始文本；json.dumps 会把反斜杠
        # 转义成 \\、换行转义成 \n，导致路径提取错乱（跨行粘连成无效路径）
        parts = _collect_result_text_fields(tool_result)
        if parts:
            return "\n".join(parts)
        return json.dumps(tool_result, ensure_ascii=False)
    # ToolOutput (pydantic): 直接取 data 中的 stdout/stderr/output/result，
    # 避免 str() 序列化导致反斜杠转义和路径含空格被正则截断
    data = getattr(tool_result, "data", None)
    if isinstance(data, dict):
        parts = _collect_result_text_fields(data)
        if parts:
            return "\n".join(parts)
    if hasattr(tool_result, "__dict__"):
        return str(tool_result)
    return str(tool_result)


def _unwrap_invoke_tool(
    tool_args: Any, tool_result: Any
) -> tuple[str | None, Any]:
    """解包 invoke_tool 的内部工具名和结果。

    invoke_tool 的 tool_args 形如 {"tool_name": "generate_image", ...}，
    tool_result 形如 {"success": True, "tool_name": "generate_image",
    "result": "..."} 或字符串。

    返回 (inner_tool_name, inner_result)。无法解包时返回 (None, None)。
    """
    # 优先从 tool_args 获取内部工具名
    args = _parse_tool_args_payload(tool_args)
    inner_name = args.get("tool_name")
    if not isinstance(inner_name, str):
        inner_name = None

    # 从 tool_result 中提取内部结果
    if isinstance(tool_result, dict):
        # dict 结果：取 "result" 字段作为内部结果
        inner_result = tool_result.get("result")
        # 同时尝试从 result dict 补充内部工具名
        if inner_name is None:
            rn = tool_result.get("tool_name")
            if isinstance(rn, str):
                inner_name = rn
    elif isinstance(tool_result, str):
        # 字符串结果：直接作为内部结果
        inner_result = tool_result
    else:
        # ToolOutput 等对象：尝试取 data 中的 result 字段
        data = getattr(tool_result, "data", None)
        if isinstance(data, dict):
            inner_result = data.get("result")
            if inner_name is None:
                rn = data.get("tool_name")
                if isinstance(rn, str):
                    inner_name = rn
        else:
            inner_result = tool_result

    if inner_name is None:
        return None, None
    return inner_name, inner_result


def _extract_raw_paths_from_result_text(
    tool_result: Any,
) -> list[str]:
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
    """从工具输出结果中提取图像产物路径（结构化优先）。

    先从结果 dict 的 path/output/result 等键提取，命中即返回；
    未命中则回退到正文逐行宽松正则扫描，再按图像扩展名白名单过滤。
    """
    image_paths: list[str] = []
    seen: set[str] = set()

    # 1. 结构化提取优先
    result_dict: dict[str, Any] | None = None
    if isinstance(tool_result, dict):
        result_dict = tool_result
    elif hasattr(tool_result, "__dict__"):
        result_dict = tool_result.__dict__
    if result_dict is not None:
        for p in _iter_structured_path_values(result_dict):
            if Path(p).suffix.lower() in _IMAGE_ARTIFACT_EXTENSIONS:
                identity = p.replace("\\", "/").lower()
                if identity not in seen:
                    seen.add(identity)
                    image_paths.append(p)
        if image_paths:
            return image_paths

    # 2. 回退：保守正则逐行扫描
    for path in _scan_body_text_for_paths(_tool_result_to_text(tool_result)):
        if Path(path).suffix.lower() in _IMAGE_ARTIFACT_EXTENSIONS:
            identity = path.replace("\\", "/").lower()
            if identity not in seen:
                seen.add(identity)
                image_paths.append(path)
    return image_paths


def _artifact_identity(path: str) -> tuple[str, int, int] | None:
    """返回文件的 (规范化绝对路径, mtime_ns, size) 身份标识。

    stat 失败（文件不存在等）返回 None。
    """
    try:
        stat = Path(path).stat()
    except OSError:
        return None
    return (
        os.path.normcase(os.path.abspath(path)),
        stat.st_mtime_ns,
        stat.st_size,
    )


def _is_path_within(path: Path, base: Path) -> bool:
    """判断 path 是否位于 base 目录内（大小写不敏感，兼容 Windows）。"""
    try:
        resolved = str(path.resolve())
    except OSError:
        return False
    norm_resolved = os.path.normcase(resolved)
    norm_base = os.path.normcase(str(base))
    return norm_resolved == norm_base or norm_resolved.startswith(
        norm_base + os.sep
    )


def _validate_artifact_candidates(
    raw_paths: list[str],
    tool_start_time: float | None,
    workspace_base: Path | None,
    cancel_event: threading.Event | None = None,
) -> list[str]:
    """校验路径候选，返回通过全部校验的产物路径。

    校验项：UNC 过滤 + 黑名单排除 + 存在性/mtime/工作区校验 + 去重。
    """
    paths: list[str] = []
    seen: set[str] = set()
    for raw_path in raw_paths:
        if cancel_event is not None and cancel_event.is_set():
            break
        path = os.path.normpath(raw_path)
        if _is_unc_path(path):
            logger.debug("[artifact-detect] skip UNC path: %s", path)
            continue
        file_path = Path(path)
        if _is_excluded_path(path):
            continue
        try:
            stat = file_path.stat()
        except OSError:
            continue
        if (
            workspace_base is not None
            and not _is_path_within(file_path, workspace_base)
        ):
            continue
        if (
            tool_start_time is not None
            and stat.st_mtime < tool_start_time - _MTIME_TOLERANCE_S
        ):
            continue
        identity = os.path.normcase(os.path.abspath(path))
        if identity in seen:
            continue
        seen.add(identity)
        paths.append(path)
    return paths


def _extract_artifact_paths_from_result(
    tool_result: Any,
    tool_start_time: float | None = None,
    workspace_base: Path | None = None,
    *,
    cancel_event: threading.Event | None = None,
) -> list[str]:
    """从工具输出中提取产物路径（结构化优先 + 正文宽松正则回退）。

    处理流程：
    1. 结构化提取：从结果 dict 的 path/file/output/result 等键提取候选，
       候选校验通过即返回；弱键垃圾候选（如 {"result": "ok"}）校验失败后
       继续走正文回退，不屏蔽真实路径
    2. 正文回退扫描：逐行宽松正则提取（单行超 _BODY_SCAN_MAX_LINE_LEN
       跳过，正则见 _ARTIFACT_PATH_PATTERNS）
    3. 统一校验：见 _validate_artifact_candidates
    """
    # 1. 结构化提取优先
    if isinstance(tool_result, dict):
        result_dict: dict[str, Any] | None = tool_result
    elif hasattr(tool_result, "__dict__"):
        result_dict = tool_result.__dict__
    else:
        result_dict = None
    if result_dict is not None:
        paths = _validate_artifact_candidates(
            _iter_structured_path_values(result_dict),
            tool_start_time,
            workspace_base,
            cancel_event,
        )
        if paths:
            return paths

    # 2. 结构化未命中则回退到正文逐行扫描
    result_text = _tool_result_to_text(tool_result)
    if not result_text:
        return []
    return _validate_artifact_candidates(
        _scan_body_text_for_paths(result_text, cancel_event=cancel_event),
        tool_start_time,
        workspace_base,
        cancel_event,
    )


class ArtifactDetection(NamedTuple):
    """统一产物检测结果。

    tool_name 为解包后的有效工具名（invoke_tool 间接调用时为内部工具名，
    如 generate_image），供 hook 上下文与日志归因使用。
    """

    tool_name: str
    paths: list[str]


def detect_artifact_paths(
    tool_name: str,
    tool_args: Any,
    tool_result: Any,
    *,
    tool_start_time: float | None = None,
    workspace_base: Path | None = None,
    cancel_event: threading.Event | None = None,
) -> ArtifactDetection:
    """统一产物检测入口，供 TaskExecutionRail / SkillTurboArtifactRail 共用。

    处理流程：
    1. invoke_tool 解包（内部工具名 + 内部结果替换外层值）
    2. 按工具类型分提取策略：
       - 图像工具：专用提取（返回权威路径，不做 mtime/工作区校验）
       - write 类工具：从 tool_args / 结果中提取
       - send_file_to_user：从 tool_args 显式提取
       - 代码执行类：正则提取 + 黑名单/mtime/工作区过滤
    3. 统一出口：仅保留实际存在的文件（write/send_file 提取的路径
       可能并不存在，需过滤后再触发 hook）
    """
    # invoke_tool：解包内部工具名和结果，按内部工具类型分提取策略
    if tool_name in INVOKE_TOOL_NAMES:
        inner_name, inner_result = _unwrap_invoke_tool(tool_args, tool_result)
        if inner_name is None:
            return ArtifactDetection(tool_name, [])
        # 只读内部工具不产文件，短路避免 633s stat 风暴（见 READONLY_INNER_TOOLS）。
        if inner_name in READONLY_INNER_TOOLS:
            return ArtifactDetection(inner_name, [])
        tool_name = inner_name
        tool_result = inner_result
        # 内部工具参数位于 invoke_tool 的 arguments 字段中
        payload = _parse_tool_args_payload(tool_args)
        inner_args = payload.get("arguments")
        tool_args = inner_args if inner_args is not None else {}

    paths: list[str] = []

    if tool_name in IMAGE_TOOL_NAMES:
        # 图像工具返回权威路径，不做 mtime/工作区校验
        paths = _extract_image_paths_from_tool_result(tool_result)
    elif tool_name in WRITE_TOOL_NAMES:
        # write 类工具：维持原有提取逻辑
        paths = _extract_file_paths_from_write_tool(
            tool_name, tool_args, tool_result
        )
    elif tool_name in SEND_FILE_TOOL_NAMES:
        # send_file_to_user：从 tool_args 提取显式路径
        payload = _parse_tool_args_payload(tool_args)
        for key in ("path", "file_path", "file", "abs_file_path"):
            value = str(payload.get(key) or "").strip()
            if value:
                paths.append(value)
        raw_list = payload.get("abs_file_path_list")
        if isinstance(raw_list, list):
            paths.extend(str(p).strip() for p in raw_list if str(p).strip())
        paths = list(dict.fromkeys(paths))
    else:
        # code / bash / mcp_exec_command：统一提取（含黑名单/mtime/工作区过滤）
        paths = _extract_artifact_paths_from_result(
            tool_result,
            tool_start_time=tool_start_time,
            workspace_base=workspace_base,
            cancel_event=cancel_event,
        )

    # 统一出口：仅保留实际存在的文件（跳过 UNC 网络路径避免同步阻塞）
    paths = [p for p in paths if not _is_unc_path(p) and Path(p).exists()]
    return ArtifactDetection(tool_name, paths)


def resolve_workspace_base() -> Path | None:
    """解析请求级工作区根目录，用于产物路径范围校验。"""
    try:
        from jiuwenswarm.agents.harness.common.tools.subagent_executor.context_vars import (
            get_effective_request_workspace_dir,
        )

        epd = get_effective_request_workspace_dir()
        if epd:
            return Path(epd).resolve()
    except ImportError:
        logger.debug(
            "[artifact-detect] context_vars unavailable, "
            "falling back to default workspace resolution",
            exc_info=True,
        )
    except Exception as exc:
        logger.debug(
            "[artifact-detect] Failed to get effective_request_workspace_dir: %s",
            exc,
        )
    return None


def pop_tool_start_time(
    start_times: dict[str, float], ctx: AgentCallbackContext
) -> float | None:
    """取出本次工具调用的开始时间（before_tool_call 时按 tool_call_id 记录）。"""
    tc = getattr(ctx.inputs, "tool_call", None)
    tool_call_id = str(getattr(tc, "id", "") or "")
    if not tool_call_id:
        return None
    return start_times.pop(tool_call_id, None)


async def detect_artifact_paths_safe(
    ctx: AgentCallbackContext,
    session_id: str,
    tool_start_time: float | None,
    *,
    log_prefix: str,
) -> ArtifactDetection | None:
    """线程中执行产物检测并加超时保护，超时/异常时返回 None（跳过检测）。

    将同步的 detect_artifact_paths 移到线程中执行，避免 stat() 对不可达
    网络路径同步阻塞 event loop。
    供 TaskExecutionRail 与 SkillTurboArtifactRail 共用；调用方需保证
    ctx.inputs 为 ToolCallInputs。
    """
    cancel_event = threading.Event()
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(
                detect_artifact_paths,
                ctx.inputs.tool_name,
                getattr(ctx.inputs, "tool_args", None),
                getattr(ctx.inputs, "tool_result", None),
                tool_start_time=tool_start_time,
                workspace_base=resolve_workspace_base(),
                cancel_event=cancel_event,
            ),
            timeout=ARTIFACT_DETECT_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        # 超时后 set 通知后台线程在下一个检查点退出（stat 卡住时线程
        # 自行结束，不影响 event loop）
        cancel_event.set()
        logger.warning(
            "%s artifact detection timed out (%.1fs), skipping "
            "session_id=%s tool=%s",
            log_prefix,
            ARTIFACT_DETECT_TIMEOUT_S,
            session_id,
            ctx.inputs.tool_name,
        )
        return None
    except Exception as exc:
        # 检测失败不应打断 rail 回调链，记录后跳过本工具的产物检测
        logger.warning(
            "%s artifact detection failed, skipping session_id=%s "
            "tool=%s error=%s",
            log_prefix,
            session_id,
            ctx.inputs.tool_name,
            exc,
            exc_info=True,
        )
        return None


def filter_unhooked(
    paths: list[str], hooked: set[tuple[str, int, int]]
) -> list[str]:
    """过滤已触发过 hook 且内容未变化的文件（防重复后处理，如水印叠盖）。"""
    result: list[str] = []
    for path in paths:
        identity = _artifact_identity(path)
        if identity is not None and identity in hooked:
            continue
        result.append(path)
    return result


def mark_hooked(
    paths: list[str], hooked: set[tuple[str, int, int]]
) -> None:
    """记录已触发 hook 的文件身份（hook 可能原地改写文件，故事后重新 stat）。"""
    for path in paths:
        identity = _artifact_identity(path)
        if identity is not None:
            hooked.add(identity)


async def fire_artifact_hook(
    session_id: str,
    tool_name: str,
    task_id: str | None,
    artifact_paths: list[str],
    *,
    log_prefix: str,
) -> bool:
    """触发产物后处理扩展 hook，返回是否成功触发。

    对同一批产物同时触发 IMAGE_ARTIFACT_POST_PROCESS 和
    ARTIFACT_POST_PROCESS，扩展在 handler 内部按扩展名自行过滤
    （如加水印、.py Unicode 归一化）。跳过（Registry 未初始化 / import
    失败）或扩展抛错返回 False，调用方据此决定是否记录去重身份，避免
    失败后永久跳过该文件。
    """
    try:
        from jiuwenswarm.extensions.registry import ExtensionRegistry
        from jiuwenswarm.extensions.hook_event import (
            AgentServerHookEvents,
        )
        from jiuwenswarm.extensions.hooks_context import (
            ArtifactPostProcessHookContext,
            ImageArtifactHookContext,
        )
    except ImportError as exc:
        logger.warning(
            "%s skip artifact post-process hook, import failed: %s",
            log_prefix, exc,
        )
        return False

    registry = ExtensionRegistry.get_instance()

    # 同时触发两个事件，扩展各自按扩展名过滤
    for event_name, ctx_cls in (
        (AgentServerHookEvents.IMAGE_ARTIFACT_POST_PROCESS, ImageArtifactHookContext),
        (AgentServerHookEvents.ARTIFACT_POST_PROCESS, ArtifactPostProcessHookContext),
    ):
        hook_ctx = ctx_cls(
            session_id=session_id,
            tool_name=tool_name,
            task_id=task_id,
            artifact_paths=artifact_paths,
        )
        try:
            await registry.trigger(event_name, hook_ctx)
        except RuntimeError:
            logger.warning(
                "%s skip %s: ExtensionRegistry not initialized",
                log_prefix, event_name,
            )
            return False
        except Exception as exc:
            logger.warning(
                "%s %s failed session_id=%s tool=%s error=%s",
                log_prefix, event_name, session_id, tool_name, exc,
            )
            return False

    logger.info(
        "%s artifact hooks done session_id=%s tool=%s count=%d",
        log_prefix,
        session_id,
        tool_name,
        len(artifact_paths),
    )
    return True


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
    # 触发产物后处理 hook 的工具（共享常量，见模块级定义）
    ARTIFACT_DETECTION_TOOLS = ARTIFACT_DETECTION_TOOL_NAMES

    def __init__(self) -> None:
        super().__init__()
        self._todo_map: dict[str, dict[str, Any]] = {}
        self._todo_map_before_tool: dict[str, dict[str, Any]] = {}
        self._active_tasks: dict[str, TaskExecutionContext] = {}
        self._todo_started: set[str] = set()
        self._deep_agent: Any | None = None
        # 产物检测：工具调用开始时间（按 tool_call_id 记录，用于 mtime 校验）
        self._tool_start_times: dict[str, float] = {}
        # 已触发过产物 hook 的文件身份（路径+mtime_ns+size），防止重复后处理
        self._hooked_artifacts: set[tuple[str, int, int]] = set()

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
        self._tool_start_times = {}
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
            return

        if tool_name in self.ARTIFACT_DETECTION_TOOLS:
            # 记录工具开始时间，供 after_tool_call 做 mtime 校验
            tc = ctx.inputs.tool_call
            tool_call_id = str(getattr(tc, "id", "") or "")
            if tool_call_id:
                self._tool_start_times[tool_call_id] = time.time()

        self._bind_context_to_in_progress_task()

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        if not isinstance(ctx.inputs, ToolCallInputs):
            return
        tool_name = ctx.inputs.tool_name

        if tool_name in self.TODO_TOOLS:
            await self._sync_todo_and_emit_transitions(ctx)
            return

        await self._auto_advance_pending_to_in_progress(ctx)
        # todo_create 可能已把首项写成 in_progress，但故意未发 task.start；
        # 首个工作工具再懒启动，避免「只建待办 + 回复用户」时前端任务栈吞掉正文。
        await self._lazy_start_in_progress_todo_on_work_tool(ctx)

        if tool_name in self.ARTIFACT_DETECTION_TOOLS:
            await self._trigger_artifact_hooks(ctx)
            return

    async def _auto_advance_pending_to_in_progress(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Auto-advance the current pending task to in_progress when a work tool is called.

        When the LLM calls a non-todo work tool and the bound active task is still
        ``pending``, we automatically transition it to ``in_progress`` — no
        ``todo_modify`` call needed.  This eliminates todo-only LLM rounds that
        would otherwise be spent just flipping status from pending to in_progress.
        """
        active_id = _ACTIVE_TASK_ID.get()
        if not active_id:
            return
        raw_id = active_id.removeprefix("todo:")
        task = self._todo_map.get(raw_id)
        if not task or task.get("status") not in self._BINDING_PENDING:
            return

        session = ctx.session
        if session is None:
            return

        session_id = session.get_session_id()
        parent_request_id = self._extract_request_id(ctx)

        todo_path = self._get_todo_workspace_path(session_id)
        if todo_path is None or not todo_path.exists():
            return

        try:
            with open(todo_path, "r", encoding="utf-8") as f:
                items = json.load(f)
            if not isinstance(items, list):
                return
        except (OSError, ValueError):
            return

        changed = False
        for item in items:
            if item.get("id") == raw_id and str(
                item.get("status", "pending")
            ).lower() in self._BINDING_PENDING:
                item["status"] = "in_progress"
                changed = True
                break

        if not changed:
            return

        try:
            with open(todo_path, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
        except OSError:
            logger.warning(
                "[TaskExecutionRail] auto_advance: failed to write "
                "todo.json session_id=%s task_id=%s",
                session_id,
                raw_id,
            )
            return

        if raw_id not in self._todo_started:
            task["status"] = "in_progress"
            await self._emit_task_start_event(
                session, raw_id, task, parent_request_id, source="todo",
            )
            self._todo_started.add(raw_id)

        self._todo_map[raw_id]["status"] = "in_progress"
        _ACTIVE_TASK_ID.set(f"todo:{raw_id}")

        await self._emit_task_update_event(session, parent_request_id)

        logger.info(
            "[TaskExecutionRail] auto_advance: pending→in_progress "
            "session_id=%s task_id=%s",
            session_id,
            raw_id,
        )

    async def _lazy_start_in_progress_todo_on_work_tool(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Emit deferred ``task.start`` when work begins on an in_progress todo.

        ``todo_create`` may mark the first item ``in_progress`` without emitting
        ``task.start`` (see ``_sync_todo_and_emit_transitions``). The first
        non-todo work tool must open the UI task segment so subsequent tool /
        progress events still attach correctly.
        """
        session = ctx.session
        if session is None:
            return
        self._bind_context_to_in_progress_task()
        active_id = _ACTIVE_TASK_ID.get()
        if not active_id or not active_id.startswith("todo:"):
            return
        raw_id = active_id.removeprefix("todo:")
        task = self._todo_map.get(raw_id)
        if not task or str(task.get("status", "")).lower() != "in_progress":
            return
        if raw_id in self._todo_started:
            return
        parent_request_id = self._extract_request_id(ctx)
        await self._emit_task_start_event(
            session, raw_id, task, parent_request_id, source="todo",
        )
        self._todo_started.add(raw_id)
        await self._emit_task_update_event(session, parent_request_id)
        logger.info(
            "[TaskExecutionRail] lazy_start: in_progress todo opened on work tool "
            "task_id=%s",
            raw_id,
        )

    async def _trigger_artifact_hooks(
        self, ctx: AgentCallbackContext
    ) -> None:
        """产物落盘后触发扩展 hook。

        同时触发 IMAGE_ARTIFACT_POST_PROCESS 和 ARTIFACT_POST_PROCESS，
        扩展在 handler 内按扩展名自行过滤（如加水印、.py Unicode 归一化）。
        已触发过 hook 且内容未变化的文件会被跳过，防止重复后处理
        （如水印叠盖）。ExtensionRegistry 未初始化或扩展抛错时仅记
        warning，不阻断主流程。
        """
        session = ctx.session
        if session is None:
            return
        try:
            session_id = session.get_session_id()
        except Exception:
            logger.debug(
                "[TaskExecutionRail] artifact hooks: "
                "failed to get session_id",
                exc_info=True,
            )
            return

        # 线程 + 超时 + 异常兜底执行产物检测，避免阻塞 event loop
        detection = await detect_artifact_paths_safe(
            ctx,
            session_id,
            pop_tool_start_time(self._tool_start_times, ctx),
            log_prefix="[TaskExecutionRail]",
        )
        if detection is None:
            return
        task_id = _ACTIVE_TASK_ID.get()

        # 去重：跳过已 hook 过且内容未变化的文件
        paths = filter_unhooked(detection.paths, self._hooked_artifacts)

        if not paths:
            return

        fired = await fire_artifact_hook(
            session_id=session_id,
            tool_name=detection.tool_name,
            task_id=task_id,
            artifact_paths=paths,
            log_prefix="[TaskExecutionRail]",
        )
        if fired:
            mark_hooked(paths, self._hooked_artifacts)

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
    # State transition detection + event emission
    # ------------------------------------------------------------------

    async def _sync_todo_and_emit_transitions(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Diff todo state before vs after a todo tool call and emit events.

        - pending -> in_progress  => task.start
          (except ``todo_create``: defer start until a work tool — see below)
        - in_progress -> completed => task.complete
        - pending -> completed (skipped in_progress) => task.start then
          task.complete. Frontend hidePending hides pending rows, and the
          left task list falls back to task.start segments while streaming;
          without start/complete the stage is invisible until the frozen
          completed snapshot appears after the run finishes.
        Always emits task.update (full snapshot) at the end.

        ``todo_create`` always marks the first item ``in_progress`` and tells
        the model to execute immediately. Emitting ``task.start`` at create
        time forces RelayClaw/frontends to treat the following user-facing
        reply as task-scoped text (and dual-write thinking), so create-only
        reminder flows show empty main bubbles. Defer ``task.start`` for
        create; ``todo_modify`` / work-tool lazy start still open the segment.
        """
        if ctx.session is None:
            return
        session_id = ctx.session.get_session_id()
        parent_request_id = self._extract_request_id(ctx)
        # Duck-type tool_name: production uses ToolCallInputs; tests may pass
        # SimpleNamespace. Missing attr → treat as non-create (emit start).
        tool_name = str(getattr(ctx.inputs, "tool_name", "") or "")
        defer_start_on_create = tool_name == "todo_create"

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
                if defer_start_on_create:
                    logger.info(
                        "[TaskExecutionRail] todo_create: defer task.start "
                        "for in_progress task_id=%s session_id=%s",
                        task_id,
                        session_id,
                    )
                elif task_id not in self._todo_started:
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
                # Include deferred todo_create in_progress (never emitted start):
                # frontend still needs start+complete for a visible completed row.
                if task_id not in self._todo_started:
                    logger.info(
                        "[TaskExecutionRail] completed without prior start: "
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
