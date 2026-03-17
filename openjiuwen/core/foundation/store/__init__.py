# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Foundation store module, supporting various kind of relational & vector databases

Lazy-loading module using PEP 562 __getattr__ to avoid unnecessary import of heavy dependencies like SQLAlchemy.
"""

import importlib
from typing import TYPE_CHECKING

from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore

# Vector store exports (the ones that don't depend on SQLAlchemy)
from openjiuwen.core.foundation.store.base_vector_store import (
    BaseVectorStore,
    CollectionSchema,
    FieldSchema,
    VectorDataType,
    VectorSearchResult,
)
from openjiuwen.core.foundation.store.kv.in_memory_kv_store import InMemoryKVStore


def create_vector_store(store_type: str, **kwargs) -> BaseVectorStore | None:
    """Factory method for creating a specific vector store"""
    if store_type == "chroma":
        from openjiuwen.core.foundation.store.vector.chroma_vector_store import ChromaVectorStore

        return ChromaVectorStore(**kwargs)
    elif store_type == "milvus":
        from openjiuwen.core.foundation.store.vector.milvus_vector_store import MilvusVectorStore

        return MilvusVectorStore(**kwargs)
    elif store_type == "gaussvector":
        from openjiuwen.core.foundation.store.vector.gauss_vector_store import GaussVectorStore

        return GaussVectorStore(**kwargs)
    else:
        return None


__all__ = [
    "BaseKVStore",
    "BaseVectorStore",
    "InMemoryKVStore",
    "VectorSearchResult",
    "CollectionSchema",
    "FieldSchema",
    "VectorDataType",
    "create_vector_store",
    # Submodules
    "vector_fields",
    "vector",
    "query",
    "object",
    "kv",
    "graph",
    "db",
    # Lazy attributes
    "BaseDbStore",
    "DbBasedKVStore",
    "DefaultDbStore",
]

# Lazy-loaded attributes (SQLAlchemy-dependent)
_LAZY_ATTRIBUTES = [
    "BaseDbStore",
    "DbBasedKVStore",
    "DefaultDbStore",
]


def __getattr__(name: str):
    """
    Lazy import for SQLAlchemy-dependent modules using PEP 562.

    This allows importing foundation.store.query without pulling in SQLAlchemy
    dependencies that are only needed for BaseDbStore, DbBasedKVStore, etc.
    """
    if name in _LAZY_ATTRIBUTES:
        match name:
            case "BaseDbStore":
                from openjiuwen.core.foundation.store.base_db_store import BaseDbStore

                return BaseDbStore
            case "DbBasedKVStore":
                from openjiuwen.core.foundation.store.kv.db_based_kv_store import DbBasedKVStore

                return DbBasedKVStore
            case "DefaultDbStore":
                from openjiuwen.core.foundation.store.db.default_db_store import DefaultDbStore

                return DefaultDbStore
    return importlib.import_module("." + name, __name__)


def __dir__():
    """
    Support dir() calls by returning __all__ plus any lazy-loaded attributes.
    """
    return __all__ + _LAZY_ATTRIBUTES


if TYPE_CHECKING:
    from openjiuwen.core.foundation.store.base_db_store import BaseDbStore
    from openjiuwen.core.foundation.store.db.default_db_store import DefaultDbStore
    from openjiuwen.core.foundation.store.kv.db_based_kv_store import DbBasedKVStore
