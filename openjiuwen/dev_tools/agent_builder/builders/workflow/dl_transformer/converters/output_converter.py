# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.converters.base import BaseConverter
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.models import InputsField


class OutputConverter(BaseConverter):
    """Output node converter."""

    def _convert_specific_config(self) -> None:
        """Convert Output node specific configuration."""
        self.node.data.inputs = InputsField(
            input_parameters=self._convert_input_variables(
                self.node_data["parameters"]["inputs"]
            ),
            content={
                "type": "template",
                "content": self.node_data["parameters"]["configs"]["template"]
            }
        )
        