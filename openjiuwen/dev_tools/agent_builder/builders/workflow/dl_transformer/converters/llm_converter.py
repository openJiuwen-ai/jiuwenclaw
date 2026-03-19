# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.converters.base import BaseConverter
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.models import InputsField
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.converter_utils import ConverterUtils


class LLMConverter(BaseConverter):
    """LLM node converter."""

    def _convert_specific_config(self) -> None:
        """Convert LLM node specific configuration."""
        self.node.data.inputs = InputsField(
            input_parameters=self._convert_input_variables(
                self.node_data["parameters"]["inputs"]
            ),
            llm_param=ConverterUtils.convert_llm_param(
                self.node_data["parameters"]["configs"]["system_prompt"],
                self.node_data["parameters"]["configs"]["user_prompt"]
            )
        )
        self.node.data.outputs = self._convert_outputs_field(
            self.node_data["parameters"]["outputs"]
        )
        