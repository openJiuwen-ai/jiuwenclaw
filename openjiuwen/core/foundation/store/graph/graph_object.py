# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Graph Database Configuration

Configuration models for graph database storage limits and indexing options
"""

from typing import Any, Optional, Self, Sequence, Union

from pydantic import BaseModel, Field, PrivateAttr, field_serializer, model_validator

from .utils import get_current_utc_timestamp, get_uuid


class BaseGraphObject(BaseModel):
    """Base class for all graph objects with common properties."""

    uuid: str = Field(default_factory=get_uuid, description="Unique 64-bit identifier for the object")
    created_at: int = Field(
        default_factory=get_current_utc_timestamp,
        description="Timestamp when the object was created",
    )
    user_id: str = Field(default="default_user", description="User identifier")
    obj_type: str = Field(default="", description="Object type classification")
    language: str = Field(
        default="cn",
        pattern=r"^[ce]n$",
        description="Language identifier for multi-analyzer support (cn/en)",
    )
    metadata: Optional[dict[str, Any]] = Field(
        default_factory=dict,
        description="JSON metadata for additional object properties",
    )
    content: str = Field(default="", description="Text content for full-text search")
    content_embedding: Optional[Sequence[float]] = Field(
        default=None, description="Dense vector embeddings for content semantic search", repr=False
    )
    content_bm25: Optional[Sequence[float]] = Field(
        default=None, description="BM25 sparse vector for full-text search", repr=False
    )

    # Version number of graph object definition
    _version: int = PrivateAttr(default=1, init=False)

    @property
    def version(self) -> int:
        """Version number of graph object definition"""
        return self._version

    @model_validator(mode="after")
    def set_default_values(self) -> Self:
        """Set default values"""
        if getattr(self, "valid_since", 0) == -1:
            setattr(self, "valid_since", self.created_at)
        if getattr(self, "metadata") is None:
            setattr(self, "metadata", dict())
        if getattr(self, "attributes", 0) is None:
            setattr(self, "attributes", dict())
        return self

    def fetch_embed_task(self) -> list[tuple[Self, str, str]]:
        """Fetch embedding task 3-tuples (self, attribute_name, content_to_embed)"""
        return [(self, "content_embedding", self.content)]


class NamedGraphObject(BaseGraphObject):
    """Base class for graph objects with names."""

    name: str = Field(default="", description="Name field")


class Entity(NamedGraphObject):
    """Node / Entity representing entity nodes in graph."""

    obj_type: str = Field(default="Entity", description="Object type classification")
    name_embedding: Optional[Sequence[float]] = Field(
        default=None, description="Dense vector embeddings for name semantic search", repr=False
    )
    relations: list[BaseGraphObject | str] = Field(
        default_factory=list, description="Array of relation IDs for this entity", repr=False
    )
    episodes: list[str] = Field(default_factory=list, description="Episodes where this entity is mentioned", repr=False)
    attributes: Optional[dict] = Field(default_factory=dict, description="Entity attributes")

    def fetch_embed_task(self) -> list[tuple[Self, str, str]]:
        """Fetch embedding task 3-tuples (self, attribute_name, content_to_embed)"""
        return [(self, "content_embedding", self.content), (self, "name_embedding", self.name)]

    @field_serializer("relations", "episodes")
    def serialize(self, graph_obj_list: list[BaseGraphObject | str], _info) -> list[str]:
        """Serialization helper"""
        return sorted({obj if isinstance(obj, str) else obj.uuid for obj in graph_obj_list})


class Relation(NamedGraphObject):
    """Edge / Relation entity representing relationships between entity nodes."""

    obj_type: str = Field(default="Relation", description="Object type classification")
    valid_since: int = Field(default=-1, description="Timestamp when the relation becomes valid")
    valid_until: int = Field(default=-1, description="Timestamp when the relation expires")
    offset_since: int = Field(default=0, description="Timezone offset of valid_since")
    offset_until: int = Field(default=0, description="Timezone offset of valid_until")
    lhs: BaseGraphObject | str = Field(description="Left-hand side entity UUID")
    rhs: BaseGraphObject | str = Field(description="Right-hand side entity UUID")

    def update_connected_entities(self) -> Self:
        """Update the connected entities"""
        for field_name in ["lhs", "rhs"]:
            connected_node: BaseGraphObject | str = getattr(self, field_name)
            if isinstance(connected_node, BaseGraphObject):
                if (self not in connected_node.relations) and (self.uuid not in connected_node.relations):
                    connected_node.relations.append(self)
        return self

    @field_serializer("lhs", "rhs")
    def serialize(self, ent: Union[Entity, str], _info) -> str:
        """Serialization helper"""
        if isinstance(ent, str):
            return ent
        return ent.uuid


class Episode(BaseGraphObject):
    """Episode nodes with no name"""

    obj_type: str = Field(default="Episode", description="Object type classification")
    valid_since: int = Field(default=-1, description="Timestamp when the episode becomes valid")
    entities: list[str] = Field(default_factory=list, description="Entities mentioned in this episode")

    @field_serializer("entities")
    def serialize(self, graph_obj_list: list[BaseGraphObject | str], _info) -> list[str]:
        """Serialization helper"""
        return sorted({obj if isinstance(obj, str) else obj.uuid for obj in graph_obj_list})
