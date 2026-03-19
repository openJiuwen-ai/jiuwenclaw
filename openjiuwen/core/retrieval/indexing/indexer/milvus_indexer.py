# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Milvus Index Manager Implementation

Responsible for building, updating and deleting Milvus indices.
"""

import asyncio
from typing import Any, Dict, List, Optional

from pymilvus import DataType, Function, FunctionType, MilvusClient, MilvusException

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.store.vector_fields.milvus_fields import MilvusAUTO, MilvusVectorField
from openjiuwen.core.retrieval.common.callbacks import BaseCallback, TqdmCallback
from openjiuwen.core.retrieval.common.config import IndexConfig, VectorStoreConfig
from openjiuwen.core.retrieval.common.document import TextChunk
from openjiuwen.core.retrieval.embedding.base import Embedding
from openjiuwen.core.retrieval.indexing.indexer.base import Indexer
from openjiuwen.core.retrieval.utils.common import create_milvus_alias
from openjiuwen.core.retrieval.vector_store.milvus_store import MilvusVectorStore


class MilvusIndexer(Indexer):
    """Milvus index manager implementation"""

    def __init__(
        self,
        config: VectorStoreConfig,
        milvus_uri: str,
        milvus_token: Optional[str] = None,
        text_field: str = "content",
        vector_field: str | MilvusVectorField = "embedding",
        sparse_vector_field: str = "sparse_vector",
        metadata_field: str = "metadata",
        doc_id_field: str = "document_id",
        doc_index_callback: type[BaseCallback] = TqdmCallback,
        milvus_alias: Optional[str] = None,
        **kwargs,
    ):
        """
        Initialize Milvus index manager

        Args:
            config: Vector store configuration
            milvus_uri: Milvus URI
            milvus_token: Milvus Token (optional)
            text_field: Text field name
            vector_field: Vector field name (str) or definition (MilvusVectorField)
            sparse_vector_field: Sparse vector field name
            metadata_field: Metadata field name
            doc_index_callback: class of callback object to use, must be subclass of BaseCallback
            milvus_alias: Optional connection alias
        """
        self.milvus_uri = milvus_uri
        self.milvus_token = milvus_token
        self.text_field = text_field
        self.sparse_vector_field = sparse_vector_field
        self.metadata_field = metadata_field
        self.doc_id_field = doc_id_field
        self.database_name = config.database_name
        if isinstance(vector_field, str):
            self.vector_field = MilvusAUTO(vector_field=vector_field)
        elif isinstance(vector_field, MilvusVectorField):
            self.vector_field = vector_field
        else:
            raise build_error(
                StatusCode.RETRIEVAL_INDEXING_VECTOR_FIELD_INVALID,
                error_msg="vector_field must be either a str or MilvusVectorField instance",
            )
        if self.vector_field.index_type == "auto":
            self._construct_config = {}
        else:
            self._construct_config = self.vector_field.to_dict(stage="construct")
        self._distance_metric = config.distance_metric.replace("dot", "ip").replace("euclidean", "l2").upper()
        self._construct_config["metric_type"] = self._distance_metric
        self._search_config = self.vector_field.to_dict(stage="search")
        self.doc_index_callback = doc_index_callback
        if not isinstance(doc_index_callback, type) or not issubclass(doc_index_callback, BaseCallback):
            raise build_error(
                StatusCode.RETRIEVAL_EMBEDDING_CALLBACK_INVALID,
                error_msg=(
                    f"doc_index_callback in MilvusIndexer must be a subclass of BaseCallback, "
                    f"got {type(doc_index_callback)}"
                ),
            )

        self.milvus_alias = create_milvus_alias(milvus_alias, uri=milvus_uri, token=milvus_token)
        self._client = MilvusVectorStore.create_client(
            database_name=self.database_name,
            path_or_uri=self.milvus_uri,
            token=self.milvus_token,
            alias=self.milvus_alias,
        )

    @property
    def client(self) -> MilvusClient:
        """Get Milvus client"""
        return self._client

    @property
    def distance_metric(self) -> str:
        """Get raw distance metric string"""
        return self._distance_metric

    async def build_index(
        self,
        chunks: List[TextChunk],
        config: IndexConfig,
        embed_model: Optional[Embedding] = None,
        **kwargs,
    ) -> bool:
        """Build index"""
        try:
            collection_name = config.index_name

            # Ensure collection exists
            await self._ensure_collection(
                collection_name=collection_name,
                config=config,
                embed_model=embed_model,
            )

            # Raise exception if any doc_id already exists
            all_doc_ids = '["' + '","'.join({chunk.doc_id for chunk in chunks}) + '"]'
            results = self._client.query(
                collection_name, filter=f"{self.doc_id_field} in {all_doc_ids}", output_fields=[self.doc_id_field]
            )
            duplicate_doc_ids = sorted({result.get(self.doc_id_field) for result in results} - {None, ""})
            if duplicate_doc_ids:
                raise build_error(
                    StatusCode.RETRIEVAL_INDEXING_ADD_DOC_RUNTIME_ERROR,
                    error_msg="some documents with same doc_id already exist, if they are the same documents, "
                    f"please consider updating instead of adding. {duplicate_doc_ids=}",
                )

            # If vector index is needed, generate embeddings
            embeddings = None
            if config.index_type in ("vector", "hybrid"):
                if not embed_model:
                    raise build_error(
                        StatusCode.RETRIEVAL_INDEXING_EMBED_MODEL_NOT_FOUND,
                        error_msg="embed_model is required for vector/hybrid index type",
                    )
                texts = [chunk.text for chunk in chunks]
                embeddings = await embed_model.embed_documents(texts, callback_cls=self.doc_index_callback)
                for chunk, embedding in zip(chunks, embeddings):
                    chunk.embedding = embedding

            vector_store_config = VectorStoreConfig(
                store_provider="milvus", collection_name=collection_name, database_name=kwargs.pop("database_name", "")
            )

            vs_alias = f"{self.milvus_alias}_{collection_name}" if self.milvus_alias else None
            vector_store = MilvusVectorStore(
                config=vector_store_config,
                milvus_uri=self.milvus_uri,
                milvus_token=self.milvus_token,
                milvus_alias=vs_alias,
            )

            # Convert TextChunk to Milvus required fields, avoiding writing id_ field not defined in schema
            data = []
            for chunk in chunks:
                meta = chunk.metadata or {}
                item = {
                    "chunk_id": meta.get("chunk_id", chunk.id_),
                    self.doc_id_field: chunk.doc_id,
                    self.text_field: chunk.text,
                    self.metadata_field: meta,
                }
                if chunk.embedding is not None:
                    item[self.vector_field.vector_field] = chunk.embedding
                data.append(item)

            await vector_store.add(data=data)

            logger.info(f"Successfully built index {collection_name} with {len(chunks)} chunks")
            return True
        except Exception as e:
            # Stored data could be damaged with runtime errors ignored, therefore it is raised
            should_raise = [StatusCode.RETRIEVAL_INDEXING_ADD_DOC_RUNTIME_ERROR.code]
            # Re-raise all BaseError exceptions to preserve error information
            # This includes embedding errors, configuration errors, and runtime errors
            if isinstance(e, BaseError) and getattr(e, "code", None) in should_raise:
                raise e
            # For non-BaseError exceptions (e.g., from third-party libraries),
            # log and return False to avoid breaking the process
            logger.error(f"Failed to build index: {e}")
            return False

    async def update_index(
        self,
        chunks: List[TextChunk],
        doc_id: str,
        config: IndexConfig,
        embed_model: Optional[Embedding] = None,
        **kwargs,
    ) -> bool:
        """Update index"""
        try:
            # Delete old data first
            await self.delete_index(doc_id, config.index_name)

            # Flush to make delete visible before rebuild (Milvus eventual consistency)
            self._client.flush(collection_name=config.index_name)

            # Rebuild
            return await self.build_index(chunks, config, embed_model, **kwargs)
        except Exception as e:
            logger.error(f"Failed to update index: {e}")
            return False

    async def delete_index(
        self,
        doc_id: str,
        index_name: str,
        **kwargs,
    ) -> bool:
        """Delete index"""
        try:
            filter_expr = f'{self.doc_id_field} == "{doc_id}"'
            result = await asyncio.to_thread(
                self._client.delete,
                collection_name=index_name,
                filter=filter_expr,
            )

            if isinstance(result, dict):
                delete_count = result.get("delete_count", 0)
            else:
                delete_count = int(result) if result else 0

            logger.info(f"Deleted {delete_count} entries for doc_id={doc_id}")
            return delete_count > 0
        except MilvusException as e:
            logger.error(f"Failed to delete index entries: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error deleting index: {e}")
            return False

    async def index_exists(
        self,
        index_name: str,
    ) -> bool:
        """Check if index exists"""
        try:
            return await asyncio.to_thread(
                self._client.has_collection,
                collection_name=index_name,
            )
        except Exception as e:
            logger.error(f"Failed to check index existence: {e}")
            return False

    async def get_index_info(
        self,
        index_name: str,
    ) -> Dict[str, Any]:
        """Get index information"""
        try:
            if not await self.index_exists(index_name):
                return {"exists": False}

            # Prefer using collection stats to avoid query count triggering pagination errors
            stats = await asyncio.to_thread(
                self._client.get_collection_stats,
                collection_name=index_name,
            )
            row_count = 0
            if isinstance(stats, dict):
                # row_count may be a string, or in the stats list
                rc = stats.get("row_count")
                try:
                    row_count = int(rc or 0)
                except Exception:
                    row_count = 0
                if row_count == 0 and isinstance(stats.get("stats"), list):
                    for item in stats["stats"]:
                        if item.get("key") == "row_count":
                            try:
                                row_count = int(item.get("value") or 0)
                                break
                            except Exception:
                                logger.warning("Failed to get row count", exc_info=True)
                                continue

            collection_info = await asyncio.to_thread(
                self._client.describe_collection,
                collection_name=index_name,
            )

            return {
                "exists": True,
                "collection_name": index_name,
                "info": collection_info,
                "count": row_count,
            }
        except Exception as e:
            logger.error(f"Failed to get index info: {e}")
            return {"exists": False, "error": str(e)}

    async def _ensure_collection(
        self,
        collection_name: str,
        config: IndexConfig,
        embed_model: Optional[Embedding] = None,
    ) -> None:
        """Ensure collection exists, create if not exists"""
        if await self.index_exists(collection_name):
            return

        # Build schema
        schema = self._client.create_schema(
            auto_id=True,
            enable_dynamic_field=False,
        )

        index_params = self._client.prepare_index_params()

        # Document ID field
        schema.add_field(
            field_name=self.doc_id_field,
            datatype=DataType.VARCHAR,
            max_length=256,
        )

        # Add scalar index on document_id for fast deletion/filtering
        index_params.add_index(
            field_name=self.doc_id_field,
            index_type="INVERTED",  # Inverted index for VARCHAR
        )

        # Primary key field (auto-generated)
        schema.add_field(
            field_name="pk",
            datatype=DataType.INT64,
            is_primary=True,
            auto_id=True,
        )

        # Chunk ID field
        schema.add_field(
            field_name="chunk_id",
            datatype=DataType.VARCHAR,
            max_length=256,
        )

        # Add scalar index on document_id for fast deletion/filtering
        index_params.add_index(
            field_name="chunk_id",
            index_type="INVERTED",  # Inverted index for VARCHAR
        )

        # Text content field (enable analyzer for BM25)
        enable_bm25 = config.index_type in ("bm25", "hybrid")
        schema.add_field(
            field_name=self.text_field,
            datatype=DataType.VARCHAR,
            max_length=65535,
            enable_analyzer=enable_bm25,
            analyzer_params={"tokenizer": "jieba"} if enable_bm25 else {},
        )
        # BM25 sparse vector field for full-text search
        if enable_bm25:
            schema.add_field(
                field_name=self.sparse_vector_field,
                datatype=DataType.SPARSE_FLOAT_VECTOR,
            )

            # Add BM25 function to convert text to sparse vector
            bm25_function = Function(
                name="text_bm25_emb",
                input_field_names=[self.text_field],
                output_field_names=[self.sparse_vector_field],
                function_type=FunctionType.BM25,
            )
            schema.add_function(bm25_function)

            # Add sparse vector index for BM25
            index_params.add_index(
                field_name=self.sparse_vector_field,
                index_type="SPARSE_INVERTED_INDEX",
                metric_type="BM25",
            )

        # Vector field (if needed)
        if config.index_type in ("vector", "hybrid"):
            dimension = None
            if embed_model:
                dimension = embed_model.dimension

            # If dimension is 0 (placeholder) or None, get actual dimension through API call
            if (dimension is None or dimension == 0) and embed_model:
                try:
                    test_embedding = await embed_model.embed_query("X")
                    dimension = len(test_embedding)
                    logger.debug(f"Got dimension through API call: {dimension}")
                except Exception as e:
                    logger.warning(f"Unable to get dimension through API call: {e}")
                    dimension = None

            if dimension is None or dimension == 0:
                raise build_error(
                    StatusCode.RETRIEVAL_INDEXING_DIMENSION_NOT_FOUND,
                    error_msg="dimension is required for vector/hybrid index type",
                )

            schema.add_field(
                field_name=self.vector_field.vector_field,
                datatype=DataType.FLOAT_VECTOR,
                dim=dimension,
            )

            # Add dense vector index
            index_type = self.vector_field.index_type
            if index_type == "auto":
                index_type = "AUTOINDEX"
            else:
                index_type = self.vector_field.index_type.upper()
                index_variant = getattr(self.vector_field, "variant", None)
                if index_variant is not None:
                    index_type = index_type + "_" + index_variant.upper()
            index_params.add_index(
                field_name=self.vector_field.vector_field,
                index_type=index_type,
                **self._construct_config,
            )

        # Metadata field
        schema.add_field(
            field_name=self.metadata_field,
            datatype=DataType.JSON,
        )

        # Create collection
        await asyncio.to_thread(
            self._client.create_collection,
            collection_name=collection_name,
            schema=schema,
            index_params=index_params,
        )

        logger.info(f"Created collection: {collection_name}")

    def close(self) -> None:
        """Close index manager"""
        if self._client is None:
            return
        try:
            self._client.close()
        except Exception as e:
            logger.warning(f"Failed to close Milvus client: {e}")
