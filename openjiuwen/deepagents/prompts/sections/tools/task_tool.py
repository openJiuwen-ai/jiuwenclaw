# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Task tool metadata for tool registration.

This module provides ONLY the tool registration metadata:
- Tool name
- Tool description template (with {available_agents} placeholder)
- Tool input parameters schema

It does NOT contain system prompt sections.
For system prompt sections, see sections/task_tool.py
"""

from __future__ import annotations

from typing import Any, Dict

from openjiuwen.deepagents.prompts.sections.tools.base import (
    ToolMetadataProvider,
)

# ---------------------------------------------------------------------------
# General-purpose agent description (bilingual) - used in available_agents list
# ---------------------------------------------------------------------------
GENERAL_PURPOSE_AGENT_DESC: Dict[str, str] = {
    "cn": "用于研究复杂问题、搜索文件与内容、执行多步骤任务。该智能体拥有与主代理完全相同的全部工具权限。",
    "en": "General-purpose agent for researching complex questions, searching for files and content, "
    "and executing multi-step tasks. This agent has access to all tools as the main agent.",
}

# ---------------------------------------------------------------------------
# Tool description (bilingual) - for tool registration ONLY
# ---------------------------------------------------------------------------
TASK_TOOL_DESCRIPTION_EN = """Launch an ephemeral subagent to handle complex, 
multi-step independent tasks with isolated context windows.

Available agent types and the tools they have access to:
{available_agents}

Important: When using the Task tool, 
you must specify the  subagent_type  and  task_description  parameters to select the agent type and describe the task.
Do not specify agents you do not have access to!!!

## Usage notes:
1. Launch multiple agents concurrently whenever possible to improve performance. 
Multiple tool calls can be initiated in a single message.
2. After an agent completes, it returns only one result, which is not visible to the user. 
If you need to display it to the user, you must provide a concise summary.
3. Each agent call is stateless; you cannot append messages. 
You must provide complete and detailed task instructions and explicitly specify what to return.
4. Generally, you can trust the agent's output directly.
5. Clearly tell the agent whether it is for creation, analysis, or research, as the agent cannot perceive user intent.
6. If the agent instructions require proactive use, 
please enable it proactively when the user does not explicitly request it.
7. If there is a clearly relevant task, prioritize using custom sub-agents.
8. The general-purpose agent is suitable for isolating context and token consumption, 
and completing specific complex tasks, as it has the exact same full capabilities as the main agent.
"""

TASK_TOOL_DESCRIPTION_CN = """启动临时子代理，处理复杂、多步骤、独立的隔离上下文任务。

可用代理类型及对应工具：
{available_agents}

重要：使用 Task 工具时，必须指定 subagent_type, task_description 参数选择代理类型和描述任务。请勿指定你无权访问的其他代理！！！

使用说明：
1. 尽可能并发启动多个代理以提升性能，可在一条消息中发起多个工具调用。
2. 代理完成后仅返回一条结果，用户不可见。如需展示给用户，需由你进行简洁总结。
3. 每次代理调用均无状态，无法追加消息。你必须给出完整详细的任务指令，并明确要求返回内容。
4. 通常可直接信任代理输出。
5. 明确告诉代理是创作、分析还是调研，代理无法感知用户意图。
6. 若代理说明要求主动使用，请在用户未明确要求时合理主动启用。
7. 如果有明确相关任务，优先使用自定义子智能体。
8. 通用智能体（general‑purpose agent）适合用于隔离上下文与 Token 消耗，并完成特定的复杂任务，因为它拥有与主代理完全相同的全部能力。

示例：
示例 1：并行独立研究
    用户：我想研究詹姆斯、科比的成就并对比。
    助手：并行启动子代理，分别研究两位球员。
    助手：汇总结果并回复用户。
    说明：研究复杂且各球员相互独立，适合拆分并行执行。
示例 2：单任务高上下文隔离
    用户：分析大型代码库的安全漏洞并生成报告。
    助手：启动单个子代理完成分析。
    助手：接收报告并总结输出。
    说明：用子代理隔离高消耗任务，避免主线程过载。
示例 3：并行处理多个简单任务
    用户：安排两场会议并准备议程。
    助手：并行启动子代理，分别处理两场会议议程。
    说明：任务简单但相互独立，用子代理隔离更清晰高效。
示例 4：简单任务不使用子代理
    用户：在三家餐厅分别点餐。
    助手：直接调用工具点餐，不使用 task 工具。
    说明：步骤简单，直接执行即可，无需子代理。
"""

DESCRIPTION: Dict[str, str] = {
    "cn": TASK_TOOL_DESCRIPTION_CN,
    "en": TASK_TOOL_DESCRIPTION_EN,
}

# ---------------------------------------------------------------------------
# Tool parameters (bilingual)
# ---------------------------------------------------------------------------
TASK_TOOL_PARAMS: Dict[str, Dict[str, str]] = {
    "subagent_type": {
        "cn": "子代理类型",
        "en": "Type of subagent to use",
    },
    "task_description": {
        "cn": "任务描述",
        "en": "Task description",
    },
}


def get_task_tool_input_params(language: str = "cn") -> Dict[str, Any]:
    """Return the full JSON Schema for task tool input_params.

    Args:
        language: 'cn' or 'en'.

    Returns:
        JSON Schema dict for tool parameters.
    """
    p = TASK_TOOL_PARAMS
    return {
        "type": "object",
        "properties": {
            "subagent_type": {
                "type": "string",
                "description": p["subagent_type"].get(language, p["subagent_type"]["cn"]),
            },
            "task_description": {
                "type": "string",
                "description": p["task_description"].get(language, p["task_description"]["cn"]),
            },
        },
        "required": ["subagent_type", "task_description"],
    }


class TaskMetadataProvider(ToolMetadataProvider):
    """Task tool metadata provider for tool registration.

    Provides tool name, description template, and parameter schema.
    Does NOT provide system prompt content.
    """

    def get_name(self) -> str:
        """Return tool name."""
        return "task_tool"

    def get_description(self, language: str = "cn") -> str:
        """Return tool description template with {available_agents} placeholder.

        Args:
            language: 'cn' or 'en'.

        Returns:
            Tool description template string.
        """
        return DESCRIPTION.get(language, DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        """Return JSON Schema for tool input parameters.

        Args:
            language: 'cn' or 'en'.

        Returns:
            JSON Schema dict for tool parameters.
        """
        return get_task_tool_input_params(language)
