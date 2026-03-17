# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Legacy schema definitions for backward compatibility

These classes are kept for backward compatibility with old code.
"""
from typing import Dict, Any

from pydantic import BaseModel, Field


class WorkflowSchema(BaseModel):
    """Legacy workflow schema for backward compatibility"""
    id: str = Field(default="")
    name: str = Field(default="")
    description: str = Field(default="")
    version: str = Field(default="")
    inputs: Dict[str, Any] = Field(default_factory=dict)


class PluginSchema(BaseModel):
    """Legacy plugin schema for backward compatibility"""
    id: str = Field(default="")
    version: str = Field(default="")
    name: str = Field(default="")
    description: str = Field(default="")
    inputs: Dict[str, Any] = Field(default_factory=dict)
    plugin_id: str = Field(default="")
