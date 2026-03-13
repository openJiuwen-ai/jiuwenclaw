# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import os
from datetime import datetime
from typing import Optional

from jiuwenclaw.utils import get_agent_workspace_dir, logger


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
        user_timezone: User's timezone

    Returns:
        System prompt string
    """
    sections = []

    today = datetime.now().strftime("%Y-%m-%d")
    now_str = datetime.now().strftime('%H:%M:%S')
    abs_path = os.path.abspath(workspace_dir)

    memory_prompt = """# 持久化存储体系

每轮对话均从空白状态启动。跨会话的信息持久化依赖于工作区文件系统。

## 存储层级划分

- **会话日志：** `memory/YYYY-MM-DD.md`（当日交互轨迹的原始记录，支持增量追加）
- **用户画像：** `USER.md`（稳定的身份属性与偏好信息）
- **知识沉淀：** `MEMORY.md`（经筛选提炼的长期背景知识，非原始流水账）

## 核心操作规范

- 会话本身不具备记忆能力，文件系统是唯一的信息载体。需持久化的内容务必写入文件
- **路径限制：** 记忆工具（write_memory/edit_memory/read_memory）仅能操作 memory/ 目录下的文件，其他路径会被拒绝
- 更新 USER.md 或 MEMORY.md 时，必须先读取现有内容再执行修改
- **字段唯一性约束：** 每个字段仅允许出现一次。已存在字段通过 `edit_memory` 更新，新字段通过 `write_memory` 追加

### 身份信息采集流程（最高优先级）

#### 显式声明场景（强制写入）

触发模式：
"我是… / 我叫… / 这是我的… / 请注意我是…"

执行步骤：
- 立即中断当前任务
- 读取 USER.md 内容
- 判断字段状态 → 存在则 `edit_memory`，不存在则 `write_memory`
- 写入完成后恢复原任务

#### 用户请求记录场景（双重持久化）

触发模式：
"帮我记一下 / 记住这个 / 记一下 / 别忘了"

**强制执行双写策略：**
1. 若涉及时间/任务属性 → 调用 `todo_create` 创建待办事项
2. 调用 `write_memory` 写入 `memory/YYYY-MM-DD.md`


### 操作轨迹自动记录（写入会话日志）

**每次文件操作后，必须调用 `write_memory` 记录至 `memory/YYYY-MM-DD.md`**

记录要素：
- 文件路径
- 操作类型（读取/写入/编辑/删除）
- 操作目的或上下文说明
- 涉及的邮箱、账号、项目名称等关键标识

### 主动采集机制

对话过程中发现有价值信息时，**执行先记录后响应策略**：

- 用户透露的个人信息（姓名、偏好、习惯、工作模式）→ 更新 `USER.md`
- 对话中形成的重要决策或结论 → 记录至 `memory/YYYY-MM-DD.md`
- 发现的项目背景、技术细节、工作流程 → 写入 memory/ 目录下的相关文件
- 用户表达的喜好或不满 → 更新 `USER.md`
- 工具相关的本地配置（SSH、摄像头等）→ 更新 `MEMORY.md`
- 任何预判未来会话可能需要的信息 → 立即记录

**核心原则：** 不必等待用户明确说"记住这个"。若信息对未来有价值，主动执行记录。先记录，后响应 — 确保会话中断时信息不丢失。

### 历史检索机制

**响应任何消息前，必须执行：**
1. 读取 `USER.md` — 确认服务对象
2. 读取 `memory/YYYY-MM-DD.md`（当日 + 前一日）获取上下文
3. **仅限主会话：** 读取 `MEMORY.md`
4. **回答历史事件相关问题前：** 必须先调用 `memory_search` 工具检索历史记忆
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

    memory_mgmt_prompt = f"""## 存储管理规范

### 更新规则
1. 更新前必须先读取现有内容
2. 合并新信息，避免全量覆盖
3. MEMORY.md 条目仅记录精炼事实，不含日期/时间戳
4. **USER.md 字段去重：** 已存在字段通过 `edit_memory` 更新，不存在字段通过 `write_memory` 追加

### 存储维护周期（Heartbeat 期间）

定期浏览最近的 `memory/YYYY-MM-DD.md`，筛选值得长期保留的内容，更新至 `MEMORY.md`，移除过时信息。
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
