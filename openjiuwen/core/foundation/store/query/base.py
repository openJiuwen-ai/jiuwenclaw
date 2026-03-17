# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Query Expression Definition
"""

from abc import ABC, abstractmethod
from typing import Any, Callable, List, Literal, Never, Optional, Sequence

from pydantic import BaseModel, Field

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error

QUERY_EXPR_FUNCTIONS: dict[str, "QueryLanguageDefinition"] = dict()


class QueryLanguageDefinition(BaseModel):
    """Definition of a database-specific query language"""

    comparison: Callable[["QueryExpr"], str]
    range: Callable[["QueryExpr"], str]
    arithmetic: Callable[["QueryExpr"], str]
    null: Callable[["QueryExpr"], str]
    json_filter: Callable[["QueryExpr"], str]
    array: Callable[["QueryExpr"], str]
    logical: Callable[["QueryExpr"], str]
    text_match: Callable[["QueryExpr"], str]


def raise_query_error(reason: str) -> Never:
    """Raise query error"""
    raise build_error(StatusCode.RETRIEVAL_VECTOR_STORE_QUERY_INVALID, error_msg=reason)


def validate_language_registered(name: str):
    """Validate that language has been registered"""
    if name not in QUERY_EXPR_FUNCTIONS:
        raise_query_error(f"Database query language {name} not registered via register_database_query_language method")


class QueryExpr(BaseModel, ABC):
    """Base class for all query filters."""

    def __and__(self, other: "QueryExpr") -> "LogicalExpr":
        """Combine filters with and operator."""
        return LogicalExpr(operator="and", left=self, right=other)

    def __or__(self, other: "QueryExpr") -> "LogicalExpr":
        """Combine filters with or operator."""
        return LogicalExpr(operator="or", left=self, right=other)

    def __xor__(self, other: "QueryExpr") -> "LogicalExpr":
        """Combine filters with xor operator (implemented as or with negation)."""
        return LogicalExpr(operator="xor", left=self, right=other)

    def __invert__(self) -> "LogicalExpr":
        """Negate the filter with not operator."""
        return LogicalExpr(operator="not", left=self, right=None)

    @staticmethod
    def sanitize_str(value: Any) -> str:
        """Sanitize string values"""
        value = str(value)
        if '"' in value:
            value = value.replace('"', '\\"')
            return f'"{value}"'
        return f'"{value}"'

    @abstractmethod
    def to_expr(self, database: str) -> Any:
        """Convert to database-specific expression format"""
        raise_query_error("Base QueryExpr should not be used directly")


class CustomExpr(QueryExpr):
    """Expr for custom expressions."""

    expr: str | Any = Field(..., description="Custom expression")

    def to_expr(self, database: str) -> Any:
        """Convert to database-specific expression format"""
        return self.expr


class ComparisonExpr(QueryExpr):
    """Expr for comparison operations (==, !=, >, <, >=, <=)."""

    field: str = Field(..., description="Field name to filter on")
    operator: str = Field(..., description="Comparison operator")
    value: Any = Field(..., description="Value to compare against")

    def to_expr(self, database: str) -> Any:
        """Convert to database-specific expression format"""
        validate_language_registered(database)
        return QUERY_EXPR_FUNCTIONS[database].comparison(self)


class RangeExpr(QueryExpr):
    """Expr for range operations (in, like)."""

    field: str = Field(..., description="Field name to filter on")
    operator: str = Field(..., description="Range operator (in or like)")
    value: Sequence | set | str = Field(..., description="Value(s) for range operation")

    def to_expr(self, database: str) -> Any:
        """Convert to database-specific expression format"""
        validate_language_registered(database)
        return QUERY_EXPR_FUNCTIONS[database].range(self)


class ArithmeticExpr(QueryExpr):
    """Expr for arithmetic operations with field values."""

    field: str = Field(..., description="Field name to filter on")
    arithmetic_operator: str = Field(..., description="Arithmetic operator (+, -, *, /, %, **)")
    arithmetic_value: int | float = Field(..., description="Value for arithmetic operation")
    comparison_operator: str = Field(..., description="Comparison operator after arithmetic")
    comparison_value: int | float = Field(..., description="Value to compare against")

    def to_expr(self, database: str) -> Any:
        """Convert to database-specific expression format"""
        validate_language_registered(database)
        return QUERY_EXPR_FUNCTIONS[database].arithmetic(self)


class NullExpr(QueryExpr):
    """Expr for null value checks (is null, is not null)."""

    field: str = Field(..., description="Field name to filter on")
    is_null: bool = Field(..., description="True for is null, False for is not null")

    def to_expr(self, database: str) -> Any:
        """Convert to database-specific expression format"""
        validate_language_registered(database)
        return QUERY_EXPR_FUNCTIONS[database].null(self)


class JSONExpr(QueryExpr):
    """Expr for JSON field operations."""

    field: str = Field(..., description="JSON field name")
    key: str = Field(..., description="JSON key to filter on")
    operator: str = Field(..., description="Comparison operator")
    value: Any = Field(..., description="Value to compare against")

    def to_expr(self, database: str) -> Any:
        """Convert to database-specific expression format"""
        validate_language_registered(database)
        return QUERY_EXPR_FUNCTIONS[database].json_filter(self)


class ArrayExpr(QueryExpr):
    """Expr for array field operations."""

    field: str = Field(..., description="Array field name")
    index: Optional[int] = Field(None, description="Array index to filter on")
    operator: str = Field(..., description="Comparison operator")
    value: Any = Field(..., description="Value to compare against")

    def to_expr(self, database: str) -> Any:
        """Convert to database-specific expression format"""
        validate_language_registered(database)
        return QUERY_EXPR_FUNCTIONS[database].array(self)


class LogicalExpr(QueryExpr):
    """Expr for logical operations (and, or, not)."""

    operator: str = Field(..., description="Logical operator (and, or, not)")
    left: QueryExpr = Field(..., description="Left operand filter")
    right: Optional[QueryExpr] = Field(None, description="Right operand filter (not needed for not)")

    def to_expr(self, database: str) -> Any:
        """Convert to database-specific expression format"""
        validate_language_registered(database)
        return QUERY_EXPR_FUNCTIONS[database].logical(self)


class MatchExpr(QueryExpr):
    """Expr for text match operations."""

    field: str = Field(..., description="Field name")
    value: str = Field(..., description="Text value")
    match_mode: Literal["prefix", "suffix", "infix", "exact"] = Field(default="exact", description="Matching mode")

    def to_expr(self, database: str) -> Any:
        """Convert to database-specific expression format"""
        validate_language_registered(database)
        return QUERY_EXPR_FUNCTIONS[database].text_match(self)


# Convenience factory functions for creating filters
def eq(field: str, value: Any) -> ComparisonExpr:
    """Create an equality filter."""
    return ComparisonExpr(field=field, operator="==", value=value)


def ne(field: str, value: Any) -> ComparisonExpr:
    """Create a not-equal filter."""
    return ComparisonExpr(field=field, operator="!=", value=value)


def gt(field: str, value: int | float) -> ComparisonExpr:
    """Create a greater-than filter."""
    return ComparisonExpr(field=field, operator=">", value=value)


def lt(field: str, value: int | float) -> ComparisonExpr:
    """Create a less-than filter."""
    return ComparisonExpr(field=field, operator="<", value=value)


def gte(field: str, value: int | float) -> ComparisonExpr:
    """Create a greater-than-or-equal filter."""
    return ComparisonExpr(field=field, operator=">=", value=value)


def lte(field: str, value: int | float) -> ComparisonExpr:
    """Create a less-than-or-equal filter."""
    return ComparisonExpr(field=field, operator="<=", value=value)


def in_list(field: str, values: Sequence | set) -> RangeExpr | ComparisonExpr:
    """Create an in filter for a list of values."""
    if len(values) == 1:
        return ComparisonExpr(field=field, operator="==", value=next(iter(values)))
    return RangeExpr(field=field, operator="in", value=values)


def wildcard_match(field: str, pattern: str, operator: str = "wildcard") -> RangeExpr:
    """Create a filter for wildcard matching (database must support this), put * in pattern string."""
    return RangeExpr(field=field, operator=operator, value=pattern)


def is_null(field: str) -> NullExpr:
    """Create an IS NULL filter."""
    return NullExpr(field=field, is_null=True)


def is_not_null(field: str) -> NullExpr:
    """Create an IS NOT NULL filter."""
    return NullExpr(field=field, is_null=False)


def json_key(field: str, key: str, operator: str, value: Any) -> JSONExpr:
    """Create a JSON key filter."""
    return JSONExpr(field=field, key=key, operator=operator, value=value)


def array_index(field: str, index: int, operator: str, value: Any) -> ArrayExpr:
    """Create an array index filter."""
    return ArrayExpr(field=field, index=index, operator=operator, value=value)


def filter_user(users: str | List[str], user_id_field: str = "user_id") -> RangeExpr | ComparisonExpr:
    """Create an in filter for one or multiple user id."""
    if isinstance(users, str):
        users = [users]
    return in_list(user_id_field, users)


def chain_filters(filters: List[QueryExpr]) -> Optional[LogicalExpr]:
    """Chain filters with AND operator (&), returns None for empty input."""
    if filters:
        final_expr = filters.pop(0)
        for expr in filters:
            final_expr = final_expr & expr
        return final_expr
    return None
