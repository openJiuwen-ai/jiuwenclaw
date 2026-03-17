# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Core data types for self-evolving: Case, EvaluatedCase.
"""

from typing import Optional, List, Dict, Any
import uuid

from pydantic import BaseModel, Field, field_validator

from openjiuwen.core.foundation.tool import ToolInfo


class Case(BaseModel):
    """Single training/evaluation sample.

    Attributes:
        inputs: Input data (e.g., query or conversation)
        label: Expected answer or desired output
        tools: Available tools for this case (optional)
        case_id: Unique identifier (auto-generated if not provided)
    """

    inputs: Dict[str, Any] = Field(..., min_length=1, description="Input data, e.g., query or conversation content")
    label: Dict[str, Any] = Field(..., min_length=1, description="Expected answer or desired output")
    tools: Optional[List[ToolInfo]] = Field(
        default=None, description="List of tools available for this case (optional)"
    )
    case_id: str = Field(default_factory=lambda: uuid.uuid4().hex, description="Unique identifier for the sample")


class EvaluatedCase(BaseModel):
    """Evaluated sample with model output and score.

    Attributes:
        case: Original Case
        answer: Model output/prediction
        score: Composite score in range [0, 1]
        reason: Reasoning for the score or error analysis
        per_metric: Per-metric scores when using MetricEvaluator
    """

    case: Case = Field(..., description="Original Case")
    answer: Optional[Dict[str, Any]] = Field(default=None, description="Model output/prediction")
    score: float = Field(default=0.0, description="Composite score in range [0, 1]")
    reason: str = Field(default="", description="Reasoning for the score or error analysis")
    per_metric: Optional[Dict[str, float]] = Field(
        default=None,
        description="Per-metric scores when using MetricEvaluator",
    )

    @field_validator("score")
    @classmethod
    def clamp_score(cls, v: float) -> float:
        """Clamp score to [0, 1] range."""
        return max(0.0, min(1.0, v))

    @property
    def inputs(self) -> Dict[str, Any]:
        return self.case.inputs

    @property
    def label(self) -> Dict[str, Any]:
        return self.case.label

    @property
    def tools(self) -> Optional[List[ToolInfo]]:
        return self.case.tools

    @property
    def case_id(self) -> str:
        return self.case.case_id
