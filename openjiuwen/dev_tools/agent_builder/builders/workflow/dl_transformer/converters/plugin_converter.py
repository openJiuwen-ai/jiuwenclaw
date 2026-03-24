# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Dict, Any

from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.converters.base import BaseConverter
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.models import (
    InputsField,
    OutputPropertySpec,
    OutputsField,
)


class PluginConverter(BaseConverter):
    PLUGIN_DEFAULT_OUTPUTS = {
        "error_code": {
            "type": "integer",
            "extra": {"index": 1}
        },
        "error_message": {
            "type": "string",
            "extra": {"index": 2}
        },
        "data": {
            "type": "object",
            "extra": {"index": 3},
            "properties": {}
        }
    }

    @staticmethod
    def is_local_code_plugin(plugin_info: dict) -> bool:
        """判断是否为本地代码插件"""
        return bool(plugin_info.get("language") or plugin_info.get("code"))

    @staticmethod
    def is_cloud_plugin(plugin_info: dict) -> bool:
        """判断是否为云端插件"""
        return bool(plugin_info.get("path") or plugin_info.get("method"))

    @staticmethod
    def _convert_plugin_info(plugin_info):
        return {
            "toolID": plugin_info.get("tool_id", ""),
            "toolName": plugin_info.get("tool_name", ""),
            "pluginID": plugin_info.get("plugin_id", ""),
            "pluginName": plugin_info.get("plugin_name", ""),
            "pluginVersion": plugin_info.get("plugin_version", "draft")
        }

    def _convert_outputs_with_defaults(self, outputs):
        result = OutputsField(type="object", properties={}, required=[])
        for item in outputs:
            variable_name_list = item["name"].split("_of_")[::-1]
            result.add_property(OutputPropertySpec(
                variable_names=variable_name_list,
                description=item["description"],
                index=self._variable_index,
                var_type=item.get("type"),
            ))
            self._variable_index += 1
        
        for name, config in PluginConverter.PLUGIN_DEFAULT_OUTPUTS.items():
            if name not in result.properties:
                if name == "data":
                    result.properties[name] = OutputsField(
                        type=config["type"],
                        extra=config["extra"],
                        properties={}
                    )
                else:
                    result.properties[name] = OutputsField(
                        type=config["type"],
                        extra=config["extra"]
                    )
        
        result.required = ["error_code", "error_message", "data"]
        return result

    def _convert_specific_config(self):
        dl_configs = self.node_data["parameters"].get("configs", {})
        tool_id = dl_configs.get("tool_id", "")
        
        plugins = self.resource.get("plugins", []) if self.resource else []
        plugin_info = next((p for p in plugins if p.get("tool_id") == tool_id), {})
        
        plugin_param = self._convert_plugin_info(plugin_info)
        
        self.node.data.inputs = InputsField(
            input_parameters=self._convert_input_variables(self.node_data["parameters"]["inputs"]),
            plugin_param=plugin_param
        )
        self.node.data.outputs = self._convert_outputs_with_defaults(self.node_data["parameters"]["outputs"])
