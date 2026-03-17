# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Memory system schemas for different approaches."""

from typing import Optional, List, Union
from datetime import datetime, timezone
import hashlib
from pydantic import BaseModel, Field, ConfigDict

from ..core.schema import VectorNode


class BaseMemory(BaseModel):
    """Abstract base class for all memory types."""
    workspace_id: str = Field(default="default", description="Workspace/user identifier")

    def to_vector_node(self) -> VectorNode:
        """Convert memory to vector node for storage.

        Returns:
            VectorNode representation

        Raises:
            NotImplementedError: Subclasses must implement
        """
        raise NotImplementedError("Subclasses must implement to_vector_node")

    @classmethod
    def from_vector_node(cls, node: VectorNode) -> "BaseMemory":
        """Convert vector node back to memory.

        Args:
            node: VectorNode from storage

        Returns:
            Memory instance

        Raises:
            NotImplementedError: Subclasses must implement
        """
        raise NotImplementedError("Subclasses must implement from_vector_node")


# ============================================================================
# ACE Memory Schemas
# ============================================================================

class ACEMemory(BaseMemory):
    """ACE Memory schema."""

    id: str = Field(description="Memory identifier")
    section: str = Field(description="Memory section/category")
    content: str = Field(description="Memory content")
    helpful: int = Field(default=0, description="Helpful count")
    harmful: int = Field(default=0, description="Harmful count")
    neutral: int = Field(default=0, description="Neutral count")
    created_at: datetime = Field(description="Creation timestamp")
    updated_at: datetime = Field(description="Last update timestamp")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "mem_001",
                "section": "python",
                "content": "Use lru_cache for memoization",
                "helpful": 0,
                "harmful": 0,
                "neutral": 0,
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
            }
        }
    )


    def to_vector_node(self) -> VectorNode:
        """Convert to vector node for storage.

        Returns:
            VectorNode with ACE memory data in metadata
        """
        # Use ID as node ID
        node_id = f"ace_{self.workspace_id}_{self.id}"

        # Content is what gets embedded
        embedding_content = self.content

        # Store all fields in metadata
        metadata = {
            "type": "ace_memory",
            "id": self.id,
            "section": self.section,
            "content": self.content,
            "helpful": self.helpful,
            "harmful": self.harmful,
            "neutral": self.neutral,
            "created_at": self.created_at.isoformat() if isinstance(self.created_at, datetime) else self.created_at,
            "updated_at": self.updated_at.isoformat() if isinstance(self.updated_at, datetime) else self.updated_at,
            "workspace_id": self.workspace_id
        }

        return VectorNode(
            id=node_id,
            content=embedding_content,
            metadata=metadata,
        )

    @classmethod
    def from_vector_node(cls, node: VectorNode) -> "ACEMemory":
        """Convert from vector node.

        Args:
            node: VectorNode from storage

        Returns:
            ACEMemory instance
        """
        metadata = node.metadata

        return cls(
            id=metadata.get("id", ""),
            section=metadata.get("section", ""),
            content=metadata.get("content", ""),
            helpful=metadata.get("helpful", 0),
            harmful=metadata.get("harmful", 0),
            neutral=metadata.get("neutral", 0),
            created_at=metadata.get("created_at", ""),
            updated_at=metadata.get("updated_at", ""),
            workspace_id=metadata.get("workspace_id", "default"),
        )



class ACESummarizeRequest(BaseModel):
    """ACE Summarize request schema."""

    matts: str = Field(
        default="none",
        description="MaTTS mode: none, parallel, or sequential"
    )
    query: str = Field(description="Query for summarization")
    trajectories: List[str] = Field(description="List of trajectory strings")
    ground_truth: Optional[str] = Field(default=None, description="Optional ground truth")
    feedback: Optional[List[str]] = Field(default=None, description="Optional environment feedback for trajectories")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "matts": "parallel",
                "query": "How to implement caching?",
                "trajectories": ["Traj1", "Traj2", "Traj3"],
                "ground_truth": "python code implementing caching",
                "feedback": ["feedback1", "feedback2", "feedback3"],
            }
        }
    )



