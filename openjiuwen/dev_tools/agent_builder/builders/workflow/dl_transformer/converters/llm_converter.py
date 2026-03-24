# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.converters.base import BaseConverter
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.models import InputsField
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.converter_utils import ConverterUtils


class LLMConverter(BaseConverter):
    """LLM node converter."""

    def _convert_specific_config(self) -> None:
        """Convert LLM node specific configuration."""
        outputs_list = self.node_data["parameters"].get("outputs", [])
        outputs_count = len(outputs_list)
        
        output_format = self.node_data["parameters"]["configs"].get("output_format", "text")
        
        if outputs_count > 1 and output_format in ("text", "markdown"):
            output_format = "json"
        
        if outputs_count == 1 and output_format not in ("text", "markdown", "json"):
            output_format = "text"
        
        self.node.data.output_format = output_format
        llm_param = ConverterUtils.convert_llm_param(
            self.node_data["parameters"]["configs"]["system_prompt"],
            self.node_data["parameters"]["configs"]["user_prompt"]
        )
        llm_param["response_format"] = {"type": output_format}
        self.node.data.inputs = InputsField(
            input_parameters=self._convert_input_variables(self.node_data["parameters"]["inputs"]),
            llm_param=llm_param,
        )
        self.node.data.outputs = self._convert_outputs_field(self.node_data["parameters"]["outputs"])
