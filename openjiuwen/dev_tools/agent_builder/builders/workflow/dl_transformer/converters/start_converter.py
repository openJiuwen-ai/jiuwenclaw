# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.converters.base import BaseConverter


class StartConverter(BaseConverter):
    """Start node converter."""

    def _convert_specific_config(self) -> None:
        """Convert Start node specific configuration."""
        outputs = self._convert_outputs_field(
            self.node_data["parameters"]["outputs"]
        )
        self.node.data.outputs = outputs
        if outputs.properties:
            self.node.data.outputs.required = list(outputs.properties.keys())
        