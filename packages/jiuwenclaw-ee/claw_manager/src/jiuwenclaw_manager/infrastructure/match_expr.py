"""``match_expr`` 写入校验（与 Gateway ``evaluate_match_expr`` / ``validate_match_expr`` 约定一致）。"""

from __future__ import annotations

import ast
import json
import re
from typing import Any

_ALLOWED_MATCH_NAMES = frozenset({"user_id", "group_id", "bot_id"})
# 不含 == / != 时仍视为全匹配，但若出现下列记号则视为误写，写入时拒绝。
_ALWAYS_MATCH_FORBIDDEN_CHECKS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\$\{"), "template_ref placeholders like ${...} are not allowed"),
    (re.compile(r"===|!=="), "=== / !== are not allowed; use == / !="),
    (
        re.compile(r">=|<=|(?<![!=])>(?!=)|(?<!<|>)<(?!=)"),
        "ordering operators (>, <, >=, <=) are not allowed; only == / != are supported",
    ),
    (
        re.compile(r"\bservice_id\b|\bagent_id\b", re.IGNORECASE),
        "service_id / agent_id are not allowed; only user_id / group_id / bot_id",
    ),
)
_MATCH_EXPR_PREFIX = "invalid match_expr"

__all__ = ("validate_match_expr",)


def _match_expr_error(reason: str) -> ValueError:
    return ValueError(f"{_MATCH_EXPR_PREFIX}: {reason}")


def _always_match_forbidden_reason(text: str) -> str | None:
    for pattern, reason in _ALWAYS_MATCH_FORBIDDEN_CHECKS:
        if pattern.search(text):
            return reason
    return None


def validate_match_expr(expr: Any) -> None:
    """Validate ``match_expr`` grammar; raise ``ValueError`` when invalid.

    Empty values / empty lists mean always-match; lists or JSON arrays mean OR;
    expressions with ``==`` / ``!=`` may only use ``user_id`` / ``group_id`` /
    ``bot_id``, literals, and ``and`` / ``or``.
    """
    if expr is None:
        return
    if isinstance(expr, list):
        for index, item in enumerate(expr):
            try:
                validate_match_expr(item)
            except ValueError as exc:
                detail = str(exc)
                if detail.startswith(_MATCH_EXPR_PREFIX):
                    detail = detail[len(_MATCH_EXPR_PREFIX):].lstrip(": ").lstrip("：")
                raise _match_expr_error(f"item[{index}]: {detail}") from exc
        return

    text = str(expr).strip()
    if not text:
        return

    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise _match_expr_error(
                f"value starting with '[' must be a valid JSON array ({exc})"
            ) from exc
        if not isinstance(parsed, list):
            raise _match_expr_error("JSON value must be an array")
        validate_match_expr(parsed)
        return

    if "==" not in text and "!=" not in text:
        reason = _always_match_forbidden_reason(text)
        if reason:
            raise _match_expr_error(reason)
        return

    # === / !== contain == / != as substrings; reject before ast.parse.
    if "===" in text or "!==" in text:
        raise _match_expr_error("=== / !== are not allowed; use == / !=")

    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise _match_expr_error(f"syntax error ({exc.msg})") from exc

    try:
        _validate_match_ast(tree.body)
    except ValueError as exc:
        raise _match_expr_error(str(exc)) from exc


def _validate_match_ast(node: ast.AST) -> None:
    if isinstance(node, ast.Compare):
        _validate_match_value(node.left)
        for op, comparator in zip(node.ops, node.comparators):
            if not isinstance(op, (ast.Eq, ast.NotEq)):
                raise ValueError(
                    "only == and != are supported; "
                    "ordering operators (>, <, >=, <=) are not allowed"
                )
            _validate_match_value(comparator)
        return

    if isinstance(node, ast.BoolOp):
        if not isinstance(node.op, (ast.And, ast.Or)):
            raise ValueError("only and / or combinators are supported")
        for value in node.values:
            _validate_match_ast(value)
        return

    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return

    raise ValueError(
        "only comparison expressions combined with and/or are allowed "
        "(fields limited to user_id / group_id / bot_id)"
    )


def _validate_match_value(node: ast.AST) -> None:
    if isinstance(node, ast.Constant):
        return
    if isinstance(node, ast.Name):
        if node.id not in _ALLOWED_MATCH_NAMES:
            raise ValueError(
                f"unknown name {node.id!r}; only user_id / group_id / bot_id are allowed"
            )
        return
    if isinstance(node, ast.Call):
        raise ValueError("function calls are not supported")
    if isinstance(node, ast.Attribute):
        raise ValueError("attribute access is not supported")
    if isinstance(node, (ast.BinOp, ast.UnaryOp)):
        raise ValueError("arithmetic and unary operators are not supported")
    raise ValueError("operands must be field names or literals")
