# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Graph Config

Configuration types for memory graph (e.g. episode source types).
"""

from enum import Enum
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from openjiuwen.core.foundation.store.graph.result_ranking import BaseRankConfig, RRFRankConfig, WeightedRankConfig
from openjiuwen.core.foundation.store.query import QueryExpr


class EpisodeType(Enum):
    """Type of possible sources (conversation/document/json)"""

    CONVERSATION: int = 0
    DOCUMENT: int = 1
    JSON: int = 2


class BaseStrategy(BaseModel):
    """Retrieval strategy during add memory"""

    top_k: int = Field(default=3, ge=1)
    min_score: float = Field(default=0.3)
    rank_config: BaseRankConfig = Field(default_factory=RRFRankConfig)


class RetrievalStrategy(BaseStrategy):
    """Retrieval strategy during add memory"""

    same_kind: bool = Field(default=False)


class EpisodeRetrievalStrategy(RetrievalStrategy):
    """Retrieval strategy for episodes during add memory"""

    same_kind: bool = Field(default=False)
    exclude_future_results: bool = Field(default=True)
    rank_config: BaseRankConfig = Field(default=RRFRankConfig())


class AddMemStrategy(BaseModel):
    """Strategy for adding graph memory"""

    chinese_entity: bool = Field(
        default=True,
        description="Whether to use Chinese for entity extraction regardless of episode language "
        "(recommended to be True for small Qwen3 models)",
    )
    chinese_entity_dedupe: bool = Field(
        default=False,
        description="Whether to use Chinese for entity deduplication regardless of episode language",
    )
    chinese_relation: bool = Field(
        default=False,
        description="Whether to use Chinese for relation extraction regardless of episode language "
        "(not recommended usually)",
    )
    skip_uuid_dedupe: bool = Field(default=False, description="Whether to skip uuid4 de-duplication")
    recall_episode: EpisodeRetrievalStrategy = Field(default=EpisodeRetrievalStrategy())
    recall_entity: RetrievalStrategy = Field(
        default=RetrievalStrategy(
            rank_config=WeightedRankConfig(name_dense=0.7, content_dense=0.1, content_sparse=0.2),
            min_score=0.1,
        )
    )
    recall_relation: RetrievalStrategy = Field(default=RetrievalStrategy(rank_config=RRFRankConfig(), min_score=0.05))
    summary_target: int = Field(
        default=250, description="Target word/character count for entity summaries", ge=10, le=2000
    )
    merge_entities: bool = Field(default=True, description="Whether to perform entity merging")
    merge_relations: bool = Field(default=True, description="Whether to perform relation merging")
    merge_filter: bool = Field(default=True, description="Whether to filter relations after entity merging")


class SearchConfig(BaseStrategy):
    """Config for searching memory"""

    bfs_k: int = Field(default=3, ge=1)
    bfs_depth: int = Field(default=0, ge=0)
    filter_expr: Optional[QueryExpr] = Field(default=None)
    output_fields: Optional[List[str]] = Field(default=None)
    rerank: bool = Field(default=False)
    language: Literal["cn", "en"] = Field(default="en")


DEFAULT_STRATEGY = AddMemStrategy()
