# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Optional, List, Dict, Any
import uuid

from pydantic import BaseModel, Field

from openjiuwen.core.foundation.tool import ToolInfo


class TuneConstant:
    """prompt tuning constants"""

    """optimizer parameters default value constant"""
    DEFAULT_EXAMPLE_NUM: int = 1
    DEFAULT_ITERATION_NUM: int = 3
    DEFAULT_MAX_SAMPLED_EXAMPLE_NUM: int = 10
    DEFAULT_PARALLEL_NUM: int = 1
    DEFAULT_MAX_NUM_SAMPLE_ERROR_CASES: int = 10
    DEFAULT_EARLY_STOP_SCORE: float = 1.0

    """optimizer parameters threshold constant"""
    MIN_ITERATION_NUM: int = 1
    MAX_ITERATION_NUM: int = 20
    MIN_PARALLEL_NUM: int = 1
    MAX_PARALLEL_NUM: int = 20
    MIN_EXAMPLE_NUM: int = 0
    MAX_EXAMPLE_NUM: int = 20


class Case(BaseModel):
    """definition of case"""
    inputs: Dict[str, Any] = Field(..., min_length=1)
    label: Dict[str, Any] = Field(..., min_length=1)
    tools: Optional[List[ToolInfo]] = Field(default=None)
    case_id: str = Field(default=str(uuid.uuid4()))


class EvaluatedCase(BaseModel):
    """definition of evaluated case"""
    case: Case = Field(...)
    answer: Dict[str, Any] = Field(default=None)
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = Field(default="")

    @property
    def inputs(self):
        return self.case.inputs

    @property
    def label(self):
        return self.case.label

    @property
    def tools(self):
        return self.case.tools

    @property
    def case_id(self):
        return self.case.case_id