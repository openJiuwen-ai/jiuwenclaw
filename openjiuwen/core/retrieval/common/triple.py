# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Triple Data Model

Contains Triple data model.
"""

from typing import Any, Dict

from pydantic import BaseModel, Field


class Triple(BaseModel):
    """Triple data model"""

    subject: str = Field(..., description="Subject")
    predicate: str = Field(..., description="Predicate")
    object: str = Field(..., description="Object")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Metadata")
