# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""轻量入参校验（无状态、无重试计数）.

校验 skill_name / scenario / plan_name / node_inputs，
不做候选集校验、不做 accumulator 组装。
"""

from __future__ import annotations

import re
from typing import Any

__all__ = [
    "validate_skill_name",
    "validate_scenario",
    "validate_plan_name",
    "validate_node_inputs",
    "get_plan_task",
]

# scenario 只允许 snake_case + 字母数字
_SCENARIO_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# skill_name 允许字母数字 + hyphen（如 pptx-craft）
_SKILL_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")


def validate_skill_name(skill_name: str) -> str:
    """校验 skill_name 格式."""
    if not skill_name or not isinstance(skill_name, str):
        raise ValueError("skill_name 不能为空")
    if not _SKILL_NAME_RE.match(skill_name):
        raise ValueError(f"skill_name 格式非法: {skill_name!r}")
    return skill_name


def validate_scenario(scenario: str) -> str:
    """校验 scenario 格式（防路径穿越）."""
    if not scenario or not isinstance(scenario, str):
        raise ValueError("scenario 不能为空")
    if not _SCENARIO_RE.match(scenario):
        raise ValueError(f"scenario 格式非法: {scenario!r}（仅允许 snake_case）")
    # 防路径穿越
    if ".." in scenario or "/" in scenario or "\\" in scenario:
        raise ValueError(f"scenario 含非法字符: {scenario!r}")
    return scenario


def validate_plan_name(plan_name: str, schema: dict[str, Any]) -> str:
    """校验 plan_name 在 schema 声明节点中（防拼写错）.

    Args:
        plan_name: 节点名
        schema: schema dict

    Returns:
        校验通过的 plan_name

    Raises:
        ValueError: plan_name 不在 schema 中
    """
    if not plan_name or not isinstance(plan_name, str):
        raise ValueError("plan_name 不能为空")

    # 从 plan_tasks 和 code_plan_names 收集所有合法 plan_name
    valid_names: set[str] = set()
    for task in schema.get("plan_tasks", []) or []:
        if not isinstance(task, dict):
            continue
        pn = task.get("plan_name")
        if isinstance(pn, str) and pn:
            valid_names.add(pn)
    for cpn in schema.get("code_plan_names", []) or []:
        if not isinstance(cpn, dict):
            continue
        plan_names = cpn.get("plan_names", [])
        if not isinstance(plan_names, list):
            continue
        for pn in plan_names:
            if isinstance(pn, str) and pn:
                valid_names.add(pn)
    if plan_name not in valid_names:
        raise ValueError(
            f"plan_name {plan_name!r} 不在 schema 声明节点中，"
            f"合法节点: {sorted(valid_names)}"
        )
    return plan_name


def get_plan_task(plan_name: str, schema: dict[str, Any]) -> dict[str, Any] | None:
    """从 schema.plan_tasks 中获取指定 plan_name 的 task 定义."""
    for task in schema.get("plan_tasks", []):
        if isinstance(task, dict) and task.get("plan_name") == plan_name:
            return task
    return None


def validate_node_inputs(
    plan_name: str,
    node_inputs: dict[str, Any],
    schema: dict[str, Any],
) -> list[str]:
    """校验节点必填 inputs 是否齐全（轻量，无重试计数）.

    Args:
        plan_name: 节点名
        node_inputs: 节点输入 dict
        schema: schema dict

    Returns:
        缺失的必填键列表（空 = 校验通过）
    """
    task = get_plan_task(plan_name, schema)
    if task is None:
        # plan_name 不在 plan_tasks 中（可能是 group 入口或 root）
        # 不做必填校验，返回空
        return []

    required = task.get("inputs", [])
    if not isinstance(required, list):
        return []

    missing = [k for k in required if k not in node_inputs]
    return missing
