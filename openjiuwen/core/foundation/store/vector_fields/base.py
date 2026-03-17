# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Base Vector Fields

Provides base classes and utilities for configuring vector field indexes
in vector databases (Chroma and Milvus). Supports stage-based field filtering
for construction and search operations.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.fields import FieldInfo

DEFAULT = None
IS_SEARCH = {"json_schema_extra": {"stage": "search"}}
IS_CONSTRUCT = {"json_schema_extra": {"stage": "construct"}}


def create_extra_construct_field() -> FieldInfo:
    """Create the pydantic Field for extra_construct"""
    return Field(
        default_factory=dict,
        description="Extra index-building arguments to pass into database during construction",
        **IS_CONSTRUCT,
    )


def create_extra_search_field() -> FieldInfo:
    """Create the pydantic Field for extra_search"""
    return Field(
        default_factory=dict,
        description="Extra index-building arguments to pass into database during search",
        **IS_SEARCH,
    )


class VectorField(BaseModel):
    """Base class for configuring Approximate Nearest Neighbor (ANN) search in vector databases.

    Provides a common interface for configuring vector field indexes across different
    database backends. Supports stage-based field filtering to separate construction
    and search parameters.

    Attributes:
        vector_field: Name of the vector field in the database schema.
        database_type: Type of vector database (milvus or chroma).
        index_type: Type of ANN index algorithm (auto, hnsw, flat, ivf, or scann).
    """

    vector_field: str = Field(default="embedding", description="Vector field name")
    database_type: Literal["milvus", "chroma"] = Field(description="Database type")
    index_type: Literal["auto", "hnsw", "flat", "ivf", "scann"] = Field(description="ANN index type")

    # Disallow extra arguments to stop typo, validate on assignment to prevent user error
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    @staticmethod
    def should_keep(finfo: FieldInfo, stage: Literal["search", "construct"]) -> bool:
        """Determine whether a field should be kept for a given stage.

        Fields can be marked with a "stage" in their json_schema_extra to indicate
        they are only relevant for "construct" or "search" operations.

        Args:
            finfo: Pydantic FieldInfo containing field metadata.
            stage: The stage to check: "search" (search-only), or "construct" (construction-only).

        Returns:
            bool: True if the field should be kept for this stage, False otherwise.
        """
        return stage == (finfo.json_schema_extra or {}).get("stage")

    def to_dict(self, stage: Literal["search", "construct"]) -> dict[str, Any]:
        """Convert the vector field configuration to a dictionary for a specific stage.

        Filters fields based on the specified stage and merges extra arguments
        for that stage. Removes internal fields (database_type, index_type, vector_field)
        and stage-specific extra fields that don't match the current stage.

        Args:
            stage: The stage to generate configuration for:
                - "search": Fields and extra_search arguments for search operations
                - "construct": Fields and extra_construct arguments for index construction

        Returns:
            dict[str, Any]: Dictionary containing only the relevant fields for the stage,
                with extra arguments merged in.
        """
        cls = self.__class__
        # Filter model fields by stages they apply to, then filter out None
        model_filtered = (
            (attr, getattr(self, attr, None))
            for attr, finfo in cls.model_fields.items()
            if self.should_keep(finfo, stage)
        )
        model_content = {attr: val for attr, val in model_filtered if val is not None}

        # Unpack the extra arguments for each stage
        for attr in ["database_type", "index_type", "vector_field", "variant"]:
            model_content.pop(attr, None)
        extra = model_content.pop(f"extra_{stage}", {})
        return model_content | extra
