# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Chroma Vector Fields

Provides configuration classes for vector field indexes in ChromaDB.
ChromaDB uses HNSW (Hierarchical Navigable Small World) algorithm for
approximate nearest neighbor search.
"""

from typing import Any, Literal

from pydantic import Field, field_validator
from pydantic_core import PydanticCustomError

from .base import IS_CONSTRUCT, VectorField, create_extra_search_field


class ChromaVectorField(VectorField):
    """HNSW index configuration for ChromaDB vector database.

    ChromaDB uses Hierarchical Navigable Small World (HNSW) algorithm for
    approximate nearest neighbor search. HNSW builds a multi-layer graph structure
    that provides excellent search performance and accuracy, especially for
    high-dimensional vectors.

    Attributes:
        max_neighbors: Maximum number of edges per node in the HNSW graph.
            Higher values improve accuracy but increase memory usage and construction time.
            Default: 16, Range: [2, 2048]
        ef_construction: Number of candidate neighbors to consider during index
            construction. Higher values improve graph quality but slow construction.
            Default: 100, Range: [1, ∞)
        ef_search: Number of candidates to explore during search. This directly controls
            the search breadth - higher values improve recall at the cost of increased latency.
            Default: 100, Range: [1, ∞)
        extra_search: Additional search parameters for fine-tuning performance:
            - resize_factor (int/float): Factor for dynamic graph resizing
            - num_threads (int): Number of threads for parallel search
            - batch_size (int): Batch size for batch search operations
            - sync_threshold (int): Threshold for synchronization operations
            Default: {} (empty dict)
    """

    database_type: Literal["chroma"] = Field(default="chroma", description="Database type", init=False)
    index_type: Literal["hnsw"] = Field(default="hnsw", description="ANN index type", init=False)
    max_neighbors: int = Field(
        default=16,
        ge=2,
        le=2048,
        description="Maximum number of edges each node can have in the graph",
        **IS_CONSTRUCT,
    )
    ef_construction: int = Field(
        default=100,
        ge=1,
        description="Number of candidate neighbors considered during index construction",
        **IS_CONSTRUCT,
    )
    ef_search: float = Field(
        default=100,
        ge=1,
        description="Controls the search breadth for Chroma HNSW search queries. "
        "If set to positive value, ef_search = number of candidates explored during search. "
        "High ef_search improves recall at the cost of latency.",
        **IS_CONSTRUCT,
    )
    extra_search: dict[str, Any] = create_extra_search_field()

    @field_validator("extra_search", mode="after")
    @classmethod
    def validate_kwargs(cls, search_dict: dict[str, Any]) -> dict[str, Any]:
        """Validate extra search arguments for ChromaDB.

        Ensures that all optional search parameters in extra_search have the correct
        types. Only validates parameters that are present in the dictionary; missing
        parameters are allowed and will use ChromaDB defaults.

        Args:
            search_dict: Dictionary containing extra search parameters. May include:
                - resize_factor (int or float): Factor for dynamic graph resizing
                - num_threads (int): Number of threads for parallel search
                - batch_size (int): Batch size for batch search operations
                - sync_threshold (int): Threshold for synchronization operations

        Returns:
            dict[str, Any]: The validated search dictionary (unchanged if valid).
        """
        if not isinstance(search_dict.get("resize_factor", 1.2), (int, float)):
            raise PydanticCustomError(
                "invalid_resize_factor",
                f"{cls.__name__}.extra_search field received invalid resize_factor, neither int nor float",
                search_dict,
            )
        for int_attr in ["num_threads", "batch_size", "sync_threshold"]:
            if not isinstance(search_dict.get(int_attr, 1), int):
                raise PydanticCustomError(
                    f"invalid_{int_attr}",
                    f"{cls.__name__}.extra_search field received invalid {int_attr}, not an integer",
                    search_dict,
                )
        return search_dict
