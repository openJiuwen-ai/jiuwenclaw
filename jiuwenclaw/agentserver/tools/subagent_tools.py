# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Fork Agent and Spawn Agent tools - Create subagents for task execution."""

from __future__ import annotations

import asyncio
from typing import Any

from openjiuwen.core.foundation.llm import AssistantMessage, SystemMessage
from openjiuwen.core.foundation.tool import tool
from openjiuwen.core.session.agent import Session

from jiuwenclaw.agentserver.tools.subagent_executor import (
    get_subagent_parent_session,
    get_current_agent_context,
    get_current_fork_agent_executor,
)
from jiuwenclaw.agentserver.tools.subagent_models import (
    ForkAgentTaskSpec,
    SpawnSubagentParams,
    SubagentTaskSpec,
)
from jiuwenclaw.utils import logger


# Hint appended to subagent tool results to guide the LLM toward stopping
# after receiving the delegated task's output, rather than continuing to
# call more tools or re-delegate the same task.
_SUBAGENT_STOP_HINT = (
    "\n\n[SYSTEM] The delegated task is complete. "
    "You should now summarize this result to the user and finish your turn. "
    "Do NOT call fork_agent or spawn_subagent again for this task."
)


def _wrap_subagent_result(result_dict: dict[str, Any]) -> dict[str, Any]:
    """Append a stop hint to the subagent result so the LLM knows to stop.

    The hint is added to the ``result`` field (for success) or ``error`` field
    (for failure) so it appears in the tool_result message visible to the LLM.
    Covers all branches including empty result / empty error.
    """
    if result_dict.get("success"):
        # Success path: append hint to result (even if empty/None)
        existing = result_dict.get("result") or ""
        result_dict["result"] = existing + _SUBAGENT_STOP_HINT
    else:
        # Failure path: append hint to error (even if empty/None)
        existing = result_dict.get("error") or ""
        result_dict["error"] = existing + _SUBAGENT_STOP_HINT
    return result_dict


@tool(
    name="fork_agent",
    description=(
        "创建分叉子代理，继承父代理的上下文。**默认情况下不要使用**，除非你需要共享上下文（例如，"
        "具有一致文档理解的并行任务）。对于常规任务，请使用 spawn_subagent（隔离上下文）。"
        "分叉会继承父代理的历史记录，可能污染子代理的推理。\n\n"
        "可选模型选择：传递 `model_tier`（配置文件中的 lite/pro）或 `model_name`"
        "（精确模型名称）。两者都省略则使用父代理的默认模型。如果请求的 tier 未配置，"
        "则改用父代理的默认模型。你可以用自然语言描述模型偏好，例如'使用 lite 模型'或'使用 pro 模型'。\n\n"
        "深度思考控制：传递 `thinking`（`default`|`off`|`on`）。重要：除非subagent的任务上下文中明确指定开启或关闭思考，否则一律填充 `default`。\n\n"
        "**重要提示**：收到 fork_agent 结果后，你**必须**将结果总结给用户并**停止**。"
        "不要对同一子任务重复委派。子代理已完成其工作——你的职责是呈现结果，而不是重新委派。"
    ),
)
async def fork_agent(
    objective: str,
    prompt: str = "",
    model_name: str = "",
    model_tier: str = "",
    thinking: str = "",
) -> dict[str, Any]:
    """
    Create fork subAgent inheriting parent Agent's message history (shared context).

    **Default choice: spawn_subagent. Use fork_agent only when you need shared context understanding.**

    Key features:
    - Inherits parent's messages (KVCache reuse for efficiency)
    - All fork agents share the same document understanding (consistent style)
    - Model decides execution: serial (one by one) or parallel (batch)

    Args:
        objective: Task objective (what to accomplish)
        prompt: Execution prompt (optional, detailed instructions)
        model_name: Optional exact model name for this subagent
        model_tier: Optional model tier (lite or pro) configured in models.defaults
        thinking: Optional semantic thinking mode: default | off | on

    Returns:
        {"success": bool, "task_id": str, "role_id": str, "result": str, "error": str, "usage": dict}
    """
    executor = get_current_fork_agent_executor()
    if executor is None:
        return _wrap_subagent_result({"success": False, "error": "Fork agent tools not initialized"})

    parent_session: Session | None = get_subagent_parent_session()

    # Get fork messages from parent agent's context
    fork_messages = await get_fork_messages()

    task = ForkAgentTaskSpec(
        objective=objective,
        prompt=prompt,
        model_name=model_name,
        model_tier=model_tier,
        thinking=thinking,
    )

    result = await executor.execute_fork(
        task, fork_messages=fork_messages, parent_session=parent_session
    )

    return _wrap_subagent_result(result.model_dump())


