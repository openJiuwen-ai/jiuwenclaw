# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Memory schemas for different memory types."""

from typing import Optional, Dict, Any
import hashlib
import json
from pydantic import BaseModel, Field

from ..core.schema import VectorNode


class BaseMemory(BaseModel):
    """Abstract base class for all memory types."""

    content: str = Field(description="Memory content")
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


class TaskMemory(BaseMemory):
    """Task memory storing when and how to use knowledge.

    This represents learnings from task execution that can be retrieved
    to help with future similar tasks.
    """

    when_to_use: str = Field(description="Conditions for using this memory")
    helpful_count: int = Field(default=0, description="Times marked as helpful")
    harmful_count: int = Field(default=0, description="Times marked as harmful")
    section: str = Field(default="general", description="Memory section/category")

    def to_vector_node(self) -> VectorNode:
        """Convert to vector node.

        Returns:
            VectorNode with memory data in metadata
        """
        # Generate unique ID based on content
        content_hash = hashlib.md5(self.content.encode()).hexdigest()
        node_id = f"task_{self.workspace_id}_{content_hash}"

        # Combine when_to_use and content for embedding
        embedding_content = f"When to use: {self.when_to_use}\n\nContent: {self.content}"

        # Store all fields in metadata
        metadata = {
            "type": "task_memory",
            "when_to_use": self.when_to_use,
            "content": self.content,
            "workspace_id": self.workspace_id,
            "helpful_count": self.helpful_count,
            "harmful_count": self.harmful_count,
            "section": self.section,
        }

        return VectorNode(
            id=node_id,
            content=embedding_content,
            metadata=metadata,
        )

    @classmethod
    def from_vector_node(cls, node: VectorNode) -> "TaskMemory":
        """Convert from vector node.

        Args:
            node: VectorNode from storage

        Returns:
            TaskMemory instance
        """
        metadata = node.metadata

        return cls(
            content=metadata.get("content", ""),
            workspace_id=metadata.get("workspace_id", "default"),
            when_to_use=metadata.get("when_to_use", ""),
            helpful_count=metadata.get("helpful_count", 0),
            harmful_count=metadata.get("harmful_count", 0),
            section=metadata.get("section", "general"),
        )

    def get_score(self) -> float:
        """Calculate relevance score based on feedback.

        Returns:
            Score between 0 and infinity (higher is better)
        """
        return (self.helpful_count + 1) / (self.harmful_count + 1)

    def __repr__(self) -> str:
        """String representation."""
        content_preview = self.content[:50] + "..." if len(self.content) > 50 else self.content
        return (
            f"TaskMemory(when_to_use='{self.when_to_use[:30]}...', "
            f"content='{content_preview}', score={self.get_score():.2f})"
        )


class PersonalMemory(BaseMemory):
    """Personal memory about user preferences and context."""

    target: str = Field(description="What this memory is about")
    reflection_subject: Optional[str] = Field(
        default=None, description="Subject of reflection"
    )

    def to_vector_node(self) -> VectorNode:
        """Convert to vector node.

        Returns:
            VectorNode with memory data in metadata
        """
        content_hash = hashlib.md5(self.content.encode()).hexdigest()
        node_id = f"personal_{self.workspace_id}_{content_hash}"

        embedding_content = f"About: {self.target}\n\nContent: {self.content}"

        metadata = {
            "type": "personal_memory",
            "target": self.target,
            "content": self.content,
            "workspace_id": self.workspace_id,
            "reflection_subject": self.reflection_subject,
        }

        return VectorNode(
            id=node_id,
            content=embedding_content,
            metadata=metadata,
        )

    @classmethod
    def from_vector_node(cls, node: VectorNode) -> "PersonalMemory":
        """Convert from vector node.

        Args:
            node: VectorNode from storage

        Returns:
            PersonalMemory instance
        """
        metadata = node.metadata

        return cls(
            content=metadata.get("content", ""),
            workspace_id=metadata.get("workspace_id", "default"),
            target=metadata.get("target", ""),
            reflection_subject=metadata.get("reflection_subject"),
        )


class ReasoningBankMemory(BaseMemory):
    """ReasoningBank memory storing distilled reasoning strategies.

    Based on the ReasoningBank algorithm, this memory type stores
    structured knowledge units with title, description, and content
    that abstract away low-level execution details while preserving
    transferable reasoning patterns and strategies.
    """

    title: str = Field(description="Concise identifier summarizing the core strategy")
    description: str = Field(description="Brief one-sentence summary of the memory item")
    source_type: str = Field(default="success", description="Source: success, failure, or comparative")
    helpful_count: int = Field(default=0, description="Times marked as helpful")
    harmful_count: int = Field(default=0, description="Times marked as harmful")

    def to_vector_node(self) -> VectorNode:
        """Convert to vector node.

        Returns:
            VectorNode with memory data in metadata
        """
        # Generate unique ID based on title and content
        combined = f"{self.title}|{self.content}"
        content_hash = hashlib.md5(combined.encode()).hexdigest()
        node_id = f"reasoning_bank_{self.workspace_id}_{content_hash}"

        # Combine title, description and content for embedding
        embedding_content = f"Title: {self.title}\n\nDescription: {self.description}\n\nContent: {self.content}"

        # Store all fields in metadata
        metadata = {
            "type": "reasoning_bank_memory",
            "title": self.title,
            "description": self.description,
            "content": self.content,
            "workspace_id": self.workspace_id,
            "source_type": self.source_type,
            "helpful_count": self.helpful_count,
            "harmful_count": self.harmful_count,
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
            content=metadata.get("content", ""),
            workspace_id=metadata.get("workspace_id", "default"),
            title=metadata.get("title", ""),
            description=metadata.get("description", ""),
            source_type=metadata.get("source_type", "success"),
            helpful_count=metadata.get("helpful_count", 0),
            harmful_count=metadata.get("harmful_count", 0),
        )

    def get_score(self) -> float:
        """Calculate relevance score based on feedback.

        Returns:
            Score between 0 and infinity (higher is better)
        """
        return (self.helpful_count + 1) / (self.harmful_count + 1)

    def __repr__(self) -> str:
        """String representation."""
        return f"ReasoningBankMemory(title='{self.title}', source='{self.source_type}', score={self.get_score():.2f})"


def vector_node_to_memory(node: VectorNode) -> BaseMemory:
    """Convert a vector node back to appropriate memory type.

    Args:
        node: VectorNode from storage

    Returns:
        Appropriate memory instance

    Raises:
        ValueError: If memory type is unknown
    """
    memory_type = node.metadata.get("type", "task_memory")

    if memory_type == "task_memory":
        return TaskMemory.from_vector_node(node)
    elif memory_type == "personal_memory":
        return PersonalMemory.from_vector_node(node)
    elif memory_type == "reasoning_bank_memory":
        return ReasoningBankMemory.from_vector_node(node)
    else:
        raise ValueError(f"Unknown memory type: {memory_type}")
