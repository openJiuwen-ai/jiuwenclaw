# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Dict, Any

from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.converters.base import BaseConverter
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.models import InputsField


class PluginConverter(BaseConverter):
    """Plugin node converter."""

    @staticmethod
    def _convert_plugin_info(plugin_info: Dict[str, Any]) -> Dict[str, str]:
        """Convert plugin information.

        Args:
            plugin_info: Plugin info dictionary

        Returns:
            Converted plugin info dictionary
        """
        return {
            "toolID": plugin_info.get("tool_id", ""),
            "toolName": plugin_info.get("tool_name", ""),
            "pluginID": plugin_info.get("plugin_id", ""),
            "pluginName": plugin_info.get("plugin_name", "")
        }

    def _convert_specific_config(self) -> None:
        """Convert Plugin node specific configuration."""
        plugins = (self.resource or {}).get("plugins", [])
        tool_id = self.node_data["parameters"]["configs"]["tool_id"]

        plugin_info = next(
            (p for p in plugins if p.get("tool_id") == tool_id),
            {}
        )
        plugin_info = self._convert_plugin_info(plugin_info)

        self.node.data.inputs = InputsField(
            input_parameters=self._convert_input_variables(
                self.node_data["parameters"]["inputs"]
            ),
            plugin_param=plugin_info
        )
        self.node.data.outputs = self._convert_outputs_field(
            self.node_data["parameters"]["outputs"]
        )
        