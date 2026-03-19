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


def create_milvus_alias(alias: str | None, uri: str, user: str = "", token: str | None = None) -> str:
    """Generate Milvus connection alias if not provided"""
    import hashlib

    if alias:
        return alias
    auth_info = user or "noauth"
    if token:
        md5 = hashlib.new("md5", usedforsecurity=False)
        md5.update(token.encode())
        auth_info = md5.hexdigest()
    return "-".join(elem for elem in ["kb", uri, auth_info] if elem)
