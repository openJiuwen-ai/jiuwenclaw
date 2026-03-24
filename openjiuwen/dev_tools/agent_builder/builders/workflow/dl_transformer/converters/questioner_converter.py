# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.converters.base import BaseConverter
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.models import (
    InputsField, InputVariable, SourceType, OutputsField)
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.converter_utils import ConverterUtils


class QuestionerConverter(BaseConverter):
    def _convert_specific_config(self):
        llm_param = ConverterUtils.convert_llm_param(self.node_data["parameters"]["configs"]["prompt"], "")
        
        outputs = self._convert_outputs_field(self.node_data["parameters"]["outputs"])
        
        if "user_response" not in outputs.properties:
            outputs.properties["user_response"] = OutputsField(
                type="string",
                description="用户响应输出变量"
            )
        if "output" not in outputs.properties:
            first_output_key = list(outputs.properties.keys())[0] if outputs.properties else None
            if first_output_key:
                outputs.properties["output"] = OutputsField(
                    type="string",
                    description="输出变量"
                )
        
        input_parameters = self._convert_input_variables(self.node_data["parameters"]["inputs"])
        
        if not input_parameters:
            input_parameters["input"] = InputVariable(
                type=SourceType.ref.value,
                content="node_start.query",
                extra={"index": 0}
            )
        
        self.node.data.inputs = InputsField(
            input_parameters=input_parameters,
            llm_param=llm_param,
            system_prompt=llm_param["systemPrompt"],
            history_enable=False,
            max_response=3
        )
        
        self.node.data.outputs = outputs
        required_keys = [k for k in outputs.properties.keys() if k != "output"]
        self.node.data.outputs.required = required_keys
