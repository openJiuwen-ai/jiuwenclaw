# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Trajectory schema for tracking task execution history."""

from typing import Optional, Dict, Any, List
from enum import Enum
from pydantic import BaseModel, Field, ConfigDict


class FeedbackType(str, Enum):
    """Feedback type for trajectory outcomes."""

    HELPFUL = "helpful"
    HARMFUL = "harmful"
    NEUTRAL = "neutral"


class Trajectory(BaseModel):
    """Trajectory representing a task execution with feedback.

    A trajectory captures:
    - The query/task that was executed
    - The response/output generated
    - Feedback on whether the outcome was helpful or harmful
    """

    query: str = Field(description="The query or task that was executed")
    response: str = Field(description="The response or output generated")
    feedback: FeedbackType = Field(
        default=FeedbackType.NEUTRAL, description="Feedback on the outcome"
    )
    context: Optional[Dict[str, Any]] = Field(
        default_factory=dict, description="Additional context about the execution"
    )

    model_config = ConfigDict(
        use_enum_values=True
    )  

    def is_success(self) -> bool:
        """Check if trajectory was successful.

        Returns:
            True if feedback is helpful
        """
        return self.feedback == FeedbackType.HELPFUL

    def is_failure(self) -> bool:
        """Check if trajectory was a failure.

        Returns:
            True if feedback is harmful
        """
        return self.feedback == FeedbackType.HARMFUL

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary.

        Returns:
            Dictionary representation
        """
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Trajectory":
        """Create from dictionary.

        Args:
            data: Dictionary data

        Returns:
            Trajectory instance
        """
        return cls(**data)

    def __repr__(self) -> str:
        """String representation."""
        query_preview = self.query[:50] + "..." if len(self.query) > 50 else self.query
        return f"Trajectory(query='{query_preview}', feedback={self.feedback})"


class TrajectoryBatch(BaseModel):
    """Batch of trajectories for processing."""

    trajectories: List[Trajectory] = Field(description="List of trajectories")
    user_id: str = Field(description="User identifier")
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Batch metadata"
    )

    def get_success_trajectories(self) -> List[Trajectory]:
        """Get successful trajectories.

        Returns:
            List of trajectories with helpful feedback
        """
        return [t for t in self.trajectories if t.is_success()]

    def get_failure_trajectories(self) -> List[Trajectory]:
        """Get failed trajectories.

        Returns:
            List of trajectories with harmful feedback
        """
        return [t for t in self.trajectories if t.is_failure()]

    def count_by_feedback(self) -> Dict[str, int]:
        """Count trajectories by feedback type.

        Returns:
            Dictionary with counts per feedback type
        """
        counts = {
            FeedbackType.HELPFUL: 0,
            FeedbackType.HARMFUL: 0,
            FeedbackType.NEUTRAL: 0,
        }

        for traj in self.trajectories:
            counts[traj.feedback] += 1

        return counts

    def __repr__(self) -> str:
        """String representation."""
        counts = self.count_by_feedback()
        return (
            f"TrajectoryBatch(user={self.user_id}, "
            f"total={len(self.trajectories)}, "
            f"helpful={counts[FeedbackType.HELPFUL]}, "
            f"harmful={counts[FeedbackType.HARMFUL]})"
        )
