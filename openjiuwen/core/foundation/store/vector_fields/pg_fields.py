# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
PGVector Fields

Provides configuration classes for vector field indexes in PGVector.
PGVector supports HNSW and IVFFlat algorithms.
"""

from typing import Any, Literal

from pydantic import Field, field_validator

from .base import IS_CONSTRUCT, VectorField, create_extra_search_field


class PGVectorField(VectorField):
    """Index configuration for PGVector database.

    Supports HNSW (Hierarchical Navigable Small World) and IVFFlat algorithms.

    Attributes:
        m: Maximum number of connections per layer in the HNSW graph.
            Default: 16, Range: [2, 2000]
        ef_construction: Number of candidate neighbors to consider during index
            construction.
            Default: 64, Range: [1, ∞)
        ef_search: Number of candidates to explore during search.
            Default: 40, Range: [1, ∞)
        lists: Number of lists for IVFFlat index.
            Default: 100
        probes: Number of probes for IVFFlat search.
            Default: 1
    """

    database_type: Literal["pg"] = Field(default="pg", description="Database type", init=False)
    index_type: Literal["hnsw", "ivfflat"] = Field(default="hnsw", description="ANN index type")

    # HNSW parameters
    m: int = Field(
        default=16,
        ge=2,
        le=2000,
        description="Maximum number of connections per layer (HNSW)",
        **IS_CONSTRUCT,
    )
    ef_construction: int = Field(
        default=64,
        ge=1,
        description="Number of candidate neighbors considered during index construction (HNSW)",
        **IS_CONSTRUCT,
    )
    ef_search: int = Field(
        default=40,
        ge=1,
        description="Controls the search breadth for HNSW search queries.",
        **IS_CONSTRUCT,
    )

    # IVFFlat parameters
    lists: int = Field(
        default=100,
        ge=1,
        description="Number of lists (IVFFlat)",
        **IS_CONSTRUCT,
    )
    probes: int = Field(
        default=1,
        ge=1,
        description="Number of probes (IVFFlat)",
        **IS_CONSTRUCT,
    )

    extra_search: dict[str, Any] = create_extra_search_field()

    @field_validator("extra_search", mode="after")
    @classmethod
    def validate_kwargs(cls, search_dict: dict[str, Any]) -> dict[str, Any]:
        """Validate extra search arguments for PGVector."""
        return search_dict
