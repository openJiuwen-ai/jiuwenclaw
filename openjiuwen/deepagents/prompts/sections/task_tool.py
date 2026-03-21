# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Task tool system prompt section for DeepAgent.

This module provides ONLY the system prompt section that tells the AI
how to use the task_tool. It does NOT contain tool registration metadata.

For tool registration metadata, see sections/tools/task_tool.py
"""

from __future__ import annotations

from typing import Dict, Optional

# ---------------------------------------------------------------------------
# Task system prompt (bilingual) - for system message injection
# ---------------------------------------------------------------------------
TASK_SYSTEM_PROMPT_EN = """## `task_tool` (subagent spawner) 
You have access to a `task_tool` tool to launch short-lived subagents that handle isolated tasks. These agents are ephemeral — they live only for the duration of the task and return a single result.

When to use the task_tool:
- Tasks that are complex, multi-step, and can be executed independently
- Scenarios requiring parallel processing, focused reasoning, or large context/token usage
- Tasks that require sandboxed execution (e.g., code execution, search, formatting)
- When only the final output is needed and intermediate steps are not required

When NOT to use the task tool:
- Tasks are simple
- Intermediate steps need to be observed
- Task decomposition provides no benefit and only adds latency

Usage Guidelines:
- Execute independent tasks in parallel whenever possible
- Use sub-agents to isolate complex tasks and improve efficiency
"""

TASK_SYSTEM_PROMPT_CN = """## task_tool 用于创建临时子代理，独立完成复杂任务并返回最终结果。
使用场景:
    - 任务复杂、多步骤、可独立执行
    - 需要并行处理、专注推理、大量上下文 / Token
    - 需要沙箱安全执行（代码、搜索、格式化）
    - 只需最终输出，不关心中间过程
不使用场景:
    - 任务简单
    - 需要查看中间步骤
    - 拆分无收益、仅增加延迟
使用原则:
    - 独立任务尽量并行执行
    - 用子代理隔离复杂任务，提升效率
"""

TASK_SYSTEM_PROMPT: Dict[str, str] = {
    "cn": TASK_SYSTEM_PROMPT_CN,
    "en": TASK_SYSTEM_PROMPT_EN,
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
def build_task_system_prompt(language: str = "cn") -> str:
    """Get the task tool system prompt for the given language.

    This is used ONLY for system prompt injection, NOT for tool registration.

    Args:
        language: 'cn' or 'en'.

    Returns:
        Task system prompt text.
    """
    return TASK_SYSTEM_PROMPT.get(language, TASK_SYSTEM_PROMPT["cn"])


def build_task_section(language: str = "cn") -> Optional["PromptSection"]:
    """Build a PromptSection for task tool system prompt.

    This creates a system prompt section that tells the AI how to use task_tool.
    It does NOT include available_agents list - that's in the tool description.

    Args:
        language: 'cn' or 'en'.

    Returns:
        A PromptSection instance for task tool.
    """
    from openjiuwen.deepagents.prompts.builder import PromptSection

    content = build_task_system_prompt(language)

    return PromptSection(
        name="task_tool",
        content={language: content},
        priority=85,
    )
