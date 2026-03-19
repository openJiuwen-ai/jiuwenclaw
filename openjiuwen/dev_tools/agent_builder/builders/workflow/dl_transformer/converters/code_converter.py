# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Dict, Any

from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.converters.base import BaseConverter
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.models import InputsField


class CodeConverter(BaseConverter):
    """Code node converter."""

    CODE_EXCEPTION_CONFIG: Dict[str, Any] = {
        "retryTimes": 3,
        "timeoutSeconds": 30,
        "processType": "break"
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
        self.node.data.outputs = self._convert_outputs_field(
            self.node_data["parameters"]["outputs"]
        )
        self.node.data.exception_config = CodeConverter.CODE_EXCEPTION_CONFIG
        