@tool(
    name="spawn_subagent",
    description=(
        "生成子代理，使用隔离的上下文执行任务。**默认选择**适用于大多数任务。"
        "子代理拥有大部分代理能力：多轮推理、工具调用、技能加载。\n\n"
        "**并发调用**：隔离上下文天然适合并发——你可以同时发起多个 spawn_subagent 调用处理**不同的**子任务"
        "（例如并行调研多个主题、同时对比多个方案）。每个子代理独立执行，无上下文冲突。"
        "仅在需要共享上下文时使用 fork_agent（用于具有一致文档理解的并行任务）。\n\n"
        "**角色指定**：通过 `role_id` 指定子代理角色（默认 MainAgent）。"
        "可选模型选择：传递 `model_tier`（配置文件中的 lite/pro）或 `model_name`"
        "（精确模型名称）。两者都省略则使用父代理的默认模型。如果请求的 tier 未配置，"
        "则改用父代理的默认模型。你可以用自然语言描述模型偏好，例如'使用 lite 模型'或'使用 pro 模型'。\n\n"
        "深度思考控制：传递 `thinking`（`default`|`off`|`on`）。重要：除非subagent的任务上下文中明确指定开启或关闭思考，否则一律填充 `default`。\n\n"
        "**重要提示**：不要对**同一个**子任务重复调用 spawn_subagent 或 fork_agent。"
        "子代理已完成其工作——你的职责是呈现结果，而不是重新委派。"
    ),
    input_params=SpawnSubagentParams,
)
async def spawn_subagent(**kwargs: Any) -> dict[str, Any]:
    """
    Spawn a subagent to execute a task, blocking until result is returned.

    The subagent has full Agent capabilities: multi-round reasoning, tool calls, skill loading.
    Streaming events are forwarded to the parent session with 'subagent.' prefix.

    **Key feature**: Isolated context (no message history inheritance). This is the default choice.
    Use fork_agent only when you need shared context understanding (e.g., parallel document processing).

    **Nested scenario**: spawn_subagent can call fork_agent inside. The fork_agent will inherit
    spawn's context (not main agent's), enabling consistent context within spawn's task scope.

    Args are validated via :class:`SpawnSubagentParams` (flat tool schema unchanged).

    Returns:
        {"success": bool, "task_id": str, "role_id": str, "result": str, "error": str, "usage": dict}
    """
    params = SpawnSubagentParams.model_validate(kwargs)

    executor = get_current_fork_agent_executor()
    if executor is None:
        return _wrap_subagent_result({"success": False, "error": "Subagent tools not initialized"})

    parent_session: Session | None = get_subagent_parent_session()

    task = SubagentTaskSpec(
        role_id=params.role_id,
        objective=params.objective,
        prompt=params.prompt,
        model_name=params.model_name,
        model_tier=params.model_tier,
        thinking=params.thinking,
    )

    result = await executor.execute_spawn(
        task, parent_session=parent_session
    )

    return _wrap_subagent_result(result.model_dump())


async def get_fork_messages() -> list[Any]:
    """
    Get messages from agent's current context for fork.

    Returns messages excluding SystemMessage (to avoid duplication with invoke's system prompt).
    Also excludes the last AssistantMessage with tool_calls if it exists, because that
    represents the current pending tool execution (fork_agent call itself) which has
    no corresponding ToolMessage yet.

    Priority:
    1. Use current context from context variable (correct for nested scenarios)
    2. Fallback to empty list (fork agent will have minimal context)

    Returns:
        List of messages from agent's context (excluding SystemMessage and pending tool calls)
    """
    # Priority 1: Use current context from context variable
    # This is the correct way to get messages from the running context
    current_context = get_current_agent_context()
    if current_context is not None:
        try:
            context_window = await current_context.get_context_window(
                system_messages=[], tools=None
            )
            messages = context_window.get_messages()

            # Filter out SystemMessage
            filtered_messages = [m for m in messages if not isinstance(m, SystemMessage)]

            # Filter out active skill body pins injected by context engine.
            # Fork agent should NOT inherit the full skill body (~62K chars) from parent:
            # - Fork instruction already contains actionable specifications
            # - Fork agent can reload skills via skill_tool() if it needs the full body
            pin_count = 0
            clean_messages = []
            for m in filtered_messages:
                meta = getattr(m, "metadata", None) or {}
                if meta.get("active_skill_pin"):
                    pin_count += 1
                else:
                    clean_messages.append(m)
            if pin_count:
                logger.info(
                    f"[ForkAgent] Filtered out {pin_count} active_skill_pin message(s) "
                    f"from fork context"
                )
            filtered_messages = clean_messages

            # Remove the last AssistantMessage if it has tool_calls
            # This is the current pending fork_agent call which has no ToolMessage yet
            # Including it would cause _fix_incomplete_tool_context to add placeholders
            if filtered_messages and isinstance(filtered_messages[-1], AssistantMessage):
                last_msg = filtered_messages[-1]
                if hasattr(last_msg, 'tool_calls') and last_msg.tool_calls:
                    # Check if this AssistantMessage has pending tool calls (no corresponding ToolMessage)
                    # by looking for tool_calls that aren't followed by ToolMessages
                    tool_call_ids = {getattr(tc, 'id', '') for tc in last_msg.tool_calls}
                    # Check if there are ToolMessages after this AssistantMessage
                    has_tool_messages_after = any(
                        hasattr(m, 'tool_call_id') and getattr(m, 'tool_call_id', '') in tool_call_ids
                        for m in filtered_messages[-1:]  # Only check messages after last AssistantMessage
                    )
                    if not has_tool_messages_after:
                        # Remove the pending AssistantMessage with tool_calls
                        filtered_messages = filtered_messages[:-1]
                        logger.debug(
                            f"[ForkAgent] Removed last AssistantMessage with pending tool_calls "
                            f"(had {len(last_msg.tool_calls)} calls)"
                        )

            logger.info(
                f"[ForkAgent] Got {len(filtered_messages)} messages from current context "
                f"(filtered from {len(messages)} total)"
            )
            return filtered_messages
        except Exception as e:
            logger.warning(f"[ForkAgent] Failed to get messages from current context: {e}")

    # Priority 2: Fallback to empty list
    # This happens when fork_agent is called outside of a running context
    logger.warning("[ForkAgent] Cannot get fork messages: no current context available")
    return []