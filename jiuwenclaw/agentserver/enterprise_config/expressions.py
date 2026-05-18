"""策略表达式求值（``match_expr``；Agent 规则另可校验 ``agent_id`` 模板）。"""

from __future__ import annotations

import ast
import json
import re
from typing import Any

from jiuwenclaw.agentserver.enterprise_config.routing import RoutingContext

_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")
_OR_SPLIT_PATTERN = re.compile(r"\s+or\s+", flags=re.IGNORECASE)
# ${user::carol} / ${group::g_demo_sales}（键为字面量，不支持嵌套 ${...}）
_MAPPING_DIM_PATTERN = re.compile(r"^\$\{(user|group)::([^}]+)\}$", re.IGNORECASE)


async def _lookup_mapping_by_part(
    part: str,
    ctx: RoutingContext,
    *,
    jiuwenclaw_id: str,
) -> str | None:
    from . import gateway_db

    dim_match = _MAPPING_DIM_PATTERN.fullmatch(part.strip())
    if dim_match:
        dim = dim_match.group(1).lower()
        key = dim_match.group(2).strip()
        if not key:
            return None
        if dim == "user":
            return await gateway_db.lookup_model_template_mapping_ref(
                jiuwenclaw_id, user_id=key
            )
        return await gateway_db.lookup_model_template_mapping_ref(
            jiuwenclaw_id, group_id=key
        )

    return None


async def resolve_model_slot_ref(
    raw: Any,
    ctx: RoutingContext,
    *,
    jiuwenclaw_id: str,
) -> str | None:
    """解析槽位引用：字面 id、``${group::key} or 1`` / ``${user::key}`` 等。"""
    text = str(raw or "").strip()
    if not text:
        return None

    parts = _OR_SPLIT_PATTERN.split(text)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if part.isdigit():
            return part

        mapping_ref = await _lookup_mapping_by_part(
            part, ctx, jiuwenclaw_id=jiuwenclaw_id
        )
        if mapping_ref:
            return mapping_ref
        if part.isdigit():
            return part
    return None


def substitute_template(template: str, ctx: RoutingContext) -> str:
    """将 ``${group_id}`` 等占位符替换为上下文值。"""

    def _repl(match: re.Match[str]) -> str:
        key = match.group(1)
        return ctx.as_dict().get(key, "")

    return _VAR_PATTERN.sub(_repl, template or "").strip()


def service_rule_matches(rule: dict[str, Any], ctx: RoutingContext) -> bool:
    """服务策略是否命中：仅 ``match_expr``；``service_id`` 字段不参与匹配。"""
    return evaluate_match_expr(rule.get("match_expr"), ctx)


def agent_rule_matches(rule: dict[str, Any], ctx: RoutingContext) -> bool:
    raw_agent_id = str(rule.get("agent_id") or "").strip()
    if raw_agent_id:
        if "${" in raw_agent_id:
            resolved = substitute_template(raw_agent_id, ctx)
            if resolved and resolved != ctx.agent_id:
                return False
        elif raw_agent_id != ctx.agent_id:
            return False
    return evaluate_match_expr(rule.get("match_expr"), ctx)


def evaluate_match_expr(expr: Any, ctx: RoutingContext) -> bool:
    """空表达式视为全匹配；支持简单比较与 JSON OR 列表。"""
    if expr is None:
        return True
    if isinstance(expr, list):
        return any(evaluate_match_expr(item, ctx) for item in expr)
    text = str(expr).strip()
    if not text:
        return True

    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return any(evaluate_match_expr(item, ctx) for item in parsed)
        except json.JSONDecodeError:
            pass

    return _eval_simple_comparison(text, ctx)


def _eval_simple_comparison(text: str, ctx: RoutingContext) -> bool:
    """支持 ``user_id == 'alice'``、``group_id == "g"`` 等。"""
    for key in ("user_id", "group_id", "bot_id", "service_id", "agent_id", "jiuwenclaw_id"):
        field_val = ctx.as_dict().get(key, "")
        # 宽松：字段出现在表达式中且字面量等于当前值
        if f"{key} ==" in text.replace(" ", ""):
            try:
                tree = ast.parse(text, mode="eval")
                return _safe_eval_bool(tree.body, ctx)
            except (SyntaxError, ValueError):
                pass
        if field_val and field_val in text and "==" not in text:
            continue
    # 无法解析时：非空表达式默认不匹配（避免误命中）
    if "==" in text or "!=" in text:
        try:
            tree = ast.parse(text, mode="eval")
            return _safe_eval_bool(tree.body, ctx)
        except (SyntaxError, ValueError):
            return False
    return True


def _safe_eval_bool(node: ast.AST, ctx: RoutingContext) -> bool:
    env = ctx.as_dict()

    if isinstance(node, ast.Compare):
        left = _safe_eval_value(node.left, env)
        for op, comparator in zip(node.ops, node.comparators):
            right = _safe_eval_value(comparator, env)
            if isinstance(op, ast.Eq):
                if left != right:
                    return False
            elif isinstance(op, ast.NotEq):
                if left == right:
                    return False
            else:
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
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        raise ValueError(f"unknown name: {node.id}")
    raise ValueError("unsupported expression node")
