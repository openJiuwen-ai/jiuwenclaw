# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Todo system prompt section for DeepAgent.

This module provides the system prompt section
how to use the todo tools for progress tracking.
"""

from __future__ import annotations

from typing import Dict, Optional

from openjiuwen.deepagents.prompts import PromptSection

# ---------------------------------------------------------------------------
# TodoTool system prompt - for system message injection
# ---------------------------------------------------------------------------
TODO_SYSTEM_PROMPT_EN = """
You are a Todo assistant responsible for managing the todo list.

Core Responsibilities:
1. Determine whether to create a task plan based on user requirements
2. Use the todo_create tool to create an initial task list
3. Use the todo_modify tool to adjust task status, append new tasks, or cancel tasks
4. Use the todo_list tool to view current task

Task Status Explanation:
- pending: Tasks waiting to be executed
- in_progress: Currently executing tasks (only one at a time)
- completed: Completed tasks
- cancelled: Cancelled tasks

Usage Rules:
- When creating tasks, use the todo_create tool with semicolons to separate multiple tasks
- When modifying tasks, use the todo_modify tool with operation type (update/delete/cancel/append/insert_after/insert_before)
- Only one task can be in in_progress status at a time, pending tasks should be marked as pending
- When a task is completed, update its status to completed
- If a task is cancelled, update its status to cancelled

Important Notes:
- When the task changes, immediately call the todo_modify tool to make modifications.
- Simple tasks can be completed directly without creating a task plan
"""

TODO_SYSTEM_PROMPT_CN = """
你是一个任务规划助手，负责管理待办事项列表。

核心职责：
1. 根据用户需求，判断是否需要创建任务计划
2. 使用 todo_create 工具创建初始任务列表
3. 使用 todo_modify 工具调整任务状态、追加新任务或取消任务
4. 使用 todo_list 工具查看当前任务信息

任务状态说明：
- pending: 待执行的任务
- in_progress: 正在执行的任务（同时只能有一个）
- completed: 已完成的任务
- cancelled: 取消的任务

使用规则：
- 创建任务时，使用 todo_create 工具，用分号分隔多个任务
- 修改任务时，使用 todo_modify 工具，指定操作类型（update/delete/cancel/append/insert_after/insert_before）
- 同一时间只能有一个任务处于 in_progress 状态，待执行任务标记为 pending
- 任务完成后，将其状态更新为 completed
- 如果任务取消，将其状态更新为 cancelled

重要提示：
- 当任务发生变化时，立即调用todo_modify工具进行修改
- 简单任务可以直接完成，无需创建任务计划
"""

TODO_SYSTEM_PROMPT: Dict[str, str] = {
    "cn": TODO_SYSTEM_PROMPT_CN,
    "en": TODO_SYSTEM_PROMPT_EN,
}

# ---------------------------------------------------------------------------
# Progress reminder user prompt (bilingual) - for user message injection
# ---------------------------------------------------------------------------
PROGRESS_REMINDER_USER_PROMPT_EN = """
The following is the content and status of all tasks in the current task plan:

{tasks}

The task currently being executed is:

{in_progress_task}

Please review the above task progress to ensure the plan is being executed correctly.
If any tasks are stuck or need adjustment, please update them promptly
"""

PROGRESS_REMINDER_USER_PROMPT_CN = """
以下是当前任务规划中所有任务的内容和状态：

{tasks}

正在执行的任务为：

{in_progress_task}

请查看上述任务进度，确保计划正在正确执行。如果有任务卡住或需要调整，请及时更新
"""

PROGRESS_REMINDER_USER_PROMPT: Dict[str, str] = {
    "cn": PROGRESS_REMINDER_USER_PROMPT_CN,
    "en": PROGRESS_REMINDER_USER_PROMPT_EN,
}


def build_todo_system_prompt(language: str = "cn") -> str:
    """Get the todo system prompt for the given language.

    Args:
        language: 'cn' or 'en'.

    Returns:
        Todo system prompt text.
    """
    return TODO_SYSTEM_PROMPT.get(language, TODO_SYSTEM_PROMPT["cn"])


def build_progress_reminder_user_prompt(language: str = "cn",
            tasks: str = "", in_progress_task: str = "") -> str:
    """Get the progress reminder user prompt for the given language.

    Args:
        language: 'cn' or 'en'.
        tasks: All tasks currently planned
        in_progress_task: The task with the status in_progress

    Returns:
        Progress reminder user prompt text.
    """
    prompt_template = PROGRESS_REMINDER_USER_PROMPT.get(
        language, PROGRESS_REMINDER_USER_PROMPT["cn"]
    )
    return prompt_template.format(tasks=tasks, in_progress_task=in_progress_task)


def build_todo_section(language: str = "cn") -> Optional["PromptSection"]:
    """Build a PromptSection for todo system prompt.

    Args:
        language: 'cn' or 'en'.

    Returns:
        A PromptSection instance for todo.
    """
    content = build_todo_system_prompt(language)

    return PromptSection(
        name="todo",
        content={language: content},
        priority=90,
    )
