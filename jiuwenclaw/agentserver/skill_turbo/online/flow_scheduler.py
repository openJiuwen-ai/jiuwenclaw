# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""FlowScheduler —— 候选集计算（execution_flow + when + 并行分组）。

职责（设计 §5.3）：
按 schema 的 ``execution_flow`` + ``when`` + ``parallel_with`` + ContextStore
已完成集 + accumulator 当前值，计算**当前可执行前沿候选集**（frontier）。
强约束 Agent 只能选候选集内 plan_name，防止跳步/乱序导致 inputs 缺失。

候选集语义：候选集 ≠ 当前任务所有待执行 PlanNode，而是**当前可执行的前沿**——
按 ``execution_flow`` 步骤顺序，最早一个含未完成节点的 step 中、``when`` 条件满足、
inputs 就绪的节点。通常只有 1 个节点（当前 create_ppt 全串行）或 1 个并行批。
"""

from __future__ import annotations

import ast
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jiuwenclaw.agentserver.skill_turbo.online.context_store import TurboContext

logger = logging.getLogger(__name__)


class WhenKeyMissing(Exception):
    """when 条件求值时所需键在 accumulator 中缺失。

    区分"条件明确 False"与"键缺失"：键缺失时上游未产出，不跳过、不进候选，等下一轮。
    """


# ── when 表达式安全求值 ──

# 布尔字面量映射（schema when 用小写 true/false）
_BOOL_LITERALS = {"true": True, "false": False}

# 允许的比较运算符
_ALLOWED_COMPARE_OPS = (
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
)


def eval_when(expr: str, accumulator: dict[str, Any]) -> bool:
    """安全求值 when 表达式。

    支持的表达式形式（schema 实例）：
    - ``"has_documents == true"``
    - ``"style_mode == template_pack"``
    - ``"content_page_count > 0"``

    安全措施：
    - 用 ``ast.parse(expr, mode="eval")`` 解析，遍历 AST
    - 只允许 ast.Compare（==/!=/</>/<=/>=）、ast.BoolOp（and/or）、
      ast.UnaryOp（not）、ast.Name（accumulator 键或 true/false）、
      ast.Constant（bool/str/int/float）
    - 禁止任意调用/属性访问/import
    - ast.Name id 不在 accumulator 且不是 true/false → 抛 WhenKeyMissing

    Args:
        expr: when 表达式字符串。
        accumulator: 当前 accumulator dict，提供变量值。

    Returns:
        bool: 条件求值结果。

    Raises:
        WhenKeyMissing: 表达式引用的键在 accumulator 中缺失。
        ValueError: 表达式包含不支持的语法。
    """
    if not expr or not expr.strip():
        return True
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"when 表达式语法错误: {expr!r}: {exc}") from exc
    result = _eval_node(tree.body, accumulator, strict=True)
    return bool(result)


def _eval_node(node: ast.AST, accumulator: dict[str, Any], *, strict: bool) -> Any:
    """递归求值 AST 节点。

    Args:
        node: AST 节点。
        accumulator: 当前 accumulator dict。
        strict: 严格模式。True 时未知 Name（不在 accumulator、非布尔字面量）抛
            WhenKeyMissing（用于比较左侧——accumulator 变量）；False 时未知 Name
            视为字符串字面量（用于比较右侧——枚举值，如 template_pack）。
    """
    if isinstance(node, ast.Constant):
        # Python 3.8+ 字面量
        if isinstance(node.value, (bool, int, float, str)):
            return node.value
        raise ValueError(f"when 表达式不支持的字面量: {node.value!r}")

    if isinstance(node, ast.Name):
        name = node.id
        # 布尔字面量
        if name in _BOOL_LITERALS:
            return _BOOL_LITERALS[name]
        # accumulator 变量
        if name in accumulator:
            return accumulator[name]
        if strict:
            # 比较左侧：accumulator 变量缺失 → 上游未产出，等下一轮
            raise WhenKeyMissing(f"when 条件键缺失: {name}")
        # 比较右侧：未知名视为字符串字面量（枚举值，如 template_pack）
        return name

    if isinstance(node, ast.BoolOp):
        values = [_eval_node(v, accumulator, strict=True) for v in node.values]
        if isinstance(node.op, ast.And):
            return all(values)
        if isinstance(node.op, ast.Or):
            return any(values)
        raise ValueError(f"不支持的布尔运算: {type(node.op).__name__}")

    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.Not):
            return not _eval_node(node.operand, accumulator, strict=strict)
        if isinstance(node.op, ast.USub):
            return -_eval_node(node.operand, accumulator, strict=strict)
        raise ValueError(f"不支持的一元运算: {type(node.op).__name__}")

    if isinstance(node, ast.Compare):
        # 左侧是 accumulator 变量（strict=True：缺失抛 KeyMissing）
        left = _eval_node(node.left, accumulator, strict=True)
        for op, comparator in zip(node.ops, node.comparators):
            if not isinstance(op, _ALLOWED_COMPARE_OPS):
                raise ValueError(f"不支持的比较运算: {type(op).__name__}")
            # 右侧是值（strict=False：未知名当字符串字面量，如 template_pack）
            right = _eval_node(comparator, accumulator, strict=False)
            if isinstance(op, ast.Eq):
                cmp = left == right
            elif isinstance(op, ast.NotEq):
                cmp = left != right
            elif isinstance(op, ast.Lt):
                cmp = left < right
            elif isinstance(op, ast.LtE):
                cmp = left <= right
            elif isinstance(op, ast.Gt):
                cmp = left > right
            elif isinstance(op, ast.GtE):
                cmp = left >= right
            else:  # pragma: no cover - 已被 _ALLOWED_COMPARE_OPS 守护
                raise ValueError(f"不支持的比较运算: {type(op).__name__}")
            if not cmp:
                return False
            left = right
        return True

    raise ValueError(f"when 表达式不支持的语法节点: {type(node).__name__}")


# ── schema 辅助 ──

def _group_by_step(execution_flow: list[dict]) -> list[list[dict]]:
    """按 step 号升序分组（同 step 的节点互为 parallel_with）。

    返回 list of step groups，每组是同 step 的 step_entry 列表。
    """
    if not execution_flow:
        return []
    buckets: dict[int, list[dict]] = {}
    for entry in execution_flow:
        step = entry.get("step")
        if not isinstance(step, int):
            step = 0
        buckets.setdefault(step, []).append(entry)
    return [buckets[step] for step in sorted(buckets.keys())]


def _get_plan_task(schema: dict, plan_name: str) -> dict | None:
    """从 schema.plan_tasks 取指定 plan_name 的任务定义。"""
    for task in schema.get("plan_tasks", []):
        if isinstance(task, dict) and task.get("plan_name") == plan_name:
            return task
    return None


def _inputs_ready(plan_name: str, schema: dict, accumulator: dict) -> bool:
    """检查节点 inputs 声明的键是否都在 accumulator 中存在。

    只检查必填 inputs（optional_inputs 可缺省）。
    """
    task = _get_plan_task(schema, plan_name)
    if task is None:
        # 未在 plan_tasks 中声明（如纯 group 入口）→ 视为 inputs=[]
        return True
    required = task.get("inputs", [])
    if not isinstance(required, list):
        return True
    for key in required:
        if key not in accumulator:
            return False
    return True


def _get_missing_inputs(plan_name: str, schema: dict, accumulator: dict) -> list[str]:
    """返回节点必填 inputs 中缺失的键列表。"""
    task = _get_plan_task(schema, plan_name)
    if task is None:
        return []
    required = task.get("inputs", [])
    if not isinstance(required, list):
        return []
    return [key for key in required if key not in accumulator]


def _get_skip_defaults(step_entry: dict) -> dict[str, Any]:
    """取 step_entry 的可选 skip_defaults 字段，缺省返回 {}。

    节点被 when 跳过时注入 accumulator 的默认值（设计 §5.3 / §7.1）。
    """
    defaults = step_entry.get("skip_defaults")
    if isinstance(defaults, dict):
        return defaults
    return {}


def _is_independent_entry(schema: dict, plan_name: str) -> bool:
    """判断节点是否为独立入口（independent_entry != false）。

    折叠节点（independent_entry == false，如 p6_1_page_worker）只能通过其 group
    入口执行，不出现在候选集。
    """
    task = _get_plan_task(schema, plan_name)
    if task is None:
        return True
    return task.get("independent_entry", True) is not False


# ── 候选集计算 ──

def next_candidates(schema: dict, ctx: "TurboContext") -> list[str]:
    """计算当前可执行前沿候选集（按步骤前沿，不跨 step 收集）。

    算法（设计 §5.3）：
    1. execution_flow 按 step 分组，按 step 号升序遍历
    2. 每组：pending = [n for n in group if n not in ctx.completed]
    3. pending 为空 → 看下一组
    4. pending 非空 → 这是前沿，只处理这一组：
       - 对每个 node：when 求值
         - when is not None:
           - eval_when 抛 WhenKeyMissing → continue（不跳过、不进候选，等下一轮）
           - eval_when == False → ctx.completed.add(node) + 注入 skip_defaults + continue
       - inputs_ready(node) → candidates.append(node)
       - inputs 未就绪 → 暂不进候选
       - 有 candidates → return candidates
       - 本组 pending 全被跳过 → continue 看下一组
       - 本组有 pending 但未就绪 → return []（等待，不前进）
    5. 全部完成 → return []

    Args:
        schema: scenario 对应的 schema dict。
        ctx: TurboContext（completed 集 + accumulator）。

    Returns:
        候选 plan_name 列表（前沿就绪节点，1 个或并行批）。
    """
    execution_flow = schema.get("execution_flow", [])
    if not isinstance(execution_flow, list):
        return []
    step_groups = _group_by_step(execution_flow)
    for step_group in step_groups:
        # 过滤掉折叠节点（不应出现在候选集）
        pending: list[dict] = []
        for entry in step_group:
            plan_name = entry.get("plan_name", "")
            if not plan_name or plan_name in ctx.completed:
                continue
            if not _is_independent_entry(schema, plan_name):
                continue
            pending.append(entry)
        if not pending:
            continue  # 该 step 全部完成（含跳过）→ 看下一 step
        # 该 step 是前沿（最早未完成步骤），只处理这一步，不跨步收集
        candidates: list[str] = []
        skipped_all = True
        for entry in pending:
            plan_name = entry.get("plan_name", "")
            when_expr = entry.get("when")
            if when_expr:
                try:
                    cond = eval_when(when_expr, ctx.accumulator)
                except WhenKeyMissing:
                    # 求值键缺失 → 上游未产出，不跳过、不进候选，等下一轮
                    skipped_all = False
                    continue
                if cond is False or cond is None:
                    # 条件明确 False → 跳过该节点
                    ctx.completed.add(plan_name)
                    skip_defaults = _get_skip_defaults(entry)
                    if skip_defaults:
                        ctx.accumulator.update(skip_defaults)
                    logger.debug(
                        "[FlowScheduler] skip node=%s when=%s skip_defaults=%s",
                        plan_name, when_expr, bool(skip_defaults),
                    )
                    continue
            # when 满足（True 或无 when）
            skipped_all = False
            if _inputs_ready(plan_name, schema, ctx.accumulator):
                candidates.append(plan_name)
            # inputs 未就绪 → 暂不进候选，等下一轮
        if candidates:
            return candidates  # 返回前沿就绪节点（1 个或并行批）
        # 若本 step pending 全被跳过（skipped_all=True 且 candidates 空）→ continue 看下一 step
        if skipped_all:
            continue
        # 若本 step 有 pending 但 inputs 未就绪/when 键缺失 → 返回 []（等待，不前进到下一 step）
        return []
    return []  # 全部完成


def is_task_complete(schema: dict, ctx: "TurboContext") -> bool:
    """所有 execution_flow 节点 ∈ ctx.completed。"""
    execution_flow = schema.get("execution_flow", [])
    if not isinstance(execution_flow, list):
        return True
    for entry in execution_flow:
        plan_name = entry.get("plan_name", "")
        if not plan_name:
            continue
        if not _is_independent_entry(schema, plan_name):
            continue
        if plan_name not in ctx.completed:
            return False
    return True


def get_execution_flow_plan_names(schema: dict) -> list[str]:
    """返回 execution_flow 中所有独立入口 plan_name（按 step 顺序）。"""
    execution_flow = schema.get("execution_flow", [])
    if not isinstance(execution_flow, list):
        return []
    result: list[str] = []
    for entry in execution_flow:
        plan_name = entry.get("plan_name", "")
        if not plan_name:
            continue
        if not _is_independent_entry(schema, plan_name):
            continue
        result.append(plan_name)
    return result


def format_execution_flow_overview(schema: dict) -> str:
    """格式化 execution_flow 概览字符串（供 activate 返回）。

    形如：``p0_pipeline_init → p1_intent_classify → ... → p10_delivery``
    """
    names = get_execution_flow_plan_names(schema)
    return " → ".join(names)


def get_missing_keys_for_node(plan_name: str, schema: dict, accumulator: dict) -> list[str]:
    """返回节点必填 inputs 中在 accumulator 缺失的键（供参数校验报错）。"""
    return _get_missing_inputs(plan_name, schema, accumulator)


__all__ = [
    "WhenKeyMissing",
    "eval_when",
    "next_candidates",
    "is_task_complete",
    "get_execution_flow_plan_names",
    "format_execution_flow_overview",
    "get_missing_keys_for_node",
]
