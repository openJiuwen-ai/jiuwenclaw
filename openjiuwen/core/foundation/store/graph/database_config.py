# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Graph Database Configuration

Configuration models for graph database storage limits and indexing options
"""

from typing import Any, Dict, Literal, Optional, Union

from pydantic import BaseModel, Field

from openjiuwen.core.foundation.store.vector_fields.base import VectorField

from .constants import ARRAY_LIMIT, VARCHAR_LIMIT


class BM25Config(BaseModel):
    """Graph Database BM25 Options"""

    bm25_b: float = Field(default=0.75, description="Document length normalization", ge=0, le=1)
    bm25_k1: float = Field(default=1.2, description="Term frequency saturation", ge=0)


class GraphStoreIndexConfig(BaseModel):
    """Graph Database Indexing Options"""

    index_type: VectorField = Field(description="Index type for Approximated Nearest Neighbour search")
    distance_metric: Literal["cosine", "euclidean", "dot"] = Field(description="Distance metric to use")
    extra_configs: Dict[str, Any] = Field(default_factory=dict, description="Extra configuration arguments")
    bm25_config: Union[BM25Config, BaseModel] = Field(default_factory=BM25Config, description="BM25 configuration")
    bm25_analyzer_settings: Optional[Dict[str, Any]] = Field(
        default=None, description="Analyzer setting for BM25 auto-indexing of content"
    )


class GraphStoreStorageConfig(BaseModel):
    """Graph Database Storage Limits"""

    uuid: int = Field(default=32, description="Max char length of uuid, keep at 32 for most case", **VARCHAR_LIMIT)
    name: int = Field(default=500, description="Max char length of names", **VARCHAR_LIMIT)
    content: int = Field(default=65535, description="Max char length of content, including episodes", **VARCHAR_LIMIT)
    language: int = Field(default=10, description="Max char length of language field", **VARCHAR_LIMIT)
    user_id: int = Field(default=32, description="Max char length of user id", **VARCHAR_LIMIT)
    entities: int = Field(
        default=4096, description="Max number of entities associated with each episode", **ARRAY_LIMIT
    )
    relations: int = Field(
        default=4096, description="Max number of relations associated with each entity", **ARRAY_LIMIT
    )
    episodes: int = Field(default=4096, description="Max number of episodes associated with each entity", **ARRAY_LIMIT)
    obj_type: int = Field(default=20, description="Max char length of entity/relation/episode type", **VARCHAR_LIMIT)
