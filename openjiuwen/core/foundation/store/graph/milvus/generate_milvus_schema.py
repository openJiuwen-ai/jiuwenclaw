# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Milvus Graph Store Schema & Index Definition

Utility function for generating milvus graph store's schema & index
"""

from typing import Literal, Tuple

from pymilvus import CollectionSchema, DataType, Function, FunctionType, MilvusClient
from pymilvus.milvus_client import IndexParams

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.store.graph.constants import ENTITY_COLLECTION, EPISODE_COLLECTION, RELATION_COLLECTION
from openjiuwen.core.foundation.store.graph.database_config import GraphStoreIndexConfig, GraphStoreStorageConfig

# Use ICU analyzer for best multi-lingual support
icu_filter = ["asciifolding", "lowercase", {"type": "stemmer", "language": "english"}, "removepunct"]
icu_analyzer = {
    "tokenizer": "icu",
    "filter": icu_filter,
}
icu_analyzer_with_stopwords = icu_analyzer.copy()
icu_analyzer_with_stopwords["filter"] = icu_filter + [{"type": "stop", "stop_words": ["of", "to", "_english_"]}]
# White space analyzer for exact match
exact_match_analyzer = {"tokenizer": "whitespace"}


def generate_schema_and_index(
    milvus_client: MilvusClient,
    collection: Literal[ENTITY_COLLECTION, RELATION_COLLECTION, EPISODE_COLLECTION],
    storage_config: GraphStoreStorageConfig,
    embed_config: GraphStoreIndexConfig,
    *,
    dim: int = 0,
    dynamic_field: bool = True,
) -> Tuple[CollectionSchema, IndexParams]:
    """Generate the schema and index for milvus db"""
    if embed_config.index_type.index_type == "auto":
        index_type_milvus = "AUTOINDEX"
    else:
        index_type_milvus = embed_config.index_type.index_type.upper()
        index_variant = getattr(embed_config.index_type, "variant", None)
        if index_variant is not None:
            index_type_milvus = index_type_milvus + "_" + str(index_variant).upper()
    metric_type_milvus = embed_config.distance_metric.replace("dot", "ip").replace("euclidean", "l2").upper()
    schema = milvus_client.create_schema(enable_dynamic_field=dynamic_field)
    index_params = milvus_client.prepare_index_params()

    # Common fields
    schema.add_field("uuid", DataType.VARCHAR, max_length=storage_config.uuid, auto_id=False, is_primary=True)
    schema.add_field("created_at", DataType.INT64)
    schema.add_field("user_id", DataType.VARCHAR, max_length=storage_config.user_id)
    schema.add_field(
        "obj_type",
        DataType.VARCHAR,
        max_length=storage_config.obj_type,
        enable_analyzer=True,
        enable_match=True,
        analyzer_params=exact_match_analyzer,
    )
    schema.add_field("language", DataType.VARCHAR, max_length=storage_config.language)
    schema.add_field("metadata", DataType.JSON)

    # Add collection-specific fields
    if collection == ENTITY_COLLECTION:
        schema.add_field(
            "name",
            DataType.VARCHAR,
            max_length=storage_config.name,
            enable_analyzer=True,
            enable_match=True,
            analyzer_params=icu_analyzer,
        )
        schema.add_field("name_embedding", DataType.FLOAT_VECTOR, dim=dim)
        schema.add_field("attributes", DataType.JSON)
        index_params.add_index(
            field_name="name_embedding",
            index_name="semantic_embedding_name",
            index_type=index_type_milvus,
            metric_type=metric_type_milvus,
            **embed_config.extra_configs,
        )
        schema.add_field(
            "relations",
            DataType.ARRAY,
            element_type=DataType.VARCHAR,
            max_length=storage_config.uuid,
            max_capacity=storage_config.relations,
        )
        schema.add_field(
            "episodes",
            DataType.ARRAY,
            element_type=DataType.VARCHAR,
            max_length=storage_config.uuid,
            max_capacity=storage_config.episodes,
        )
    elif collection == RELATION_COLLECTION:
        schema.add_field("valid_since", DataType.INT64)
        schema.add_field("valid_until", DataType.INT64)
        schema.add_field("offset_since", DataType.INT8)
        schema.add_field("offset_until", DataType.INT8)
        schema.add_field("name", DataType.VARCHAR, max_length=storage_config.name)
        schema.add_field("lhs", DataType.VARCHAR, max_length=storage_config.uuid)
        schema.add_field("rhs", DataType.VARCHAR, max_length=storage_config.uuid)
    elif collection == EPISODE_COLLECTION:
        schema.add_field("valid_since", DataType.INT64)
        schema.add_field(
            "entities",
            DataType.ARRAY,
            element_type=DataType.VARCHAR,
            max_length=storage_config.uuid,
            max_capacity=storage_config.entities,
        )
    else:
        raise build_error(
            StatusCode.STORE_GRAPH_COLLECTION_NOT_SUPPORTED,
            collection=collection,
        )

    # Content field (summary, fact, etc.)
    schema.add_field(
        "content",
        DataType.VARCHAR,
        max_length=storage_config.content,
        enable_analyzer=True,
        analyzer_params=embed_config.bm25_analyzer_settings or icu_analyzer_with_stopwords,
    )
    if dim:
        schema.add_field("content_embedding", DataType.FLOAT_VECTOR, dim=dim)
        index_params.add_index(
            field_name="content_embedding",
            index_name="semantic_embedding_content",
            index_type=index_type_milvus,
            metric_type=metric_type_milvus,
            **embed_config.extra_configs,
        )
    schema.add_field("content_bm25", DataType.SPARSE_FLOAT_VECTOR)
    bm25_func = Function(
        name="bm25_func",
        input_field_names=["content"],
        output_field_names=["content_bm25"],
        function_type=FunctionType.BM25,
    )
    schema.add_function(bm25_func)
    index_params.add_index(
        field_name="content_bm25",
        index_name="sparse_inverted_index",
        index_type="SPARSE_INVERTED_INDEX",
        metric_type="BM25",
        params=embed_config.bm25_config.model_dump(),
    )

    return schema, index_params
