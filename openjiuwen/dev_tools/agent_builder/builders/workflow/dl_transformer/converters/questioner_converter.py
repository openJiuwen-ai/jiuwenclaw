# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.converters.base import BaseConverter
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.models import InputsField
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.converter_utils import ConverterUtils


class QuestionerConverter(BaseConverter):
    """Questioner node converter."""

    def _convert_specific_config(self) -> None:
        """Convert Questioner node specific configuration."""
        llm_param = ConverterUtils.convert_llm_param(
            self.node_data["parameters"]["configs"]["prompt"],
            ""
        )
        self.node.data.inputs = InputsField(
            input_parameters=self._convert_input_variables(
                self.node_data["parameters"]["inputs"]
            ),
            llm_param=llm_param,
            system_prompt=llm_param["systemPrompt"]
        )
        outputs = self._convert_outputs_field(
            self.node_data["parameters"]["outputs"]
        )
        self.node.data.outputs = outputs
        if outputs.properties:
            self.node.data.outputs.required = list(outputs.properties.keys())
            