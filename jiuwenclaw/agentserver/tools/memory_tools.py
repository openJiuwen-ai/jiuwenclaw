# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Memory tools for JiuWenClaw - Using @tool decorator for openjiuwen."""

import os
import logging
from typing import Optional, Dict, Any, List

from openjiuwen.core.foundation.tool.tool import tool

from ..memory import (
    MemoryIndexManager,
    MemorySettings,
    create_memory_settings,
    is_memory_enabled,
)

logger = logging.getLogger(__name__)

_global_manager: Optional[MemoryIndexManager] = None
_global_workspace_dir: str = "."
_global_settings: Optional[MemorySettings] = None
_global_agent_id: str = "default"


def _resolve_memory_path(path: str) -> str:
    """Resolve memory file path. Maps USER.md and MEMORY.md to memory/ subdirectory."""
    # If path already starts with memory/, return as-is
    if path.startswith("memory/"):
        return path
    # Map USER.md and MEMORY.md to memory/ subdirectory
    if path in ("USER.md", "MEMORY.md"):
        return f"memory/{path}"
    return path


def set_global_memory_manager(
    manager: Optional[MemoryIndexManager],
    workspace_dir: str = ".",
    settings: Optional[MemorySettings] = None,
    agent_id: str = "default"
):
    """Set global memory manager for tool functions."""
    global _global_manager, _global_workspace_dir, _global_settings, _global_agent_id
    _global_manager = manager
    _global_workspace_dir = workspace_dir
    _global_settings = settings
    _global_agent_id = agent_id


async def init_memory_manager_async(
    workspace_dir: str = ".",
    agent_id: str = "default"
) -> Optional[MemoryIndexManager]:
    """初始化记忆管理器（带文件监控）.
    
    Args:
        workspace_dir: 工作区目录
        agent_id: Agent ID
    
    Returns:
        MemoryIndexManager 实例，如果 memory 未启用则返回 None
    """
    global _global_manager, _global_workspace_dir, _global_settings, _global_agent_id
    
    if not is_memory_enabled():
        logger.info("Memory system is disabled")
        return None
    
    if _global_manager is not None and _global_workspace_dir == workspace_dir:
        return _global_manager
    
    settings = create_memory_settings(workspace_dir)
    
    _global_workspace_dir = workspace_dir
    _global_settings = settings
    _global_agent_id = agent_id
    
    try:
        _global_manager = await MemoryIndexManager.get(
            agent_id=agent_id,
            workspace_dir=workspace_dir,
            settings=settings
        )
        
        if _global_manager:
            logger.info(f"Memory manager initialized for: {workspace_dir}")
        
        return _global_manager
        
    except Exception as e:
        logger.error(f"Failed to initialize memory manager: {e}")
        return None


