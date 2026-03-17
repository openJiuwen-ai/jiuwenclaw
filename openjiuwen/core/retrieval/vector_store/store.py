# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import TYPE_CHECKING

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.retrieval.common.config import StoreType, VectorStoreConfig
from openjiuwen.core.retrieval.vector_store.base import VectorStore

if TYPE_CHECKING:
    from openjiuwen.core.retrieval.vector_store.chroma_store import ChromaVectorStore
    from openjiuwen.core.retrieval.vector_store.milvus_store import MilvusVectorStore
    from openjiuwen.core.retrieval.vector_store.pg_store import PGVectorStore


def create_vector_store(config: VectorStoreConfig, **kwargs) -> VectorStore:
    """
    Factory to create vector stores dynamically based on configuration.
    """
    if config.store_provider == StoreType.Milvus:
        from openjiuwen.core.retrieval.vector_store.milvus_store import MilvusVectorStore

        return MilvusVectorStore(config=config, **kwargs)

    elif config.store_provider == StoreType.Chroma:
        from openjiuwen.core.retrieval.vector_store.chroma_store import ChromaVectorStore

        return ChromaVectorStore(config=config, **kwargs)

    elif config.store_provider == StoreType.PGVector:
        from openjiuwen.core.retrieval.vector_store.pg_store import PGVectorStore

        return PGVectorStore(config=config, **kwargs)

    else:
        raise build_error(
            StatusCode.RETRIEVAL_VECTOR_STORE_PROVIDER_INVALID,
            error_msg=f"unavailable vector store provider: {config.store_provider},"
            f"and available providers are: {', '.join([m.value for m in StoreType])}",
        )
