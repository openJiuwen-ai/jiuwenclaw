# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Common Utilities for Retrieval Module
"""

from typing import Callable, Hashable, Iterable, List, TypeVar

T = TypeVar("T")


def deduplicate(data: Iterable[T], key: Callable[[T], Hashable] = lambda x: x) -> List[T]:
    """Remove duplicates from an iterable while preserving order."""
    seen = set()
    result = []
    for item in data:
        k = key(item)
        if k not in seen:
            seen.add(k)
            result.append(item)
    return result
