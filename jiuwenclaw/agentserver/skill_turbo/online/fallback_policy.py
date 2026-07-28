# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""在线 fallback 计数 + 整任务回退策略。

职责（设计 §5.7 / §6.4）：
- 参数校验重试：< N 返回 error 让 Agent 重试；≥ N 触发单节点 fallback
- 单节点 fallback：复用现有 fallback_handler ReAct 兜底
- 累计 fallback 超阈值 → 整任务回退（阶段相关：阶段2 回退批量、阶段3 直跳 skill_tool）
"""

from __future__ import annotations

from typing import Any

# 参数校验最大重试次数（< N 返回 error 让 Agent 重试；≥ N 触发单节点 fallback）
MAX_PARAM_RETRY_DEFAULT = 2

# 整任务最大 fallback 节点数（超过 → 整任务回退）
MAX_FALLBACK_NODES_DEFAULT = 3

# 关键节点：这些节点 fallback 直接触发整任务回退（不可降级）
CRITICAL_NODES_DEFAULT = ("p0_pipeline_init", "p10_delivery")


def should_retry(retry_count: int, max_retry: int = MAX_PARAM_RETRY_DEFAULT) -> bool:
    """参数校验重试 < N → True（返回 error 让 Agent 重试）。"""
    return retry_count < max_retry


def should_node_fallback(retry_count: int, max_retry: int = MAX_PARAM_RETRY_DEFAULT) -> bool:
    """参数校验重试 ≥ N → True（触发单节点 fallback）。"""
    return retry_count >= max_retry


def should_task_fallback(
    fallback_count: int,
    max_fallback: int = MAX_FALLBACK_NODES_DEFAULT,
    fallback_nodes: list[str] | None = None,
    critical_nodes: tuple[str, ...] = CRITICAL_NODES_DEFAULT,
) -> bool:
    """累计 fallback 超阈值 或 关键节点 fallback → True（整任务回退）。

    边界语义：本函数用 ``>``（不含上界，默认 max=3 时累计 3 次仍继续）；
    ``should_node_fallback`` 用 ``>=``（含上界）。二者有意不同，调用方勿混用。

    Args:
        fallback_count: 累计 fallback 节点数。
        max_fallback: 整任务最大 fallback 节点数。
        fallback_nodes: 走过 fallback 的节点列表。
        critical_nodes: 关键节点（这些节点 fallback 直接触发整任务回退）。
    """
    if fallback_count > max_fallback:
        return True
    if fallback_nodes:
        for node in fallback_nodes:
            if node in critical_nodes:
                return True
    return False


def build_task_fallback_output(
    skill_name: str,
    fallback_count: int,
    fallback_nodes: list[str] | None,
    *,
    stage: int = 3,
) -> dict[str, Any]:
    """构造整任务回退的 ToolOutput（设计 §5.7 / §6.4）。

    Args:
        skill_name: 源 skill 名。
        fallback_count: 累计 fallback 节点数。
        fallback_nodes: 走过 fallback 的节点列表（可为 None）。
        stage: 过渡阶段。stage=2 → 回退 skill_acceleration_exec（批量，已下线）；
            stage=3 → 回退 skill_tool（ReAct）。M6 后默认 stage=3（设计 §8.4 阶段3）。

    Returns:
        ToolOutput dict（success=False + 回退指引）。
    """
    if stage >= 3:
        # 阶段 3（默认）：直跳 skill_tool（ReAct）
        reason = (
            f"在线执行累计失败超阈值，请改用 skill_tool 走 {skill_name} 标准 ReAct 流程"
            f"（直接执行，无需再调用 skill_turbo_tool）"
        )
    else:
        # 阶段 2（已下线）：回退批量（skill_acceleration_exec）——保留分支供回滚
        reason = (
            f"在线执行累计失败超阈值，请改用 skill_acceleration_exec 走 {skill_name} 批量加速流程"
            f"（直接执行，无需再调用 skill_turbo_tool）"
        )
    return {
        "success": False,
        "error": reason,
        "fallback_count": fallback_count,
        "fallback_nodes": list(fallback_nodes or []),
    }


def build_param_retry_output(
    plan_name: str,
    missing_keys: list[str],
    retry_count: int,
    *,
    hint: str = "",
) -> dict[str, Any]:
    """构造参数校验失败重试的 ToolOutput（设计 §5.5 / §7.3）。"""
    default_hint = f"节点 {plan_name} 缺失 inputs: {missing_keys}，请先完成上游节点或传 increment 补全"
    return {
        "success": False,
        "error": f"参数校验失败，缺失 inputs: {missing_keys}",
        "missing_keys": list(missing_keys),
        "retry_count": retry_count,
        "hint": hint or default_hint,
    }


def build_candidate_violation_output(
    plan_name: str,
    candidates: list[str],
) -> dict[str, Any]:
    """构造候选集强约束校验失败的 ToolOutput（设计 §5.2 / §7.3）。"""
    return {
        "success": False,
        "error": f"plan_name {plan_name!r} 不在当前候选集",
        "candidates": list(candidates),
        "hint": "请从 candidates 中选择 plan_name",
    }


__all__ = [
    "MAX_PARAM_RETRY_DEFAULT",
    "MAX_FALLBACK_NODES_DEFAULT",
    "CRITICAL_NODES_DEFAULT",
    "should_retry",
    "should_node_fallback",
    "should_task_fallback",
    "build_task_fallback_output",
    "build_param_retry_output",
    "build_candidate_violation_output",
]
