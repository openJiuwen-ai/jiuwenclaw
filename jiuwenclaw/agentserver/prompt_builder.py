# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import os
import logging
from datetime import datetime
from typing import Optional

from jiuwenclaw.paths import get_agent_workspace_dir

logger = logging.getLogger(__name__)

SILENT_REPLY_TOKEN = "[[SILENT]]"

DEFAULT_WORKSPACE_DIR = str(get_agent_workspace_dir())


def build_system_prompt(
    workspace_dir: str = DEFAULT_WORKSPACE_DIR,
    agent_id: str = "main_agent",
    user_timezone: Optional[str] = None,
) -> str:
    """Build system prompt for the agent.

    Args:
        workspace_dir: Workspace directory (default: workspace/agent)
        agent_id: Agent identifier
        base_prompt: Base prompt to append at the beginning (e.g., SYSTEM_PROMPT from interface.py)
        user_timezone: User's timezone

    Returns:
        System prompt string
    """
    sections = []

    today = datetime.now().strftime("%Y-%m-%d")
    now_str = datetime.now().strftime('%H:%M:%S')
    abs_path = os.path.abspath(workspace_dir)

    memory_prompt = """# 记忆系统

每次会话都是全新的。唯一可以跨会话保留的信息来自工作目录中的文件。

## 记忆结构

- daily: `memory/YYYY-MM-DD.md`（当天原始事件与操作轨迹，允许追加）
- user: `USER.md`（稳定身份信息）
- long-term: `MEMORY.md`（长期稳定背景，精选的记忆 — 提炼的精华，不是原始日志）

## 核心原则

- 会话不会保留记忆，文件才是唯一记忆载体。想记住什么，必须写入文件
- 更新 USER.md 或 MEMORY.md 前必须先读取现有内容
- **每个字段只能出现一次**，已存在用 `edit_memory` 替换，不存在用 `write_memory` append

### 身份记录机制（高优先级）

#### 明确声明（强制写入）

触发词：
"我是… / 我叫… / 这是我的… / 请注意我是…"

流程：
- 立即暂停任务
- read USER.md
- 判断字段 → edit 或 write
- 写入成功后再继续任务

#### 用户要求记录（双重写入）

触发词：
"帮我记一下 / 记住这个 / 记一下 / 别忘了"

**强制规则：同时完成两件事！**
1. 如果涉及时间/任务 → 调用 `todo_create` 创建待办
2. 调用 `write_memory` 写入 `memory/YYYY-MM-DD.md`


### 操作轨迹自动落盘（写入 daily）

**每次文件操作后，必须调用 `write_memory` 记录到 `memory/YYYY-MM-DD.md`**

记录内容：
- 文件路径
- 操作类型（读取/写入/编辑/删除）
- 操作目的或上下文
- 涉及的邮箱、账号、项目名称等关键信息

### 主动记录

对话中发现有价值的信息时，**先记下来，再回答问题**：

- 用户提到的个人信息（名字、偏好、习惯、工作方式）→ 更新 `USER.md`
- 对话中做出的重要决策或结论 → 记录到 `memory/YYYY-MM-DD.md`
- 发现的项目上下文、技术细节、工作流程 → 写入相关文件
- 用户表达的喜好或不满 → 更新 `USER.md`
- 工具相关的本地配置（SSH、摄像头等）→ 更新 `MEMORY.md`
- 任何你觉得未来会话可能用到的信息 → 立刻记下来

**关键原则：** 不要总是等用户说"记住这个"。如果信息对未来有价值，主动记录。先记录，再回答 — 这样即使会话中断，信息也不会丢失。

### 检索工具

**响应任何消息前，必须：**
1. 读 `USER.md` — 你在帮谁
2. 读 `memory/YYYY-MM-DD.md`（今天 + 昨天）了解上下文
3. **仅主会话：** 读 `MEMORY.md`
4. **回答过往事件前：** 必须先用 `memory_search` 工具搜索历史记忆
"""
    sections.append(memory_prompt)
    sections.append("")

    profile_content = _read_file(workspace_dir, "USER.md")
    if profile_content:
        sections.append("# 当前身份与用户资料")
        sections.append("这是你对自己和用户的了解：")
        sections.append(profile_content)
        sections.append("")

    memory_content = _read_file(workspace_dir, "MEMORY.md")
    if memory_content:
        sections.append("# 长期记忆")
        sections.append("之前会话的重要信息：")
        sections.append(memory_content)
        sections.append("")

    today_content = _read_file(workspace_dir, f"memory/{today}.md")
    if today_content:
        sections.append("# 今日会话记录")
        sections.append(today_content)
        sections.append("")

    memory_mgmt_prompt = f"""## 记忆管理

### 更新规则
1. 更新前必须先读取现有内容
2. 合并新信息，不要全部覆盖
3. MEMORY.md 条目只写精炼的事实，不含日期/时间戳
4. **USER.md 字段去重：** 已存在用 `edit_memory` 替换，不存在用 `write_memory` append

### 记忆维护（Heartbeat 期间）

定期浏览最近的 `memory/YYYY-MM-DD.md`，识别值得长期保留的内容，更新到 `MEMORY.md`，删除过时信息。
""".format(today=today)
    sections.append(memory_mgmt_prompt)

    time_prompt = f"""# 当前日期与时间
当前日期：{today}
当前时间：{now_str}
"""
    if user_timezone:
        time_prompt += f"时区：{user_timezone}\n"
    sections.append(time_prompt)
    return "\n".join(sections)


def _resolve_memory_path(path: str) -> str:
    """Resolve memory file path. Maps USER.md and MEMORY.md to memory/ subdirectory."""
    if path.startswith("memory/"):
        return path
    if path in ("USER.md", "MEMORY.md"):
        return f"memory/{path}"
    return path


def _read_file(workspace_dir: str, relative_path: str) -> Optional[str]:
    """Read file content from workspace."""
    resolved_path = _resolve_memory_path(relative_path)
    file_path = os.path.join(workspace_dir, resolved_path)
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if content:
                return content
            return None
    except FileNotFoundError:
        logger.debug(f"File not found: {file_path}")
        return None
    except Exception as e:
        logger.error(f"Error reading {file_path}: {e}")
        return None
