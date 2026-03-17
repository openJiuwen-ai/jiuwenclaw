# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Result Ranking Options

Result ranking for hybrid search
"""

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

RANKER_CLS = dict()


def register_result_ranker_cls(name: str, weighted: Optional[Callable], rrf: Optional[Callable], **kwargs):
    """Register result ranker classes for database"""
    RANKER_CLS[name] = dict(weighted=weighted, rrf=rrf, **kwargs)


class BaseRankConfig(BaseModel, ABC):
    """Result Ranking Base Class"""

    name: str = "base"
    higher_is_better: bool = False

    @property
    @abstractmethod
    def args(self) -> Tuple[List, Dict]:
        """Get arguments for construction of ranker object"""

    @property
    def is_active(self) -> List[int]:
        """Determine which vectors (name_dense, content_dense, content_sparse) to search on"""
        return [1] * 3

    def get_ranker_cls(self, database: str) -> Any:
        """Get class for ranker object"""
        return RANKER_CLS.get(database, {}).get(self.name)


class WeightedRankConfig(BaseRankConfig):
    """Weighted Result Ranking"""

    name: str = "weighted"
    dense_name: float = Field(default=0.15, ge=0.0, le=1.0)
    dense_content: float = Field(default=0.6, ge=0.0, le=1.0)
    sparse_content: float = Field(default=0.25, ge=0.0, le=1.0)

    @property
    def args(self) -> Tuple[List, Dict]:
        """Get arguments for construction of ranker object"""
        weights = [w for w in (self.dense_name, self.dense_content, self.sparse_content) if w > 0]
        weight_sum = sum(weights)
        if weight_sum > 0:
            return [w / weight_sum for w in weights], {}
        return [], {}


class RRFRankConfig(BaseRankConfig):
    """RRF Result Ranking"""

    name: str = "rrf"
    higher_is_better: bool = True
    k: int = Field(default=40, ge=0)
    dense_name: bool = Field(default=True)
    dense_content: bool = Field(default=True)
    sparse_content: bool = Field(default=True)

    @property
    def args(self) -> Tuple[List, Dict]:
        """Get arguments for construction of ranker object"""
        return [self.k], {}

    @property
    def is_active(self) -> List[int]:
        """Determine which vectors (name_dense, content_dense, content_sparse) to search on"""
        return [int(w) for w in [self.dense_name, self.dense_content, self.sparse_content]]
