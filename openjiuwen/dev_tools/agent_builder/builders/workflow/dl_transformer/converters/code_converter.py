# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Dict, Any

from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.converters.base import BaseConverter
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.models import InputsField, Edge


class CodeConverter(BaseConverter):
    """Code node converter."""

    CODE_EXCEPTION_CONFIG = {
        "retryTimes": 3,
        "timeoutSeconds": 30,
        "processType": "break",
        "executeStep": {
            "defaultStep": "0",
            "errorStep": "1"
        }
    }

    def _convert_specific_config(self) -> None:
        """Convert Code node specific configuration."""
        self.node.data.inputs = InputsField(
            input_parameters=self._convert_input_variables(
                self.node_data["parameters"]["inputs"]
            ),
            language="python",
            code=self.node_data["parameters"]["configs"]["code"],
        )
        outputs = self._convert_outputs_field(
            self.node_data["parameters"]["outputs"]
        )
        self.node.data.outputs = outputs
        if outputs.properties:
            self.node.data.outputs.required = list(outputs.properties.keys())
        self.node.data.exception_config = CodeConverter.CODE_EXCEPTION_CONFIG

    def convert_edges(self):
        if "next" in self.node_data:
            self.edges.append(Edge(
                source_node_id=self.node_data["id"],
                target_node_id=self.node_data["next"],
                source_port_id="0"
            ))
        