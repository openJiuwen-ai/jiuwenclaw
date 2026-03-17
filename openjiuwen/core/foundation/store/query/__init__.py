# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Query Expression Definition

Provide high-level abstraction for combining queries together
"""

from .base import (
    ArithmeticExpr,
    ArrayExpr,
    ComparisonExpr,
    CustomExpr,
    JSONExpr,
    LogicalExpr,
    MatchExpr,
    NullExpr,
    QueryExpr,
    QueryLanguageDefinition,
    RangeExpr,
    array_index,
    chain_filters,
    eq,
    filter_user,
    gt,
    gte,
    in_list,
    is_not_null,
    is_null,
    json_key,
    lt,
    lte,
    ne,
    wildcard_match,
)
from .chroma_query_func import chroma_def
from .milvus_query_func import milvus_def
from .registry import register_database_query_language

for db_name, db_query_def in [("milvus", milvus_def), ("chroma", chroma_def)]:
    register_database_query_language(db_name, db_query_def)

__all__ = [
    "register_database_query_language",
    # Classes
    "QueryLanguageDefinition",
    "QueryExpr",
    "CustomExpr",
    "ComparisonExpr",
    "RangeExpr",
    "ArithmeticExpr",
    "NullExpr",
    "JSONExpr",
    "ArrayExpr",
    "LogicalExpr",
    "MatchExpr",
    # Convenience factory functions
    "eq",
    "ne",
    "gt",
    "lt",
    "gte",
    "lte",
    "in_list",
    "wildcard_match",
    "is_null",
    "is_not_null",
    "json_key",
    "array_index",
    "filter_user",
    "chain_filters",
]
