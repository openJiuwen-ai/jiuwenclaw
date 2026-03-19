# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Graph Memory Validate Input

Input validation for graph memory add and search operations.
"""

__all__ = ["validate_add_memory_input", "validate_search_input"]

from typing import Optional

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.memory.config.graph import EpisodeType

_STORE_TYPE = "graph mem store"


def validate_add_memory_input(
    user_id_max_length: int,
    src_type: EpisodeType,
    user_id: str,
    content_fmt_kwargs: Optional[dict] = None,
):
    """Preprocess episode content & retrieve relevant history episodes"""

    # Validate content_fmt_kwargs
    if content_fmt_kwargs is None:
        content_fmt_kwargs = {}
    else:
        if not (content_fmt_kwargs and isinstance(content_fmt_kwargs, dict)):
            raise build_error(
                StatusCode.MEMORY_STORE_VALIDATION_INVALID,
                store_type=_STORE_TYPE,
                error_msg="When supplied, content_fmt_kwargs must be of type dict[str, str] and not empty",
            )
    if not all(isinstance(k, str) and isinstance(v, str) and k and v for k, v in content_fmt_kwargs.items()):
        raise build_error(
            StatusCode.MEMORY_STORE_VALIDATION_INVALID,
            store_type=_STORE_TYPE,
            error_msg="content_fmt_kwargs must have non-empty keys and values of string type",
        )

    # Data Preparation
    if not isinstance(src_type, EpisodeType):
        raise build_error(
            StatusCode.MEMORY_STORE_VALIDATION_INVALID,
            store_type=_STORE_TYPE,
            error_msg="src_type must be one of [EpisodeType.CONVERSATION, EpisodeType.DOCUMENT, EpisodeType.JSON]",
        )
    if not (isinstance(user_id, str) and (1 <= len(user_id.strip()) <= user_id_max_length)):
        raise build_error(
            StatusCode.MEMORY_STORE_VALIDATION_INVALID,
            store_type=_STORE_TYPE,
            error_msg=f"user_id must be a string of length <= {user_id_max_length} (preferably UUID4)",
        )


def validate_search_input(query: str, user_id: str | list, settings: list[bool]) -> list[str]:
    """Validate GraphMemory.search method input"""
    if not (isinstance(query, str) and query.strip()):
        raise build_error(
            StatusCode.MEMORY_STORE_VALIDATION_INVALID,
            store_type=_STORE_TYPE,
            error_msg="query must be a non-empty string value",
        )
    if not isinstance(user_id, list):
        user_id = [user_id]
    if not all((isinstance(uid, str) and uid.strip() and len(uid) <= 32) for uid in user_id):
        raise build_error(
            StatusCode.MEMORY_STORE_VALIDATION_INVALID,
            store_type=_STORE_TYPE,
            error_msg="user_id must be a non-empty string of length <= 32 or a list of such strings",
        )
    if not all(isinstance(s, bool) for s in settings):
        raise build_error(
            StatusCode.MEMORY_STORE_VALIDATION_INVALID,
            store_type=_STORE_TYPE,
            error_msg="entity, relation, episode must be boolean values True or False",
        )
    return user_id
