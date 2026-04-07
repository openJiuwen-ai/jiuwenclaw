# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""DeepResearch tools implemented with openjiuwen @tool style."""

from __future__ import annotations
import json

from openjiuwen.core.foundation.tool import tool
from jiuwenclaw.agentserver.tools.deepresearch_task_manager import DeepResearchTaskManager


@tool(
    name="deepresearch_create_task",
    description=(
        "创建并启动 DeepResearch 任务（异步执行，不阻塞 Agent）。"
        "任务将在后台运行，完成后会通过 WebSocket 推送结果。"
        "返回任务 ID，可用于查询状态、取消任务或获取结果。"
    ),
)
async def deepresearch_create_task(
    query: str,
    file_name: str,
) -> str:
    """创建 DeepResearch 任务.

    Args:
        query: 研究查询
        file_name: 报告文件名，不带后缀

    Returns:
        任务 ID
    """
    manager = await DeepResearchTaskManager.get_instance()
    task_id = await manager.create_task(
        query=query,
        file_name=file_name,
        session_id="",
        channel_id="",
        request_id="",
    )
    return f"已创建 DeepResearch 任务，任务 ID: {task_id}"


@tool(
    name="deepresearch_get_status",
    description=(
        "查询 DeepResearch 任务的状态。"
        "返回任务的详细信息，包括状态、创建时间、开始时间、完成时间等。"
    ),
)
async def deepresearch_get_status(task_id: str) -> str:
    """获取任务状态.

    Args:
        task_id: 任务 ID

    Returns:
        任务状态信息（JSON 格式字符串）
    """
    manager = await DeepResearchTaskManager.get_instance()
    task_info = await manager.get_task_status(task_id)

    if task_info is None:
        return f"未找到任务 ID: {task_id}"

    return json.dumps(task_info, ensure_ascii=False, indent=2)


@tool(
    name="deepresearch_list_tasks",
    description=(
        "列出所有 DeepResearch 任务。"
        "支持按状态过滤（running/completed/cancelled/error）。"
        "返回任务列表，按创建时间倒序排列。"
    ),
)
async def deepresearch_list_tasks(status: str = "") -> str:
    """列出所有任务.

    Args:
        status: 可选的状态过滤器

    Returns:
        任务列表（JSON 格式字符串）
    """
    manager = await DeepResearchTaskManager.get_instance()
    status_filter = status if status else None
    tasks = await manager.list_tasks(status_filter=status_filter)

    if not tasks:
        return "暂无 DeepResearch 任务"

    return json.dumps(tasks, ensure_ascii=False, indent=2)


@tool(
    name="deepresearch_cancel_task",
    description=(
        "取消正在运行的 DeepResearch 任务。"
        "取消后任务状态将变为 cancelled。"
    ),
)
async def deepresearch_cancel_task(task_id: str) -> str:
    """取消任务.

    Args:
        task_id: 任务 ID

    Returns:
        操作结果
    """
    manager = await DeepResearchTaskManager.get_instance()
    success = await manager.cancel_task(task_id)

    if success:
        return f"已取消任务 ID: {task_id}"
    else:
        return f"取消任务失败，任务不存在或已完成: {task_id}"


@tool(
    name="deepresearch_get_result",
    description=(
        "获取已完成任务的详细结果。"
        "如果任务未完成，返回提示信息。"
    ),
)
async def deepresearch_get_result(task_id: str) -> str:
    """获取任务结果.

    Args:
        task_id: 任务 ID

    Returns:
        任务结果
    """
    manager = await DeepResearchTaskManager.get_instance()
    result = await manager.get_task_result(task_id)

    if result is None:
        task_info = await manager.get_task_status(task_id)
        if task_info:
            return f"任务 {task_id} 尚未完成，当前状态: {task_info['status']}"
        else:
            return f"未找到任务 ID: {task_id}"

    return result


def get_deepresearch_tools() -> list:
    """获取 DeepResearch 工具列表.

    Returns:
        工具列表（仅包含任务池工具）
    """
    return [
        deepresearch_create_task,
        deepresearch_get_status,
        deepresearch_list_tasks,
        deepresearch_cancel_task,
        deepresearch_get_result,
    ]


__all__ = [
    "deepresearch_create_task",
    "deepresearch_get_status",
    "deepresearch_list_tasks",
    "deepresearch_cancel_task",
    "deepresearch_get_result",
    "get_deepresearch_tools",
]
