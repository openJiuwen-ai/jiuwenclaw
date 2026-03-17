# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from openjiuwen.core.workflow import WorkflowCard
from openjiuwen.core.workflow.components.base import ComponentAbility
from openjiuwen.core.session import Transformer


class CompIOConfig(BaseModel):
    """
    Input/Output configuration for a component.

    Defines schemas and transformers for component data processing.
    """
    inputs_schema: Optional[Dict | Transformer] = None
    outputs_schema: Optional[Dict | Transformer] = None


class NodeSpec(BaseModel):
    """
    Specification for a workflow node/component.

    Contains configuration for both regular and streaming I/O,
    along with component capabilities.
    """
    io_configs: CompIOConfig = None  # Configuration for regular (non-streaming) I/O
    stream_io_configs: CompIOConfig = None  # Configuration for streaming I/O
    abilities: List[ComponentAbility] = Field(default_factory=list)  # List of component abilities supported


class WorkflowSpec(BaseModel):
    """
    Complete specification of a workflow structure.

    Defines the graph structure, connections, and component configurations.
    """
    edges: Dict[str, list[str]] = Field(
        default_factory=dict,
        description="Regular data flow edges (source -> [targets])"
    )
    stream_edges: Dict[str, list[str]] = Field(
        default_factory=dict,
        description="Streaming data flow edges (source -> [targets])"
    )
    comp_configs: Dict[str, NodeSpec] = Field(
        default_factory=dict,
        description="Configuration for each component in the workflow"
    )
    start_nodes: list[str] = Field(default_factory=list)


class WorkflowConfig(BaseModel):
    card: WorkflowCard
    spec: Optional[WorkflowSpec] = Field(default_factory=WorkflowSpec)
    workflow_max_nesting_depth: int = Field(default=5, ge=0, le=10)