class ACESummarizeResponse(BaseModel):
    """ACE Summarize response schema."""

    status: str = Field(description="Operation status")
    memory: List[ACEMemory] = Field(description="List of created or updated memories")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "success",
                "memory": [
                    {
                        "id": "mem_001",
                        "section": "python",
                        "content": "Use lru_cache for memoization",
                        "helpful": 0,
                        "harmful": 0,
                        "neutral": 0,
                        "created_at": "2024-01-01T00:00:00Z",
                        "updated_at": "2024-01-01T00:00:00Z",
                    }
                ]
            }
        }
    )




class ACERetrieveRequest(BaseModel):
    """ACE Retrieve request schema (retrieves all memories)."""

    user_id: Optional[str] = Field(default=None, description="Optional user identifier")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "user_id": "alice"
            }
        }
    )




class ACERetrievedMemory(BaseModel):
    """ACE Retrieved memory item."""

    id: str = Field(description="Memory identifier")
    section: str = Field(description="Memory section")
    content: str = Field(description="Memory content")
    helpful: int = Field(description="Helpful count")
    harmful: int = Field(description="Harmful count")
    neutral: int = Field(description="Neutral count")


class ACERetrieveResponse(BaseModel):
    """ACE Retrieve response schema."""

    status: str = Field(description="Operation status")
    memory_string: str = Field(description="Formatted memory string")
    retrieved_memory: List[ACERetrievedMemory] = Field(description="List of retrieved memories")
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "success",
                "memory_string": "Section: python\nContent: Use lru_cache...",
                "retrieved_memory": [
                    {
                        "id": "mem_001",
                        "section": "python",
                        "content": "Use lru_cache for memoization",
                        "helpful": 0,
                        "harmful": 0,
                        "neutral": 0,
                    }
                ],
            }
        }
    )


# ============================================================================
# ReasoningBank Schemas
# ============================================================================


class ReasoningBankMemoryItem(BaseModel):
    """ReasoningBank memory item."""

    title: str = Field(description="Memory title")
    description: str = Field(description="Memory description")
    content: str = Field(description="Memory content")


class ReasoningBankMemory(BaseMemory):
    """ReasoningBank Memory schema."""

    query: str = Field(description="Query used as embedding index")
    memory: List[ReasoningBankMemoryItem] = Field(description="List of memory items")
    label: Optional[bool] = Field(default=None, description="Memory label")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "query": "How to implement caching?",
                "memory": [
                    {
                        "title": "Python Caching",
                        "description": "Memoization technique",
                        "content": "Use functools.lru_cache decorator",
                    }
                ],
                "label": True,
            }
        }
    )


    def to_vector_node(self) -> VectorNode:
        """Convert to vector node.

        Returns:
            VectorNode with memory data in metadata
        """
        # Generate unique ID based on title and content
        combined = f"{self.query}|{self.memory[0].title}" if self.memory else self.query
        content_hash = hashlib.md5(combined.encode()).hexdigest()
        node_id = f"reasoning_bank_{self.workspace_id}_{content_hash}"

        # Combine title, description and content for embedding
        embedding_content = self.query

        # Store all fields in metadata
        metadata = {
            "type": "reasoning_bank_memory",
            "query": self.query,
            "memory": self.memory,
            "label": self.label,
            "workspace_id": self.workspace_id
        }

        return VectorNode(
            id=node_id,
            content=embedding_content,
            metadata=metadata,
        )

    @classmethod
    def from_vector_node(cls, node: VectorNode) -> "ReasoningBankMemory":
        """Convert from vector node.

        Args:
            node: VectorNode from storage

        Returns:
            ReasoningBankMemory instance
        """
        metadata = node.metadata

        return cls(
            query=metadata.get("query", ""),
            memory=metadata.get("memory", []),
            label=metadata.get("label", None),
            workspace_id=metadata.get("workspace_id", "default"),
        )

    def __repr__(self) -> str:
        """String representation."""
        return (
            f"ReasoningBankMemory(query='{self.query}', memory='{self.memory}', "
            f"label='{self.label}', workspace_id='{self.workspace_id}')"
        )