async def _ensure_global_manager() -> bool:
    """Ensure global memory manager is initialized."""
    global _global_manager, _global_settings, _global_workspace_dir, _global_agent_id
    
    if _global_manager is not None:
        return True
    
    try:
        _global_settings = _global_settings or MemorySettings()
        _global_manager = await MemoryIndexManager.get(
            agent_id=_global_agent_id,
            workspace_dir=_global_workspace_dir,
            settings=_global_settings
        )
        return True
    except Exception as e:
        logger.error(f"Failed to initialize global memory manager: {e}")
        return False


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
    if not await _ensure_global_manager():
        return {
            "results": [],
            "disabled": True,
            "error": "Memory manager not available"
        }
    
    if not _global_manager:
        return {
            "results": [],
            "disabled": True,
            "error": "Memory manager not initialized"
        }
    
    try:
        opts = {}
        if maxResults is not None:
            opts["maxResults"] = maxResults
        if minScore is not None:
            opts["minScore"] = minScore
        if sessionKey is not None:
            opts["sessionKey"] = sessionKey
        
        results = await _global_manager.search(query, opts=opts if opts else None)
        
        for r in results:
            if r["startLine"] == r["endLine"]:
                r["citation"] = f"{r['path']}#L{r['startLine']}"
            else:
                r["citation"] = f"{r['path']}#L{r['startLine']}-L{r['endLine']}"
        
        status = _global_manager.status()
        
        return {
            "results": results,
            "provider": status.get("provider"),
            "model": status.get("model"),
            "disabled": False
        }
        
    except Exception as e:
        logger.error(f"Memory search failed: {e}")
        return {
            "results": [],
            "disabled": True,
            "error": str(e)
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
    if not await _ensure_global_manager():
        return {
            "path": path,
            "text": "",
            "disabled": True,
            "error": "Memory manager not available"
        }
    
    if not _global_manager:
        return {
            "path": path,
            "text": "",
            "disabled": True,
            "error": "Memory manager not initialized"
        }
    
    try:
        result = await _global_manager.read_file(
            rel_path=path,
            from_line=from_line,
            lines=lines
        )
        return {
            **result,
            "disabled": False
        }
        
    except Exception as e:
        logger.error(f"Memory get failed: {e}")
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
    """创建新文件或覆盖现有文件。可用于创建新的记忆文件、保存重要信息、生成代码文件。
    主要用于新建memory/*.md文件，以及更新USER.md和MEMORY.md文件

    Args:
        path: 文件路径 (相对于工作区)
        content: 要写入的内容
        append: 是否追加模式 (默认覆盖)

    Returns:
        操作结果字典
    """
    try:
        resolved_path = _resolve_memory_path(path)
        full_path = os.path.join(_global_workspace_dir, resolved_path)
        
        if ".." in path or path.startswith("/"):
            return {
                "success": False,
                "path": path,
                "error": "Invalid path: directory traversal not allowed"
            }
        
        parent_dir = os.path.dirname(full_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        
        file_existed = os.path.exists(full_path)
        
        mode = "a" if append else "w"
        with open(full_path, mode, encoding="utf-8") as f:
            f.write(content)
        
        logger.info(f"{'Appended to' if append else 'Wrote'} file: {resolved_path}")

        return {
            "success": True,
            "path": resolved_path,
            "fullPath": full_path,
            "appended": append,
            "fileExisted": file_existed
        }
        
    except Exception as e:
        logger.error(f"Write failed: {e}")
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
    """精确编辑文件内容。oldText 必须完全匹配文件中的内容。如果 oldText 出现多次，需要更具体地指定。
    主要用于更新USER.md和MEMORY.md文件

    Args:
        path: 文件路径 (相对于工作区)
        oldText: 要查找的文本 (必须完全匹配)
        newText: 替换的文本

    Returns:
        操作结果字典
    """
    try:
        resolved_path = _resolve_memory_path(path)
        full_path = os.path.join(_global_workspace_dir, resolved_path)
        
        if ".." in path or path.startswith("/"):
            return {
                "success": False,
                "path": path,
                "error": "Invalid path: directory traversal not allowed"
            }
        
        if not os.path.exists(full_path):
            return {
                "success": False,
                "path": path,
                "error": f"File not found: {path}"
            }
        
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()
        
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
        
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        
        logger.info(f"Edited file: {resolved_path}")

        return {
            "success": True,
            "path": resolved_path,
            "replaced": oldText,
            "with": newText
        }
        
    except Exception as e:
        logger.error(f"Edit failed: {e}")
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
    """读取文件内容。可以读取整个文件或指定行范围。
    主要用于更新USER.md和MEMORY.md前的文件读取

    Args:
        path: 文件路径 (相对于工作区)
        offset: 起始行号 (从1开始)
        limit: 读取的行数

    Returns:
        文件内容字典
    """
    try:
        resolved_path = _resolve_memory_path(path)
        full_path = os.path.join(_global_workspace_dir, resolved_path)
        
        if ".." in path or path.startswith("/"):
            return {
                "success": False,
                "path": path,
                "content": "",
                "error": "Invalid path: directory traversal not allowed"
            }
        
        if not os.path.exists(full_path):
            return {
                "success": False,
                "path": path,
                "content": "",
                "error": f"File not found: {path}"
            }
        
        if not os.path.isfile(full_path):
            return {
                "success": False,
                "path": path,
                "content": "",
                "error": f"Not a file: {path}"
            }
        
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        
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
        logger.error(f"Read failed: {e}")
        return {
            "success": False,
            "path": path,
            "content": "",
            "error": str(e)
        }


def get_decorated_tools() -> List:
    """获取使用 @tool 装饰器的工具列表"""
    return [memory_search, memory_get, write_memory, edit_memory, read_memory]
