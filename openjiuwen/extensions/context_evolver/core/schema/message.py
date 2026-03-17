# -*- coding: UTF-8 -*-
"""Message schema for chat interactions."""
from enum import Enum
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field, ConfigDict



class Role(str, Enum):
    """Message role enumeration."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class Message(BaseModel):
    """Chat message with role, content, and optional metadata."""

    role: Role = Field(description="Message role (system, user, assistant, tool)")
    content: str = Field(description="Message content")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")

    model_config = ConfigDict(
        use_enum_values=True
    )
    #class Config:
    #    """Pydantic config."""#
    #    use_enum_values = True

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary.

        Returns:
            Dictionary representation
        """
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Message":
        """Create from dictionary.

        Args:
            data: Dictionary data

        Returns:
            Message instance
        """
        return cls(**data)

    def __repr__(self) -> str:
        """String representation."""
        content_preview = self.content[:50] + "..." if len(self.content) > 50 else self.content
        return f"Message(role={self.role}, content='{content_preview}')"
