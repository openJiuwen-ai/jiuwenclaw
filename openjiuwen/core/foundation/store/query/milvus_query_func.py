# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Query Expression support for Milvus
"""

__all__ = ["milvus_def"]

from typing import Sequence

from .base import (
    ArithmeticExpr,
    ArrayExpr,
    ComparisonExpr,
    JSONExpr,
    LogicalExpr,
    MatchExpr,
    NullExpr,
    QueryLanguageDefinition,
    RangeExpr,
    raise_query_error,
)


def milvus_comparison_filter(self: ComparisonExpr) -> str:
    """Convert to Milvus comparison filter string."""
    if isinstance(self.value, str):
        return f"{self.field} {self.operator} {self.sanitize_str(self.value)}"
    return f"{self.field} {self.operator} {self.value}"


def milvus_range_filter(self: RangeExpr) -> str:
    """Convert to Milvus range filter string."""
    match self.operator.lower():
        case "in":
            if isinstance(self.value, (Sequence, set)):
                # Handle list values for in operator
                if all(isinstance(v, str) for v in self.value):
                    values_str = ",".join(self.sanitize_str(v) for v in self.value)
                else:
                    values_str = ",".join(str(v) for v in self.value)
                return f"{self.field} in [{values_str}]"
            raise_query_error("in operator requires a sequence or set value")
        case "like":
            if isinstance(self.value, str):
                if "%" not in self.value:
                    raise_query_error("Milvus's like operator uses % for wildcard matching")
                return f"{self.field} like {self.sanitize_str(self.value)}"
            raise_query_error("like operator requires a string value")
        case _:
            raise_query_error(f"Unsupported range operator: {self.operator}")


def milvus_arithmetic_filter(self: ArithmeticExpr) -> str:
    """Convert to Milvus arithmetic filter string."""
    return (
        f"{self.field} {self.arithmetic_operator} {self.arithmetic_value}"
        + f"{self.comparison_operator} {self.comparison_value}"
    )


def milvus_null_filter(self: NullExpr) -> str:
    """Convert to Milvus null filter string."""
    if self.is_null:
        return f"{self.field} is null"
    return f"{self.field} is not null"


def milvus_json_filter(self: JSONExpr) -> str:
    """Convert to Milvus JSON filter string."""
    if isinstance(self.value, str):
        return f"{self.field}[{self.sanitize_str(self.key)}] {self.operator} {self.sanitize_str(self.value)}"
    return f"{self.field}[{self.sanitize_str(self.key)}] {self.operator} {self.value}"


def milvus_array_filter(self: ArrayExpr) -> str:
    """Convert to Milvus array filter string."""
    if self.index is not None:
        if isinstance(self.value, str):
            return f"{self.field}[{self.index}] {self.operator} {self.sanitize_str(self.value)}"
        else:
            return f"{self.field}[{self.index}] {self.operator} {self.value}"
    else:
        # Filter on the entire array field
        if isinstance(self.value, str):
            return f"{self.field} {self.operator} {self.sanitize_str(self.value)}"
        else:
            return f"{self.field} {self.operator} {self.value}"


def milvus_logical_filter(self: LogicalExpr) -> str:
    """Convert to Milvus logical filter string."""
    match self.operator.lower():
        case "not":
            if self.right is not None:
                raise_query_error("not operator should not have a right operand")
            return f"not ({self.left.to_expr('milvus')})"
        case "and" | "or":
            if self.right is None:
                raise_query_error(f"{self.operator} operator requires both left and right operands")
            return f"({self.left.to_expr('milvus')}) {self.operator} ({self.right.to_expr('milvus')})"
        case _:
            raise_query_error(f"Unsupported logical operator: {self.operator}")


def milvus_text_match_filter(self: MatchExpr) -> str:
    """Convert to Milvus text match filter string."""
    pattern = self.value
    match self.match_mode:
        case "exact":
            return f"TEXT_MATCH({self.field}, {self.sanitize_str(pattern)})"
        case "prefix":
            pattern = pattern + "%"
        case "suffix":
            pattern = "%" + pattern
        case "infix":
            pattern = "%" + pattern + "%"
        case _:
            raise_query_error(f"Unknown match mode: {self.match_mode}")
    return f"{self.field} like {self.sanitize_str(pattern)}"


milvus_def = QueryLanguageDefinition(
    comparison=milvus_comparison_filter,
    range=milvus_range_filter,
    arithmetic=milvus_arithmetic_filter,
    null=milvus_null_filter,
    json_filter=milvus_json_filter,
    array=milvus_array_filter,
    logical=milvus_logical_filter,
    text_match=milvus_text_match_filter,
)
