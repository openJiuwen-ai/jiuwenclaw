# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any


class NodeType(Enum):
    """Node type enumeration."""
    Start = ("Start", "1")
    End = ("End", "2")
    LLM = ("LLM", "3")
    IntentDetection = ("IntentDetection", "6")
    Questioner = ("Questioner", "7")
    Code = ("Code", "10")
    Plugin = ("Plugin", "19")
    Output = ("Output", "9")
    Branch = ("Branch", "4")

    def __init__(self, dl_type: str, dsl_type: str) -> None:
        self.dl_type: str = dl_type
        self.dsl_type: str = dsl_type


class SourceType(Enum):
    """Source type enumeration."""
    ref: str = "ref"
    constant: str = "constant"


@dataclass
class Position:
    """Position information."""
    x: float
    y: float


@dataclass
class InputVariable:
    """Input variable."""
    type: str
    content: str
    extra: Dict[str, str]
    schema: Optional[Dict[str, str]] = None


@dataclass
class InputsField:
    """Inputs field."""
    input_parameters: Dict[str, InputVariable] = field(default_factory=dict)
    llm_param: Optional[Dict[str, dict]] = None
    system_prompt: Optional[Dict[str, str]] = None
    intents: Optional[List[Dict[str, str]]] = None
    language: Optional[str] = None
    code: Optional[str] = None
    plugin_param: Optional[Dict[str, str]] = None
    content: Optional[Dict[str, str]] = None


@dataclass
class OutputsField:
    """Outputs field."""
    type: str = "object"
    properties: Optional[Dict[str, "OutputsField"]] = None
    required: Optional[List[str]] = None
    description: Optional[str] = None
    default: Optional[str] = None
    extra: Optional[Dict[str, int]] = None

    def add_property(
            self,
            variable_names: List[str],
            desc: str,
            index: int,
            var_type: Optional[str] = None
    ) -> None:
        """Add property."""
        if not variable_names:
            return

        key = variable_names[0]
        is_leaf = len(variable_names) == 1

        if self.properties is None:
            self.properties = {}

        if key not in self.properties:
            if is_leaf:
                self.properties[key] = OutputsField(
                    type=var_type or "string",
                    description=desc,
                    extra={"index": index}
                )
            else:
                self.properties[key] = OutputsField(
                    type="object",
                    properties={},
                    required=[]
                )

        output_field = self.properties[key]
        output_field.add_property(variable_names[1:], desc, index, var_type)


@dataclass
class DataConfig:
    """Data configuration."""
    title: str = ""
    inputs: Optional[InputsField] = None
    outputs: Optional[OutputsField] = None
    branches: Optional[List[Dict[str, Any]]] = None
    exception_config: Optional[Dict[str, Any]] = None


@dataclass
class Node:
    """Node."""
    id: str
    type: str
    meta: Dict[str, Any] = field(default_factory=dict)
    data: DataConfig = field(default_factory=DataConfig)


@dataclass
class Edge:
    """Edge."""
    source_node_id: str
    target_node_id: str
    source_port_id: Optional[str] = None


@dataclass
class Workflow:
    """Workflow."""
    nodes: List[Node] = field(default_factory=list)
    edges: List[Edge] = field(default_factory=list)
    