# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Memory tools for JiuWenClaw - Using @tool decorator for openjiuwen."""

import asyncio
import contextvars
import os
import re
from typing import Optional, Dict, Any, List

from openjiuwen.core.foundation.tool.tool import tool

from jiuwenclaw.agentserver.reload_result import memory_cache_fingerprint
from jiuwenclaw.config import get_config
from jiuwenclaw.utils import logger

from ..memory import (
    MemoryIndexManager,
    MemoryWikiManager,
    MemorySettings,
    WikiMemorySettings,
    create_memory_settings,
    create_wiki_memory_settings,
    is_memory_enabled,
    get_memory_mode,
    DEFAULT_WORKSPACE_DIR,
    get_bound_memory_cache_fingerprint,
)
from ..memory.external_memory_config import is_builtin_memory_allowed

_GROUP_CHAT_MODE: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "group_chat_mode", default=False,
)


def set_group_chat_mode(enabled: bool) -> contextvars.Token:
    return _GROUP_CHAT_MODE.set(enabled)


def is_group_chat_mode() -> bool:
    return _GROUP_CHAT_MODE.get()


_default_agent_id: str = "default"


def _resolve_workspace_dir(workspace_dir: str | None = None) -> str:
    if workspace_dir and workspace_dir != ".":
        return workspace_dir
    from jiuwenclaw.utils import get_agent_workspace_dir
    return str(get_agent_workspace_dir())