class ReasoningBankSummarizeRequest(BaseModel):
    """ReasoningBank Summarize request schema."""

    matts: str = Field(
        default="none",
        description="MaTTS mode: none, parallel, or sequential"
    )
    query: str = Field(description="Query for summarization")
    trajectories: List[str] = Field(description="List of trajectory strings")
    label: Optional[List[bool]] = Field(default=None, description="Optional labels for trajectories")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "matts": "parallel",
                "query": "How to handle errors?",
                "trajectories": ["Traj1", "Traj2", "Traj3"],
                "label": [True, False, True],
            }
        }
    )



class ReasoningBankSummarizeResponse(BaseModel):
    """ReasoningBank Summarize response schema."""

    status: str = Field(description="Operation status")
    memory: List[ReasoningBankMemory] = Field(description="Created or updated memory")
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "success",
                "memory": [
                    {
                    "query": "How to handle errors?",
                    "memory": [
                        {
                            "title": "Error Handling",
                            "description": "Best practices",
                            "content": "Use specific exception types",
                        }
                    ],
                    "label": True,
                    }
                ]
            }
        }
    )


class ReasoningBankRetrieveRequest(BaseModel):
    """ReasoningBank Retrieve request schema."""

    query: str = Field(description="Query to retrieve memories")
    topk: int = Field(default=5, description="Number of top memories to retrieve")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "query": "How to implement caching?",
                "topk": 3,
            }
        }
    )



class ReasoningBankRetrievedMemory(BaseModel):
    """ReasoningBank retrieved memory item."""

    title: str = Field(description="Memory title")
    description: str = Field(description="Memory description")
    content: str = Field(description="Memory content")


class ReasoningBankRetrieveResponse(BaseModel):
    """ReasoningBank Retrieve response schema."""

    status: str = Field(description="Operation status")
    memory_string: str = Field(description="Formatted memory string")
    retrieved_memory: List[ReasoningBankRetrievedMemory] = Field(
        description="List of retrieved memories"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "success",
                "memory_string": "Title: Python Caching\nDescription: Memoization...",
                "retrieved_memory": [
                    {
                        "title": "Python Caching",
                        "description": "Memoization technique",
                        "content": "Use functools.lru_cache decorator",
                    }
                ],
            }
        }
    )


# ============================================================================
# ReMe Schemas
# ============================================================================

class ReMeMemoryMetadata(BaseModel):
    """ReMe Memory metadata."""

    tags: List[str] = Field(default_factory=list, description="Memory tags")
    step_type: Optional[str] = Field(default=None, description="Step type")
    tools_used: List[str] = Field(default_factory=list, description="Tools used")
    confidence: Optional[float] = Field(default=None, description="Confidence score")
    freq: int = Field(default=0, description="Frequency of use")
    utility: Optional[float] = Field(default=None, description="Utility score")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "tags": ["python", "caching"],
                "step_type": "implementation",
                "tools_used": ["functools"],
                "confidence": 0.95,
                "freq": 5,
                "utility": 4,
            }
        }
    )


