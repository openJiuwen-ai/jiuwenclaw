# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Result Ranking Options for Hybrid Search

Configuration and registry for result ranking in hybrid search.
"""

from abc import ABC, abstractmethod
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field

RANKER_CLS = dict()


def register_result_ranker_cls(name: str, weighted: Optional[Callable], rrf: Optional[Callable], **kwargs):
    """Register result ranker classes for a graph store backend.

    Args:
        name (str): Database backend identifier (e.g. "milvus").
        weighted (Optional[Callable]): Callable that builds a weighted ranker. Defaults to None.
        rrf (Optional[Callable]): Callable that builds an RRF ranker. Defaults to None.
        **kwargs: Additional name-to-callable entries for this backend.
    """
    RANKER_CLS[name] = dict(weighted=weighted, rrf=rrf, **kwargs)


class BaseRankConfig(BaseModel, ABC):
    """Base configuration for result ranking in hybrid graph search.

    Channels: name_dense (name embedding), content_dense (content embedding),
    content_sparse (content BM25).
    """

    name: str = "base"
    higher_is_better: bool = False

    @property
    @abstractmethod
    def args(self) -> tuple[list, dict]:
        """(positional_args, keyword_args) for constructing the ranker."""

    @property
    def is_active(self) -> list[int]:
        """Per-channel flags (name_dense, content_dense, content_sparse); non-zero means active."""
        return [1] * 3

    def get_ranker_cls(self, database: str) -> Any:
        """Get the ranker class for the given backend.

        Args:
            database (str): Backend name as registered via register_result_ranker_cls.

        Returns:
            Callable or None: Ranker class for this config's name, or None if not registered.
        """
        return RANKER_CLS.get(database, {}).get(self.name)


class WeightedRankConfig(BaseRankConfig):
    """Weighted combination of scores from name_dense, content_dense, content_sparse.

    name_dense: weight for name embedding;
    content_dense: weight for content embedding;
    content_sparse: weight for content BM25.
    Weights are normalized; 0 excludes that channel.
    """

    name: str = "weighted"
    name_dense: float = Field(default=0.15, ge=0.0, le=1.0)
    content_dense: float = Field(default=0.6, ge=0.0, le=1.0)
    content_sparse: float = Field(default=0.25, ge=0.0, le=1.0)

    @property
    def args(self) -> tuple[list, dict]:
        """Normalized weights (zeros dropped) and empty kwargs."""
        weights = [w for w in (self.name_dense, self.content_dense, self.content_sparse) if w > 0]
        weight_sum = sum(weights)
        if weight_sum > 0:
            return [w / weight_sum for w in weights], {}
        return [], {}


class RRFRankConfig(BaseRankConfig):
    """RRF (Reciprocal Rank Fusion) for merging ranked lists from multiple channels.

    name_dense, content_dense, content_sparse: whether to include each channel
    (name embedding, content embedding, content BM25) in the fusion.
    """

    name: str = "rrf"
    higher_is_better: bool = True
    k: int = Field(default=40, ge=0)
    name_dense: bool = Field(default=True)
    content_dense: bool = Field(default=True)
    content_sparse: bool = Field(default=True)

    @property
    def args(self) -> tuple[list, dict]:
        """RRF constant k and empty kwargs."""
        return [self.k], {}

    @property
    def is_active(self) -> list[int]:
        """One per channel (name_dense, content_dense, content_sparse); 1 if True else 0."""
        return [int(w) for w in [self.name_dense, self.content_dense, self.content_sparse]]
