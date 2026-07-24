# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""参数校验 —— 按 schema plan_tasks inputs/optional_inputs 校验组装后的 node_inputs。

职责（设计 §5.5 + 优化修复 F1）：
1. 执行入参：始终传 accumulator **全量副本**（与原 root / HITL resume 语义一致）
2. 必填校验：``inputs`` 中每个键都在 node_inputs 中存在
3. increment：仅允许覆盖该节点 ``inputs ∪ optional_inputs`` 声明的键；
   group / 无 plan_tasks 时仅允许覆盖 accumulator 已有键
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ValidationResult:
    """参数校验结果。"""

    ok: bool
    missing_keys: list[str] = field(default_factory=list)
    hint: str = ""


def _get_plan_task(schema: dict, plan_name: str) -> dict | None:
    """从 schema.plan_tasks 取指定 plan_name 的任务定义。"""
    for task in schema.get("plan_tasks", []):
        if isinstance(task, dict) and task.get("plan_name") == plan_name:
            return task
    return None


def _is_group_node(schema: dict, plan_name: str) -> bool:
    """判断 plan_name 是否为 group 入口（出现在 group_children）。"""
    group_children = schema.get("group_children") or {}
    return isinstance(group_children, dict) and plan_name in group_children


def assemble_node_inputs(
    plan_name: str,
    schema: dict,
    accumulator: dict[str, Any],
    increment: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """从 accumulator 组装 node_inputs，并返回缺失的必填键。

    统一策略（优化修复 F1）：
    - ``node_inputs = dict(accumulator)`` 全量副本
    - 按 plan_tasks 声明做必填校验（无声明视为 inputs=[]）
    - increment 仅覆盖声明键（叶）或已有键（group/无声明）

    Returns:
        (node_inputs, missing_keys)。
    """
    task = _get_plan_task(schema, plan_name)
    if task is None:
        required: list[str] = []
        optional: list[str] = []
    else:
        required = task.get("inputs", []) or []
        optional = task.get("optional_inputs", []) or []
        if not isinstance(required, list):
            required = []
        if not isinstance(optional, list):
            optional = []

    node_inputs: dict[str, Any] = dict(accumulator)

    if increment and isinstance(increment, dict):
        if task is None or _is_group_node(schema, plan_name):
            # group / 无声明：只允许覆盖已有键，防止越权写入
            for key, value in increment.items():
                if key in node_inputs:
                    node_inputs[key] = value
        else:
            allowed_keys = set(required) | set(optional)
            for key, value in increment.items():
                if key in allowed_keys:
                    node_inputs[key] = value

    missing_keys = [key for key in required if key not in node_inputs]
    return node_inputs, missing_keys


def validate_node_inputs(
    plan_name: str,
    schema: dict,
    node_inputs: dict[str, Any],
) -> ValidationResult:
    """校验节点 inputs（必填齐全 + 超集允许）。"""
    task = _get_plan_task(schema, plan_name)
    if task is None:
        return ValidationResult(ok=True)
    required = task.get("inputs", []) or []
    if not isinstance(required, list):
        return ValidationResult(ok=True)
    missing = [key for key in required if key not in node_inputs]
    if missing:
        return ValidationResult(
            ok=False,
            missing_keys=missing,
            hint=f"节点 {plan_name} 缺失必填 inputs: {missing}",
        )
    return ValidationResult(ok=True)


def get_node_inputs_outputs(plan_name: str, schema: dict) -> tuple[list[str], list[str], list[str]]:
    """返回节点的 (inputs, optional_inputs, outputs) 声明。"""
    task = _get_plan_task(schema, plan_name)
    if task is None:
        return [], [], []
    inputs = task.get("inputs", []) or []
    optional = task.get("optional_inputs", []) or []
    outputs = task.get("outputs", []) or []
    return (
        inputs if isinstance(inputs, list) else [],
        optional if isinstance(optional, list) else [],
        outputs if isinstance(outputs, list) else [],
    )


__all__ = [
    "ValidationResult",
    "assemble_node_inputs",
    "validate_node_inputs",
    "get_node_inputs_outputs",
]