class ReMeMemory(BaseMemory):
    """ReMe Memory schema."""

    when_to_use: str = Field(description="Conditions for using this memory")
    content: str = Field(description="Memory content")
    score: float = Field(default=0.0, description="Memory score (0-1)")
    created_at: datetime = Field(description="Creation timestamp")
    updated_at: datetime = Field(description="Last update timestamp")
    metadata: ReMeMemoryMetadata = Field(
        default_factory=ReMeMemoryMetadata,
        description="Additional metadata"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "when_to_use": "When implementing caching in Python",
                "content": "Use functools.lru_cache decorator",
                "score": 0.8,
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "metadata": {
                    "tags": ["python", "caching"],
                    "step_type": "implementation",
                    "tools_used": ["functools"],
                    "confidence": 0.95,
                    "freq": 4,
                    "utility": 5,
                },
            }
        }
    )


    def to_vector_node(self) -> VectorNode:
        """Convert to vector node for storage.

        Returns:
            VectorNode with ReMe memory data in metadata
        """
        # Create unique node ID
        node_id = f"reme_{self.workspace_id}_{hashlib.md5(self.when_to_use.encode()).hexdigest()[:12]}"
        embedding_content = self.when_to_use

        # Store all fields in metadata
        metadata = {
            "type": "reme_memory",
            "when_to_use": self.when_to_use,
            "content": self.content,
            "score": self.score,
            "created_at": self.created_at.isoformat() if isinstance(self.created_at, datetime) else self.created_at,
            "updated_at": self.updated_at.isoformat() if isinstance(self.updated_at, datetime) else self.updated_at,
            "workspace_id": self.workspace_id,
            "metadata": {
                "tags": self.metadata.tags,
                "step_type": self.metadata.step_type,
                "tools_used": self.metadata.tools_used,
                "confidence": self.metadata.confidence,
                "freq": self.metadata.freq,
                "utility": self.metadata.utility,
            }
        }

        return VectorNode(
            id=node_id,
            content=embedding_content,
            metadata=metadata,
        )

    @classmethod
    def from_vector_node(cls, node: VectorNode) -> "ReMeMemory":
        """Convert from vector node.

        Args:
            node: VectorNode from storage

        Returns:
            ReMeMemory instance
        """
        metadata = node.metadata

        # Reconstruct ReMeMemoryMetadata
        memory_metadata = ReMeMemoryMetadata(
            tags=metadata.get("metadata", {}).get("tags", []),
            step_type=metadata.get("metadata", {}).get("step_type", ""),
            tools_used=metadata.get("metadata", {}).get("tools_used", []),
            confidence=metadata.get("metadata", {}).get("confidence", 0.0),
            freq=metadata.get("metadata", {}).get("freq", 0),
            utility=metadata.get("metadata", {}).get("utility", 0),
        )

        return cls(
            workspace_id=metadata.get("workspace_id", ""),
            when_to_use=metadata.get("when_to_use", ""),
            content=metadata.get("content", ""),
            score=metadata.get("score", 0.0),
            created_at=metadata.get("created_at", datetime.now(timezone.utc).isoformat()),
            updated_at=metadata.get("updated_at", datetime.now(timezone.utc).isoformat()),
            metadata=memory_metadata,
        )


class ReMeSummarizeRequest(BaseModel):
    """ReMe Summarize request schema."""

    matts: str = Field(
        default="none",
        description="MaTTS mode: none, parallel, or sequential"
    )
    trajectories: List[str] = Field(description="List of trajectory strings")
    score: Optional[List[float]] = Field(
        default=None,
        description="Optional scores for trajectories (0-1)"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "matts": "parallel",
                "trajectories": ["Traj1", "Traj2", "Traj3"],
                "score": [0.85, 0.92, 1],
            }
        }
    )



class ReMeSummarizeResponse(BaseModel):
    """ReMe Summarize response schema."""

    status: str = Field(description="Operation status")
    memory: List[ReMeMemory] = Field(description="Created or updated memory")
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "success",
                "memory": [
                    {
                    "when_to_use": "When implementing caching in Python",
                    "content": "Use functools.lru_cache decorator",
                    "score": 0.92,
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-01T00:00:00Z",
                    "metadata": {
                        "tags": ["python", "caching"],
                        "step_type": "implementation",
                        "tools_used": ["functools"],
                        "confidence": 0.95,
                        "freq": 0,
                        "utility": 0,
                        },
                    }
                ]
            }
        }
    )



