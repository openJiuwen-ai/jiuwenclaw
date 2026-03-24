# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from abc import ABC, abstractmethod
from typing import Optional, Dict, List, Any
from enum import Enum

from openjiuwen.core.common.logging import LogManager
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.converter_utils import ConverterUtils
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.models import (
    NodeType,
    SourceType,
    Node,
    Edge,
    Position,
    InputVariable,
    OutputPropertySpec,
    OutputsField,
)

logger = LogManager.get_logger("agent_builder")


class BaseConverter(ABC):
    """Base converter defining interface and common logic for DL to DSL transformation.

    Subclasses must implement `_convert_specific_config` method.

    Example:
        ```python
        class CustomConverter(BaseConverter):
            def _convert_specific_config(self):
                pass
        ```
    """

    def __init__(
            self,
            node_data: Dict[str, Any],
            nodes_dict: Dict[str, Any],
            resource: Optional[Dict[str, Any]] = None,
            position: Position = Position(0, 0)
    ) -> None:
        """Initialize converter.

        Args:
            node_data: Node data (DL format)
            nodes_dict: Dictionary of all nodes (for reference lookup)
            resource: Resource dictionary (optional)
            position: Node position
        """
        self.node_data: Dict[str, Any] = node_data
        self.nodes_dict: Dict[str, Any] = nodes_dict
        self.resource: Optional[Dict[str, Any]] = resource
        self.position: Position = position

        node_type_enum = NodeType[node_data["type"]]
        self.node: Node = Node(
            id=node_data["id"],
            type=node_type_enum.dsl_type
        )
        self.edges: List[Edge] = []
        self._variable_index: int = 0

    @abstractmethod
    def _convert_specific_config(self) -> None:
        """Convert specific configuration. Subclasses must implement this method."""
        pass

    def convert(self) -> None:
        """Execute conversion.

        Steps:
        1. Convert common configuration
        2. Convert specific configuration
        3. Convert edges
        """
        self.convert_common_config()
        self._convert_specific_config()
        self.convert_edges()

    def convert_common_config(self) -> None:
        """Convert common configuration."""
        self.node.id = self.node_data["id"]
        self.node.meta = {
            "position": {"x": self.position.x, "y": self.position.y}
        }
        self.node.data.title = self.node_data["description"]

    def convert_edges(self) -> None:
        """Convert edges."""
        if "next" in self.node_data and self.node_data["next"]:
            self.edges.append(Edge(
                source_node_id=self.node_data["id"],
                target_node_id=self.node_data["next"]
            ))

    def _convert_input_variables(
            self,
            inputs: List[Dict[str, Any]]
    ) -> Dict[str, InputVariable]:
        """Convert input variables.

        Args:
            inputs: Input list

        Returns:
            Input variable dictionary
        """
        result: Dict[str, InputVariable] = {}
        for item in inputs:
            if "${" in item["value"]:
                ref_variable = ConverterUtils.convert_ref_variable(
                    item["value"]
                )
                result[item["name"]] = InputVariable(
                    type=ref_variable["type"],
                    content=ref_variable["content"],
                    extra={"index": self._variable_index}
                )
            else:
                result[item["name"]] = InputVariable(
                    type=SourceType.constant.value,
                    content=item["value"],
                    schema={"type": item.get("type") or "string"},
                    extra={"index": self._variable_index}
                )
            self._variable_index += 1
        return result

    def _convert_outputs_field(
            self,
            outputs: List[Dict[str, Any]]
    ) -> OutputsField:
        """Convert outputs field.

        Args:
            outputs: Output list

        Returns:
            Outputs field object
        """
        result = OutputsField(type="object", properties={}, required=[])
        for item in outputs:
            variable_name_list = item["name"].split("_of_")[::-1]
            result.add_property(OutputPropertySpec(
                variable_names=variable_name_list,
                description=item["description"],
                index=self._variable_index,
                var_type=item.get("type"),
                items=item.get("items"),
                properties=item.get("properties"),
                required=item.get("required"),
            ))
            self._variable_index += 1
        return result
