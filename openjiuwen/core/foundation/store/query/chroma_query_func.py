# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Query Expression support for Chroma
"""

__all__ = ["chroma_def"]

from typing import Any, Sequence

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

# Global operator map for comparison operators
OPERATOR_MAP: dict[str, str] = {
    "==": "$eq",
    "!=": "$nin",  # Note: Chroma doesn't have $ne, will raise error
    ">": "$gt",
    ">=": "$gte",
    "<": "$lt",
    "<=": "$lte",
}


def chroma_comparison_filter(self: ComparisonExpr) -> dict[str, dict]:
    """Convert to Chroma comparison filter dictionary."""
    where_filter: dict[str, Any] = {}
    where_document_filter: dict[str, Any] = {}

    chroma_op = OPERATOR_MAP.get(self.operator)
    if chroma_op is None:
        raise_query_error(f"Unsupported comparison operator: {self.operator}")

    match chroma_op:
        case "$eq":
            where_filter[self.field] = self.value
        case "$nin":
            where_filter[self.field] = {chroma_op: [self.value]}
        case _:
            where_filter[self.field] = {chroma_op: self.value}

    return {"where": where_filter, "where_document": where_document_filter}


def chroma_range_filter(self: RangeExpr) -> dict[str, dict]:
    """Convert to Chroma range filter dictionary."""
    where_filter: dict[str, Any] = {}
    where_document_filter: dict[str, Any] = {}

    match self.operator.lower():
        case "in":
            if isinstance(self.value, (Sequence, set)):
                value_list = list(self.value) if not isinstance(self.value, list) else self.value
                where_filter[self.field] = {"$in": value_list}
            else:
                raise_query_error("in operator requires a sequence or set value")
        case _:
            raise_query_error(f"Unsupported range operator: {self.operator}")

    return {"where": where_filter, "where_document": where_document_filter}


def chroma_arithmetic_filter(self: ArithmeticExpr) -> dict[str, dict]:
    """Not supported by chroma"""
    raise_query_error(
        "Chroma does not support arithmetic operations in metadata filters. "
        "Consider pre-computing the arithmetic result and storing it as a metadata field."
    )


def chroma_null_filter(self: NullExpr) -> dict[str, dict]:
    """Not supported by chroma"""
    raise_query_error(
        "Chroma does not support nested JSON fields in metadata. "
        "Chroma only supports flat metadata (str, int, float, bool, None). "
        "Consider flattening your metadata structure (e.g., 'user.name' -> 'user_name')."
    )


def chroma_json_filter(self: JSONExpr) -> dict[str, dict]:
    """Not supported by chroma"""
    raise_query_error(
        "Chroma does not support nested JSON fields in metadata. "
        "Chroma only supports flat metadata (str, int, float, bool, None). "
        "Consider flattening your metadata structure (e.g., 'user.name' -> 'user_name')."
    )


def chroma_array_filter(self: ArrayExpr) -> dict[str, dict]:
    """Not supported by chroma"""
    raise_query_error(
        "Chroma does not support array indexing in metadata. "
        "Chroma only supports flat metadata (str, int, float, bool, None). "
        "Consider flattening your array structure (e.g., 'tags[0]' -> 'tag_0')."
    )


def chroma_logical_filter(self: LogicalExpr) -> dict[str, dict]:
    """Convert to Chroma logical filter dictionary."""
    where_filter: dict[str, Any] = {}
    where_document_filter: dict[str, Any] = {}

    # Get filters from left and right operands
    left_result: dict[str, Any] = self.left.to_expr("chroma")  # type: ignore
    right_result: dict[str, Any] = self.right.to_expr("chroma") if self.right else None  # type: ignore

    left_where = left_result.get("where", {})
    left_where_doc = left_result.get("where_document", {})
    right_where = right_result.get("where", {}) if right_result else {}
    right_where_doc = right_result.get("where_document", {}) if right_result else {}

    def combine_where_filters(op: str) -> dict[str, dict]:
        """Helper to combine where filters."""
        if left_where and right_where:
            return {op: [left_where, right_where]}
        elif left_where:
            return left_where
        elif right_where:
            return right_where
        return {}

    def combine_where_doc_filters(op: str) -> dict[str, dict]:
        """Helper to combine where_document filters."""
        if left_where_doc and right_where_doc:
            return {op: [left_where_doc, right_where_doc]}
        elif left_where_doc:
            return left_where_doc
        elif right_where_doc:
            return right_where_doc
        return {}

    match self.operator.lower():
        case "and":
            where_filter = combine_where_filters("$and")
            where_document_filter = combine_where_doc_filters("$and")
        case "or":
            where_filter = combine_where_filters("$or")
            where_document_filter = combine_where_doc_filters("$or")
        case _:
            raise_query_error(f"Unsupported logical operator: {self.operator}")

    if self.right is None:
        raise_query_error(f"{self.operator.lower()} operator requires both left and right operands")

    return {"where": where_filter, "where_document": where_document_filter}


def chroma_text_match_filter(self: MatchExpr) -> dict[str, dict]:
    """Convert to Chroma text match filter dictionary.

    Chroma uses where_document for full-text search.
    """
    where_filter: dict[str, Any] = {}
    where_document_filter: dict[str, Any] = {}

    pattern = self.value

    match self.match_mode:
        case "exact":
            # Exact match - use $contains for exact substring match
            # Note: $contains is case-sensitive in Chroma
            where_document_filter = {"$contains": pattern}
        case "prefix":
            # Match prefix - convert to regex
            regex_pattern = f"^{pattern}"
            where_document_filter = {"$regex": regex_pattern}
        case "suffix":
            # Match suffix - convert to regex
            regex_pattern = f"{pattern}$"
            where_document_filter = {"$regex": regex_pattern}
        case "infix":
            # Match anywhere - use $contains
            where_document_filter = {"$contains": pattern}
        case _:
            raise_query_error(f"Unknown match mode: {self.match_mode}")

    return {"where": where_filter, "where_document": where_document_filter}


chroma_def = QueryLanguageDefinition(
    comparison=chroma_comparison_filter,
    range=chroma_range_filter,
    arithmetic=chroma_arithmetic_filter,
    null=chroma_null_filter,
    json_filter=chroma_json_filter,
    array=chroma_array_filter,
    logical=chroma_logical_filter,
    text_match=chroma_text_match_filter,
)