class ReMeRetrieveRequest(BaseModel):
    """ReMe Retrieve request schema."""

    query: str = Field(description="Query to retrieve memories")
    topk_retrieval: int = Field(default=10, description="Number of memories to retrieve initially")
    topk_rerank: int = Field(default=5, description="Number of memories after reranking")
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "query": "How to implement caching?",
                "topk_retrieval": 10,
                "topk_rerank": 5,
            }
        }
    )



class ReMeRetrievedMemory(BaseModel):
    """ReMe retrieved memory item."""

    when_to_use: str = Field(description="Conditions for using this memory")
    content: str = Field(description="Memory content")


class ReMeRetrieveResponse(BaseModel):
    """ReMe Retrieve response schema."""

    status: str = Field(description="Operation status")
    memory_string: str = Field(description="Formatted memory string")
    retrieved_memory: List[ReMeRetrievedMemory] = Field(
        description="List of retrieved memories"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "success",
                "memory_string": "When to use: When implementing caching...",
                "retrieved_memory": [
                    {
                        "when_to_use": "When implementing caching in Python",
                        "content": "Use functools.lru_cache decorator",
                    }
                ],
            }
        }
    )


# ============================================================================
# Ours (Custom) Schemas
# ============================================================================
# Note: Same structure as ReMe with different trajectory generation scaling method


class OursMemory(ReMeMemory):
    """Custom memory schema (same structure as ReMe)."""
    pass


class OursSummarizeRequest(ReMeSummarizeRequest):
    """Custom Summarize request schema (same structure as ReMe)."""
    pass


class OursSummarizeResponse(ReMeSummarizeResponse):
    """Custom Summarize response schema (same structure as ReMe)."""
    pass


class OursRetrieveRequest(ReMeRetrieveRequest):
    """Custom Retrieve request schema (same structure as ReMe)."""
    pass


class OursRetrievedMemory(ReMeRetrievedMemory):
    """Custom retrieved memory item (same structure as ReMe)."""
    pass


class OursRetrieveResponse(ReMeRetrieveResponse):
    """Custom Retrieve response schema (same structure as ReMe)."""
    pass


# ============================================================================
# Generic Schemas (Multi-Algorithm Support)
# ============================================================================

class SummarizeResponse(BaseModel):
    """Generic summarize response that works with any algorithm.

    The memory field can contain a list of ACEMemory, ReasoningBankMemory, or ReMeMemory
    depending on which algorithm is used.
    """

    status: str = Field(description="Operation status")
    memory: Union[List[ACEMemory], List[ReasoningBankMemory], List[ReMeMemory]] = Field(
        description="List of created or updated memories (algorithm-specific)"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "success",
                "memory": [
                    {
                        "query": "How to implement caching?",
                        "memory": [
                            {
                                "title": "Python Caching",
                                "description": "Memoization technique",
                                "content": "Use functools.lru_cache decorator",
                            }
                        ],
                        "label": True,
                    }
                ]
            }
        }
    )



class RetrieveResponse(BaseModel):
    """Generic retrieve response that works with any algorithm.

    The retrieved_memory field can contain ACERetrievedMemory, ReasoningBankRetrievedMemory,
    or ReMeRetrievedMemory depending on which algorithm is used.
    """

    status: str = Field(description="Operation status")
    memory_string: str = Field(description="Formatted memory string")
    retrieved_memory: Union[
        List[ACERetrievedMemory],
        List[ReasoningBankRetrievedMemory],
        List[ReMeRetrievedMemory]
    ] = Field(description="List of retrieved memories (algorithm-specific)")
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "success",
                "memory_string": "Title: Python Caching\\nDescription: Memoization...",
                "retrieved_memory": [
                    {
                        "title": "Python Caching",
                        "description": "Memoization technique",
                        "content": "Use functools.lru_cache decorator",
                    }
                ],
            }
        }
    )


