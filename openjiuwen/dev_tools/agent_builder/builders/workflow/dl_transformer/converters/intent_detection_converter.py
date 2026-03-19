# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import List, Dict, Any

from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.converters.base import BaseConverter
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.models import Edge, InputsField
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.converter_utils import ConverterUtils


class IntentDetectionConverter(BaseConverter):
    """IntentDetection node converter."""

    @staticmethod
    def _convert_intents(conditions: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        """Convert intent list.

        Args:
            conditions: Condition list

        Returns:
            Intent list
        """
        return [
            {"name": cond["expression"].split(" contain ")[1]}
            for cond in conditions
            if cond["expression"] != "default"
        ]

    @staticmethod
    def _convert_branches(
            conditions: List[Dict[str, Any]]
    ) -> List[Dict[str, str]]:
        """Convert branch list.

        Args:
            conditions: Condition list

        Returns:
            Branch list
        """
        return [{"branchId": cond["branch"]} for cond in conditions]

    def _convert_specific_config(self) -> None:
        """Convert IntentDetection node specific configuration."""
        self.node.data.inputs = InputsField(
            input_parameters=self._convert_input_variables(
                self.node_data["parameters"]["inputs"]
            ),
            llm_param=ConverterUtils.convert_llm_param(
                self.node_data["parameters"]["configs"]["prompt"],
                ""
            ),
            intents=self._convert_intents(
                self.node_data["parameters"]["conditions"]
            )
        )
        self.node.data.outputs = self._convert_outputs_field(
            [{"name": "classificationId", "type": "integer", "description": None}]
        )
        if self.node.data.outputs.properties:
            self.node.data.outputs.required = ["classificationId"]
        self.node.data.branches = self._convert_branches(
            self.node_data["parameters"]["conditions"]
        )

    def _convert_edges(self) -> None:
        """Convert edges (IntentDetection node has multiple branches)."""
        for cond in self.node_data["parameters"]["conditions"]:
            self.edges.append(
                Edge(
                    source_node_id=self.node_data["id"],
                    target_node_id=cond["next"],
                    source_port_id=cond["branch"]
                )
            )
