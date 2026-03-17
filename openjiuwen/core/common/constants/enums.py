# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Core enum constants"""

from enum import Enum


class ControllerType(Enum):
    """Controller type enumeration"""
    ReActController = "react"
    WorkflowController = "workflow"
    Undefined = "undefined"


class TaskType(Enum):
    """Task type enumeration"""
    PLUGIN = "plugin"
    WORKFLOW = "workflow"
    MCP = "mcp"
    UNDEFINED = "undefined"