def _resolve_embed_fingerprint(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    bound = get_bound_memory_cache_fingerprint()
    if bound:
        return bound
    return memory_cache_fingerprint(get_config())


async def _get_memory_manager(
    *,
    workspace_dir: str | None = None,
    agent_id: str = "default",
    memory_mode: str | None = None,
    embed_fingerprint: str | None = None,
) -> Optional[MemoryIndexManager | MemoryWikiManager]:
    if not is_builtin_memory_allowed():
        return None

    mode = memory_mode or get_memory_mode()
    if not is_memory_enabled(mode):
        return None

    resolved_workspace = _resolve_workspace_dir(workspace_dir)
    fp = _resolve_embed_fingerprint(embed_fingerprint)
    settings = create_memory_settings(resolved_workspace)

    try:
        if mode == "wiki":
            wiki_settings = create_wiki_memory_settings()
            return await MemoryWikiManager.get(
                agent_id=agent_id,
                workspace_dir=resolved_workspace,
                settings=settings,
                embed_fingerprint=fp,
                max_iterations=wiki_settings.max_iterations,
                query_timeout_s=wiki_settings.query_timeout_s,
                language=wiki_settings.language,
            )
        return await MemoryIndexManager.get(
            agent_id=agent_id,
            workspace_dir=resolved_workspace,
            settings=settings,
            embed_fingerprint=fp,
        )
    except Exception as e:
        logger.error("[MemoryTools] Failed to resolve memory manager: %s", e, exc_info=True)
        return None


def set_global_memory_manager(
    manager: Optional[MemoryIndexManager | MemoryWikiManager],
    workspace_dir: str = ".",
    settings: Optional[MemorySettings] = None,
    agent_id: str = "default",
):
    """Deprecated: memory managers are fingerprint-scoped; kept for test compatibility."""
    del manager, workspace_dir, settings, agent_id


def reset_global_memory_manager(
    *,
    agent_id: str | None = None,
    workspace_dir: str | None = None,
) -> None:
    """Deprecated no-op; use cache_registry release helpers instead."""
    del agent_id, workspace_dir


def _is_path_traversal_attempt(normalized: str) -> bool:
    """Check if path contains directory traversal patterns.
    
    Args:
        normalized: Normalized path with forward slashes
    
    Returns:
        True if path traversal is detected
    """
    if ".." in normalized:
        return True
    if normalized.startswith("/"):
        return True
    if len(normalized) >= 2 and normalized[1] == ":":
        return True
    return False


def _validate_memory_path(path: str) -> tuple[bool, str]:
    """Validate that path is within memory directory.
    
    Only allows:
    - memory/YYYY-MM-DD.md (date format files)
    - memory/USER.md
    - memory/MEMORY.md
    
    Returns:
        (is_valid, resolved_path_or_error)
    """
    normalized = path.replace("\\", "/")
    if _is_path_traversal_attempt(normalized):
        return (False, "Invalid path: directory traversal not allowed")
    
    if path in ("memory/USER.md", "memory/MEMORY.md"):
        return (True, path)
    
    if path.startswith("memory/"):
        filename = path[7:]
        if re.match(r"^\d{4}-\d{2}-\d{2}\.md$", filename):
            return (True, path)
    
    return (False, f"Path must be memory/YYYY-MM-DD.md, memory/USER.md, or memory/MEMORY.md. Got: {path}")


async def init_memory_manager_async(
    workspace_dir: str = ".",
    agent_id: str = "default",
    memory_mode: str = "plan",
    embed_fingerprint: str | None = None,
) -> Optional[MemoryIndexManager | MemoryWikiManager]:
    """Initialize memory manager for the given workspace and config fingerprint."""
    if not is_builtin_memory_allowed():
        logger.info("Memory system is disabled (engine gate)")
        return None

    if not is_memory_enabled(memory_mode):
        logger.info("[MemoryTools] Memory system is disabled")
        return None

    manager = await _get_memory_manager(
        workspace_dir=workspace_dir,
        agent_id=agent_id,
        memory_mode=memory_mode,
        embed_fingerprint=embed_fingerprint,
    )
    if manager:
        logger.info(
            "[MemoryTools] Memory manager initialized (%s) for: %s fp=%s",
            memory_mode,
            workspace_dir,
            embed_fingerprint or _resolve_embed_fingerprint(),
        )
    return manager


@tool(
    name="memory_search",
    description="在长期记忆系统中搜索用户的记忆信息。在回答关于之前的工作内容、决策、日期、人物、偏好或待办事项的问题之前，必须先调用此工具。",
)
async def memory_search(
    query: str,
    maxResults: Optional[int] = None,
    minScore: Optional[float] = None,
    sessionKey: Optional[str] = None
) -> Dict[str, Any]:
    """在长期记忆系统中搜索用户的记忆信息。在回答关于之前的工作内容、决策、日期、人物、偏好或待办事项的问题之前，必须先调用此工具。

    Args:
        query: 搜索查询内容
        maxResults: 最大返回结果数量 (1-50)
        minScore: 最小相关性分数 (0-1)
        sessionKey: 可选的会话键

    Returns:
        搜索结果字典，包含 results 列表
    """
    manager = await _get_memory_manager()
    if not manager:
        return {
            "results": [],
            "disabled": True,
            "error": "Memory manager not available"
        }

    try:
        opts = {}
        if maxResults is not None:
            opts["maxResults"] = maxResults
        if minScore is not None:
            opts["minScore"] = minScore
        if sessionKey is not None:
            opts["sessionKey"] = sessionKey

        results = await manager.search(query, opts=opts if opts else None)

        for r in results:
            if r["startLine"] == r["endLine"]:
                r["citation"] = f"{r['path']}#L{r['startLine']}"
            else:
                r["citation"] = f"{r['path']}#L{r['startLine']}-L{r['endLine']}"

        status = manager.status()
        
        return {
            "results": results,
            "provider": status.get("provider"),
            "model": status.get("model"),
            "disabled": False
        }
        
    except Exception as e:
        logger.error(f"[MemoryTools] Memory search failed: {e}")
        return {
            "results": [],
            "disabled": True,
            "error": str(e)
        }


@tool(
    name="memory_index",
    description="在写入或编辑每日记忆文件（memory/daily_memory/YYYY-MM-DD.md）后调用此工具，对该文件执行记忆索引。\
        注意：只能索引每日记忆文件（格式为 YYYY-MM-DD.md），USER.md 和 MEMORY.md 不需要索引，它们已经直接加载在上下文中。",
)
async def memory_index(
    path: str,
) -> Dict[str, Any]:
    """在写入或编辑每日记忆文件后调用此工具，对该文件执行记忆索引。
    只能索引每日记忆文件（memory/daily_memory/YYYY-MM-DD.md 格式）。USER.md 和 MEMORY.md 不需要索引。

    Args:
        path: 刚被修改的每日记忆文件路径（格式必须为 memory/daily_memory/YYYY-MM-DD.md，如 memory/daily_memory/2026-05-14.md）

    Returns:
        操作结果字典
    """
    if not is_builtin_memory_allowed():
        return {"success": False, "error": "记忆系统已禁用"}
    try:
        manager = await _get_memory_manager()

        if not manager:
            mem_mode = get_memory_mode()
            return {
                "success": False,
                "error": (
                    f"记忆管理器未初始化（当前模式: {mem_mode}）。"
                    "可能原因：1) LLM 模型配置不完整（wiki 模式需要 models.default 配置）；"
                    "2) 记忆功能未启用（检查 modes 配置中 memory.enabled 是否为 true）；"
                    "3) MEMORY_ENGINE 环境变量设为 none。"
                ),
            }

        if isinstance(manager, MemoryIndexManager):
            manager.dirty = True
            try:
                await manager.sync(reason="memory_index_tool")
            except Exception as sync_err:
                logger.warning(f"[MemoryTools] local mode sync failed: {sync_err}")
            return {
                "success": True,
                "path": path,
                "message": "local 模式下已触发同步索引，文件变更将被自动处理。",
            }

        if not hasattr(manager, 'notify_change'):
            return {
                "success": False,
                "error": "记忆管理器不支持索引操作（缺少 notify_change 方法）。",
            }

        normalized = path.replace("\\", "/")
        if ".." in normalized or normalized.startswith("/"):
            return {
                "success": False,
                "path": path,
                "error": "Invalid path: directory traversal not allowed",
            }

        workspace_dir = _resolve_workspace_dir()
        if not os.path.isabs(path):
            abs_path = os.path.join(workspace_dir, path)
        else:
            abs_path = path

        if not os.path.isfile(abs_path):
            return {
                "success": False,
                "path": path,
                "error": f"File not found: {path}",
            }

        basename = os.path.basename(abs_path)
        if not re.match(r'^\d{4}-\d{2}-\d{2}\.md$', basename):
            return {
                "success": False,
                "path": path,
                "error": (
                    f"不支持索引文件 {basename}。"
                    "只能索引每日记忆文件（格式为 YYYY-MM-DD.md，如 2026-05-14.md）。"
                    "USER.md 和 MEMORY.md 已经直接加载在上下文中，不需要索引。"
                ),
            }

        try:
            result = await manager.notify_change(abs_path)
        except Exception as e:
            logger.warning(f"[MemoryTools] notify_change failed for {path}: {e}")
            return {
                "success": False,
                "path": path,
                "error": str(e),
            }

        if not result.get("success"):
            return {
                "success": False,
                "path": path,
                "error": result.get("error", "Unknown error"),
            }

        status = result.get("status", "indexed")
        if status == "unchanged":
            return {
                "success": True,
                "path": path,
                "message": "文件内容未变化，无需重新索引。",
            }

        if status == "queued":
            return {
                "success": True,
                "path": path,
                "message": "索引任务已提交，正在后台异步建立索引。",
            }

        return {
            "success": True,
            "path": path,
            "message": "索引完成，该记忆文件已成功建立索引。",
        }

    except Exception as e:
        logger.error(f"[MemoryTools] memory_index failed: {e}")
        return {
            "success": False,
            "path": path,
            "error": str(e),
        }


@tool
async def memory_get(
    path: str,
    from_line: Optional[int] = None,
    lines: Optional[int] = None
) -> Dict[str, Any]:
    """安全地读取 memory/*.md 文件的指定行。在 memory_search 之后使用，只读取需要的行，保持上下文简洁。

    Args:
        path: 文件路径 (相对于工作区)
        from_line: 起始行号 (从1开始)
        lines: 读取的行数

    Returns:
        文件内容字典
    """
    manager = await _get_memory_manager()
    if not manager:
        return {
            "path": path,
            "text": "",
            "disabled": True,
            "error": "Memory manager not available"
        }

    try:
        result = await manager.read_file(
            rel_path=path,
            from_line=from_line,
            lines=lines
        )
        return {
            **result,
            "disabled": False
        }
        
    except Exception as e:
        logger.error(f"[MemoryTools] Memory get failed: {e}")
        return {
            "path": path,
            "text": "",
            "disabled": True,
            "error": str(e)
        }


@tool
async def write_memory(
    path: str,
    content: str,
    append: bool = False
) -> Dict[str, Any]:
    """在 memory 目录下创建或更新记忆文件。仅用于写入记忆相关内容，如 memory/USER.md、memory/MEMORY.md 或 memory/*.md 文件。
    禁止用于创建代码文件、配置文件或其他非记忆类文件。

    Args:
        path: 文件路径，仅允许 memory/ 目录下的文件（如 "memory/xxx.md" 或 "memory/USER.md"）
        content: 要写入的内容
        append: 是否追加模式 (默认覆盖)

    Returns:
        操作结果字典
    """
    if not is_builtin_memory_allowed():
        return {"success": False, "error": "记忆系统已禁用"}
    if is_group_chat_mode():
        return {"success": False, "error": "群聊模式下禁止写入记忆文件"}
    try:
        is_valid, result = _validate_memory_path(path)
        if not is_valid:
            return {
                "success": False,
                "path": path,
                "error": result
            }
        
        resolved_path = result
        full_path = os.path.join(_resolve_workspace_dir(), resolved_path)
        
        parent_dir = os.path.dirname(full_path)
        if parent_dir:
            await asyncio.to_thread(os.makedirs, parent_dir, exist_ok=True)
        
        file_existed = await asyncio.to_thread(os.path.exists, full_path)
        
        mode = "a" if append else "w"
        
        def _write_file():
            with open(full_path, mode, encoding="utf-8") as f:
                f.write(content)
                f.write("\n")
        
        await asyncio.to_thread(_write_file)

        logger.info(f"{'Appended to' if append else 'Wrote'} file: {resolved_path}", extra={'user_visible': 'critical'})

        return {
            "success": True,
            "path": resolved_path,
            "fullPath": full_path,
            "appended": append,
            "fileExisted": file_existed
        }
        
    except Exception as e:
        logger.error(f"[MemoryTools] Write failed: {e}")
        return {
            "success": False,
            "path": path,
            "error": str(e)
        }


@tool
async def edit_memory(
    path: str,
    oldText: str,
    newText: str
) -> Dict[str, Any]:
    """精确编辑 memory 目录下的文件内容。仅用于更新记忆文件（如 memory/USER.md、memory/MEMORY.md）。
    oldText 必须完全匹配文件中的内容。如果 oldText 出现多次，需要更具体地指定。

    Args:
        path: 文件路径，仅允许 memory/ 目录下的文件
        oldText: 要查找的文本 (必须完全匹配)
        newText: 替换的文本

    Returns:
        操作结果字典
    """
    if not is_builtin_memory_allowed():
        return {"success": False, "error": "记忆系统已禁用"}
    if is_group_chat_mode():
        return {"success": False, "error": "群聊模式下禁止编辑记忆文件"}
    try:
        is_valid, result = _validate_memory_path(path)
        if not is_valid:
            return {
                "success": False,
                "path": path,
                "error": result
            }
        
        resolved_path = result
        full_path = os.path.join(_resolve_workspace_dir(), resolved_path)
        
        if not await asyncio.to_thread(os.path.exists, full_path):
            return {
                "success": False,
                "path": path,
                "error": f"File not found: {path}"
            }
        
        def _read_content():
            with open(full_path, "r", encoding="utf-8") as f:
                return f.read()
        
        content = await asyncio.to_thread(_read_content)
        
        if oldText not in content:
            return {
                "success": False,
                "path": path,
                "error": "oldText not found in file. Use read_memory tool to check exact content."
            }
        
        occurrences = content.count(oldText)
        if occurrences > 1:
            return {
                "success": False,
                "path": path,
                "error": f"oldText appears {occurrences} times in file. Be more specific."
            }
        
        new_content = content.replace(oldText, newText, 1)

        def _write_content():
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(new_content)
                f.write("\n")

        await asyncio.to_thread(_write_content)

        logger.info(f"Edited file: {resolved_path}", extra={'user_visible': 'critical'})

        return {
            "success": True,
            "path": resolved_path,
            "replaced": oldText,
            "with": newText
        }
        
    except Exception as e:
        logger.error(f"[MemoryTools] Edit failed: {e}")
        return {
            "success": False,
            "path": path,
            "error": str(e)
        }


@tool
async def read_memory(
    path: str,
    offset: Optional[int] = None,
    limit: Optional[int] = None
) -> Dict[str, Any]:
    """读取 memory 目录下的文件内容。仅用于读取记忆文件（如 memory/USER.md、memory/MEMORY.md 或 memory/*.md）。

    Args:
        path: 文件路径，仅允许 memory/ 目录下的文件
        offset: 起始行号 (从1开始)
        limit: 读取的行数

    Returns:
        文件内容字典
    """
    if not is_builtin_memory_allowed():
        return {"success": False, "path": path, "content": "", "error": "记忆系统已禁用"}
    try:
        is_valid, result = _validate_memory_path(path)
        if not is_valid:
            return {
                "success": False,
                "path": path,
                "content": "",
                "error": result
            }
        
        resolved_path = result
        full_path = os.path.join(_resolve_workspace_dir(), resolved_path)
        
        if not await asyncio.to_thread(os.path.exists, full_path):
            return {
                "success": False,
                "path": path,
                "content": "",
                "error": f"File not found: {path}"
            }
        
        if not await asyncio.to_thread(os.path.isfile, full_path):
            return {
                "success": False,
                "path": path,
                "content": "",
                "error": f"Not a file: {path}"
            }
        
        def _read_lines():
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                return f.readlines()
        
        lines = await asyncio.to_thread(_read_lines)
        
        total_lines = len(lines)
        
        if offset is not None:
            start = max(0, offset - 1)
        else:
            start = 0
        
        if limit is not None:
            end = min(start + limit, total_lines)
        else:
            end = total_lines
        
        selected_lines = lines[start:end]
        content = "".join(selected_lines)
        
        return {
            "success": True,
            "path": resolved_path,
            "content": content,
            "totalLines": total_lines,
            "startLine": start + 1,
            "endLine": end,
            "truncated": limit is not None and end < total_lines
        }
        
    except Exception as e:
        logger.error(f"[MemoryTools] Read failed: {e}")
        return {
            "success": False,
            "path": path,
            "content": "",
            "error": str(e)
        }


def get_decorated_tools() -> List:
    mode = get_memory_mode()
    if mode == "wiki":
        return [memory_search, memory_index, memory_get, write_memory, edit_memory, read_memory]
    return [memory_search, memory_get, write_memory, edit_memory, read_memory]
