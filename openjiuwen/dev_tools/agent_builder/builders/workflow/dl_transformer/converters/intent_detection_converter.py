# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import uuid
from typing import List, Dict, Any

from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.converters.base import BaseConverter
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.models import Edge, InputsField
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.converter_utils import ConverterUtils


class IntentDetectionConverter(BaseConverter):
    """IntentDetection node converter."""

    @staticmethod
    def _convert_intents(conditions: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        """Convert intent list.

        Args:
            conditions: Condition list

        Returns:
            Intent list
        """
        intents = []
        for cond in conditions:
            if cond["expression"] != "default":
                parts = cond["expression"].split(" contain ")
                if len(parts) > 1:
                    intent_name = parts[1]
                    intents.append({
                        "name": intent_name,
                        "id": cond.get("intent_id", f"intent_{uuid.uuid4().hex[:8]}")
                    })
        return intents

    @staticmethod
    def _convert_branches(
            conditions: List[Dict[str, Any]]
    ) -> List[Dict[str, str]]:
        """Convert branch list.

        Args:
            conditions: Condition list

        Returns:
            Branch list
        """
        return [{"branchId": cond["branch"]} for cond in conditions]

    def _convert_specific_config(self) -> None:
        """Convert IntentDetection node specific configuration."""
        prompt_content = self.node_data["parameters"]["configs"].get("prompt", "")
        llm_param = {
            "systemPrompt": {
                "type": "template",
                "content": ""
            },
            "prompt": {
                "type": "template",
                "content": prompt_content
            },
            "model": ConverterUtils.LLM_MODEL_CONFIG
        }
        input_vars = self._convert_input_variables(self.node_data["parameters"]["inputs"])
        renamed_input_vars = {}
        for key, value in input_vars.items():
            renamed_input_vars["query"] = value
        self._intents = self._convert_intents(self.node_data["parameters"]["conditions"])
        self.node.data.inputs = InputsField(
            input_parameters=renamed_input_vars,
            llm_param=llm_param,
            intents=self._intents
        )
        self.node.data.outputs = self._convert_outputs_field(
            [{"name": "classification_id", "type": "integer", "description": None}]
        )
        self.node.data.outputs.required.append("classification_id")


    def convert_edges(self) -> None:
        """Convert edges (IntentDetection node has multiple branches)."""
        intent_id_map = {}
        for intent in self._intents:
            intent_name = intent["name"]
            intent_id = intent["id"]
            for cond in self.node_data["parameters"]["conditions"]:
                if cond["expression"] != "default":
                    parts = cond["expression"].split(" contain ")
                    if len(parts) > 1 and parts[1] == intent_name:
                        intent_id_map[cond["branch"]] = intent_id
                        break
        for cond in self.node_data["parameters"]["conditions"]:
            if cond["expression"] == "default":
                self.edges.append(
                    Edge(source_node_id=self.node_data["id"], target_node_id=cond["next"], source_port_id="0")
                )
            else:
                source_port_id = intent_id_map.get(cond["branch"])
                if source_port_id:
                    self.edges.append(
                        Edge(source_node_id=self.node_data["id"], 
                        target_node_id=cond["next"], source_port_id=source_port_id)
                    )
