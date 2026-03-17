# -*- coding: UTF-8 -*-
"""Vector node schema for vector store serialization."""
import json
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field, ConfigDict



class VectorNode(BaseModel):
    """Vector node for storing in vector databases.

    This is the standard serialization format for all memory types.
    """

    id: str = Field(description="Unique identifier for the vector")
    content: str = Field(description="Text content to be embedded")
    embedding: Optional[List[float]] = Field(default=None, description="Vector embedding")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")

    model_config = ConfigDict(
        json_encoders={}
    )

    #class Config:
    #    """Pydantic config."""

    #    json_encoders = {
    #        # Custom encoders if needed
    #    }

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary.

        Returns:
            Dictionary representation
        """
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VectorNode":
        """Create from dictionary.

        Args:
            data: Dictionary data

        Returns:
            VectorNode instance
        """
        return cls(**data)

    def __repr__(self) -> str:
        """String representation."""
        content_preview = self.content[:50] + "..." if len(self.content) > 50 else self.content
        return f"VectorNode(id={self.id}, content='{content_preview}')"
