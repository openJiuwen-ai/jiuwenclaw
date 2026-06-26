# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""策略表达式求值（``match_expr``；Agent / Service 规则匹配均仅看 ``match_expr``）。"""

from __future__ import annotations

import ast
import json
import re
from typing import Any

from .gateway_db import GatewayDb
from .schemas import RoutingContext, TemplateRefSlot

_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")
_OR_SPLIT_PATTERN = re.compile(r"\s+or\s+", flags=re.IGNORECASE)
_MAPPING_DIM_PATTERN = re.compile(r"^\$\{(user|group)::([^}]+)\}$", re.IGNORECASE)


async def _list_records(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    return await GatewayDb.current().list_records(*args, **kwargs)


async def _lookup_mapping_by_part(
    part: str,
    *,
    template_type: str,
) -> str | None:
    """将 ``${user::…}`` / ``${group::…}`` 片段解析为映射表中的 ``template_id``。

    按 ``template_type``（槽位键）与 ``user_id`` / ``group_id`` 查询
    ``config_default_template_mapping``，命中则返回 **priority 最高、同 priority 时 updated_at 最新** 的一条。

    入参举例::

        part = "${group::g_demo_sales}"
        template_type = "default_model"

    返回值举例::

        "f5555555-5555-4555-8555-555555555505"  # 映射表命中
        None  # 非 ``${user::}`` / ``${group::}`` 格式，或库中无启用映射行
    """
    dim_match = _MAPPING_DIM_PATTERN.fullmatch(part.strip())
    if not dim_match:
        return None

    dim = dim_match.group(1).lower()
    key = dim_match.group(2).strip()
    if not key:
        return None

    base_filters: dict[str, Any] = {
        "enabled": True,
        "template_type": str(template_type or "").strip(),
    }
    if dim == "user":
        base_filters["user_id"] = key
    else:
        base_filters["group_id"] = key

    rows = await _list_records(
        "config_default_template_mapping",
        filters=base_filters,
        order_by=[("priority", True), ("updated_at", True)],
    )
    if not rows:
        return None
    ref = str(rows[0].get("template_id") or "").strip()
    return ref or None


async def resolve_template_slot_ref(
    raw: Any,
    ctx: RoutingContext,
    *,
    template_type: str = TemplateRefSlot.DEFAULT_MODEL,
) -> str | None:
    """解析**单个槽位**的 ``template_ref`` 原始字符串，返回最终 ``template_id``（非整表映射）。

    支持字面 ``template_id``（UUID）、``${user::key}`` / ``${group::key}``（查默认映射表），
    以及 ``<映射表达式> or <回退 template_id>``：按 ``or`` 从左到右尝试，先命中先返回。
    ``template_type`` 须与当前槽位键一致；由 ``resolve_slot_template_id_map`` 汇总为
    ``slot -> template_id`` 字典。

    入参举例::

        raw = "${group::g_demo_sales} or f1111111-1111-4111-8111-111111111111"
        ctx = RoutingContext(group_id="g_demo_sales", bot_id="bot_main", user_id="alice")
        template_type = "default_model"

    返回值举例::

        "f5555555-5555-4555-8555-555555555505"  # 组映射命中
        "f1111111-1111-4111-8111-111111111111"  # 映射未命中，取 ``or`` 右侧字面 UUID
        None  # ``raw`` 为空，或各段均为无效 ``${...}`` 且无法回退
    """
    text = str(raw or "").strip()
    if not text:
        return None

    for part in _OR_SPLIT_PATTERN.split(text):
        part = part.strip()
        if not part:
            continue

        mapping_ref = await _lookup_mapping_by_part(
            part,
            template_type=template_type,
        )
        if mapping_ref:
            return mapping_ref
        if _MAPPING_DIM_PATTERN.fullmatch(part) or part.startswith("${"):
            continue
        return part
    return None


async def resolve_slot_template_id_map(
    refs: dict[str, list[str]],
    ctx: RoutingContext,
) -> dict[str, list[str]]:
    """将合并后的 ``template_ref``（槽位 -> 原始引用列表）解析为槽位 -> ``template_id`` 列表。

    入参举例::

        refs = {
            "default_model": [
                "${group::g_demo_sales} or f1111111-1111-4111-8111-111111111111",
            ],
            "vision_model": ["f2222222-2222-4222-8222-222222222202"],
            "skill_whitelist": [
                "a1000001-0000-4000-8000-000000000001",
                "abc",
            ],
        }
        ctx = RoutingContext(group_id="g_demo_sales", bot_id="bot_main", user_id="alice")

    返回值举例::

        {
            "default_model": ["f5555555-5555-4555-8555-555555555505"],
            "vision_model": ["f2222222-2222-4222-8222-222222222202"],
            "skill_whitelist": [
                "a1000001-0000-4000-8000-000000000001",
                "abc",
            ],
        }
        {}  # 各槽位均解析失败时
    """
    slot_template_id_map: dict[str, list[str]] = {}
    for slot, raw_list in refs.items():
        resolved: list[str] = []
        seen: set[str] = set()
        for raw in raw_list:
            template_id = await resolve_template_slot_ref(
                raw,
                ctx,
                template_type=slot,
            )
            if template_id and template_id not in seen:
                seen.add(template_id)
                resolved.append(template_id)
        if resolved:
            slot_template_id_map[slot] = resolved
    return slot_template_id_map


def substitute_template(template: str, ctx: RoutingContext) -> str:
    """将 ``${group_id}`` 等占位符替换为上下文值。

    入参举例::

        template = "${group_id}::${bot_id}"
        ctx = RoutingContext(group_id="g_demo_sales", bot_id="bot_main", user_id="alice")

    返回值举例::

        "g_demo_sales::bot_main"
        ""  # ``template`` 为空，或占位字段在 ``ctx`` 中不存在
    """

    def _repl(match: re.Match[str]) -> str:
        return ctx.as_dict().get(match.group(1), "")

    return _VAR_PATTERN.sub(_repl, template or "").strip()


def evaluate_match_expr(expr: Any, ctx: RoutingContext) -> bool:
    """对 ``match_expr`` 求值，判断当前上下文是否满足策略条件。

    ``None`` / 空字符串 / 空列表视为全匹配；列表或 JSON 数组表示 OR（任一子表达式为真即真）；
    否则按简单比较表达式解析（见 ``_eval_simple_comparison``）。

    入参举例::

        expr = "group_id == 'g_demo_sales'"
        ctx = RoutingContext(group_id="g_demo_sales", bot_id="bot_main", user_id="alice")

        expr = ""  # 或 None

    返回值举例::

        True  # 表达式为真，或 ``None`` / ``""`` / ``[]`` 视为全匹配
        False  # 比较为假（如 ``group_id == 'g_unknown'``）
    """
    if expr is None:
        return True
    if isinstance(expr, list):
        if not expr:
            return True
        return any(evaluate_match_expr(item, ctx) for item in expr)
    text = str(expr).strip()
    if not text:
        return True
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                if not parsed:
                    return True
                return any(evaluate_match_expr(item, ctx) for item in parsed)
        except json.JSONDecodeError:
            pass
    return _eval_simple_comparison(text, ctx)


def _eval_simple_comparison(text: str, ctx: RoutingContext) -> bool:
    """将单行 ``match_expr`` 求值为是否命中。

    - 不含 ``==`` / ``!=``：视为全匹配（返回 ``True``）。
    - 含比较运算符：用 ``ast`` 解析整句，在 ``user_id`` / ``group_id`` / ``bot_id`` 与字面量间求值；
      支持 ``and`` / ``or`` 组合。解析或求值失败返回 ``False``，避免误命中。

    入参举例::

        text = "group_id == 'g_demo_sales'"
        ctx = RoutingContext(group_id="g_demo_sales", bot_id="bot_main", user_id="alice")

        text = "user_id == 'alice' and group_id == 'g_demo_sales'"

        text = "invalid ==="

    返回值举例::

        True  # 比较成立，或 ``text`` 不含 ``==`` / ``!=``（全匹配）
        False  # 比较不成立，或含 ``==`` 但 ``ast`` 解析/求值失败
    """
    if "==" not in text and "!=" not in text:
        return True
    try:
        tree = ast.parse(text, mode="eval")
        return _safe_eval_bool(tree.body, ctx)
    except (SyntaxError, ValueError):
        return False


def _safe_eval_bool(node: ast.AST, ctx: RoutingContext) -> bool:
    """在安全子集内对 ``ast`` 比较/布尔节点求值（仅 ``==``、``!=``、``and``、``or``）。

    入参举例::

        # ``text = "group_id == 'g_demo_sales'"`` 经 ``ast.parse`` 得到 ``tree.body``
        ctx = RoutingContext(group_id="g_demo_sales", bot_id="bot_main", user_id="alice")

    返回值举例::

        True  # ``Compare`` / ``BoolOp(and/or)`` 求值为真
        False  # 任一比较不成立
        # 不支持的节点类型 → 抛出 ``ValueError``
    """
    env = ctx.as_dict()

    if isinstance(node, ast.Compare):
        left = _safe_eval_value(node.left, env)
        for op, comparator in zip(node.ops, node.comparators):
            right = _safe_eval_value(comparator, env)
            if isinstance(op, ast.Eq) and left != right:
                return False
            if isinstance(op, ast.NotEq) and left == right:
                return False
            if not isinstance(op, (ast.Eq, ast.NotEq)):
                return False
        return True

    if isinstance(node, ast.BoolOp):
        values = [_safe_eval_bool(v, ctx) for v in node.values]
        if isinstance(node.op, ast.And):
            return all(values)
        if isinstance(node.op, ast.Or):
            return any(values)
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return node.value
    raise ValueError("unsupported match_expr")


def _safe_eval_value(node: ast.AST, env: dict[str, str]) -> Any:
    """求值比较表达式中的左/右操作数：字面量常量或路由上下文字段名。

    入参举例::

        # 左侧 ``Name(id='group_id')``，``env`` 来自 ``ctx.as_dict()``
        env = {"group_id": "g_demo_sales", "bot_id": "bot_main", "user_id": "alice"}

        # 右侧 ``Constant(value='g_demo_sales')``

    返回值举例::

        "g_demo_sales"  # ``Name`` 从 ``env`` 取值，或 ``Constant`` 字面量
        # 未知 ``Name`` → 抛出 ``ValueError``
        # 非 ``Name`` / ``Constant`` 节点 → 抛出 ``ValueError``
    """
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        raise ValueError(f"unknown name: {node.id}")
    raise ValueError("unsupported expression node")


__all__ = (
    "evaluate_match_expr",
    "resolve_slot_template_id_map",
    "resolve_template_slot_ref",
    "substitute_template",
)
