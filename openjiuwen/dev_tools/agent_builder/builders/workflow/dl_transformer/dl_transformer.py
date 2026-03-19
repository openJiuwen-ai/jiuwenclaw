# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Optional, List, Dict, Any

from openjiuwen.core.common.security.json_utils import JsonUtils

from openjiuwen.core.common.logging import LogManager
from openjiuwen.dev_tools.agent_builder.utils.utils import extract_json_from_text
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.models import (
    Workflow,
    Position,
    NodeType
)
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.converter_utils import ConverterUtils
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.simpleir_to_mermaid import SimpleIrToMermaid
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.converters import (
    StartConverter,
    EndConverter,
    LLMConverter,
    IntentDetectionConverter,
    QuestionerConverter,
    CodeConverter,
    PluginConverter,
    OutputConverter,
    BranchConverter
)
logger = LogManager.get_logger("agent_builder")


class DLTransformer:
    """DL transformer.

    Transforms DL format to Mermaid flowchart and workflow DSL.

    Example:
        ```python
        transformer = DLTransformer()
        mermaid = transformer.transform_to_mermaid(dl_content)
        dsl = transformer.transform_to_dsl(dl_content, resource)
        ```
    """

    _dsl_converter_registry: Dict[str, type] = {
        NodeType.Start.dl_type: StartConverter,
        NodeType.End.dl_type: EndConverter,
        NodeType.LLM.dl_type: LLMConverter,
        NodeType.IntentDetection.dl_type: IntentDetectionConverter,
        NodeType.Questioner.dl_type: QuestionerConverter,
        NodeType.Code.dl_type: CodeConverter,
        NodeType.Plugin.dl_type: PluginConverter,
        NodeType.Output.dl_type: OutputConverter,
        NodeType.Branch.dl_type: BranchConverter,
    }

    @staticmethod
    def collect_plugin(
            tool_id_list: List[str],
            plugin_dict: Dict[str, Dict[str, Any]],
            tool_id_map: Dict[str, str]
    ) -> List[Dict[str, Any]]:
        """
        Collect plugin information.

        Args:
            tool_id_list: Tool ID list
            plugin_dict: Plugin dictionary
            tool_id_map: Tool ID to plugin ID mapping

        Returns:
            Plugin information list
        """
        collected: List[Dict[str, Any]] = []
        for tool_id in tool_id_list:
            if tool_id not in tool_id_map:
                continue

            plugin_id = tool_id_map[tool_id]
            plugin = plugin_dict.get(plugin_id, {})
            tool = plugin.get("tools", {}).get(tool_id, {})

            collected.append({
                "plugin_id": plugin_id,
                "plugin_name": plugin.get("plugin_name", ""),
                "tool_id": tool_id,
                "tool_name": tool.get("tool_name", ""),
                "inputs": tool.get("ori_inputs", []),
                "outputs": tool.get("ori_outputs", []),
            })

        return collected

    @staticmethod
    def transform_to_mermaid(dl_content: str) -> str:
        """
        Transform to Mermaid flowchart.

        Args:
            dl_content: DL content (JSON string)

        Returns:
            Mermaid code string
        """
        json_text = extract_json_from_text(dl_content)
        nodes = JsonUtils.safe_json_loads(json_text)
        if not isinstance(nodes, list):
            raise ValueError(
                f"DL content format error: expected JSON array (list), got {type(nodes)}"
            )
        mermaid_result = SimpleIrToMermaid.transform_to_mermaid(nodes)

        logger.debug(
            "Mermaid transformation completed",
            node_count=len(nodes)
        )

        return mermaid_result

    def transform_to_dsl(
            self,
            dl_content: str,
            resource: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Transform to workflow DSL.

        Args:
            dl_content: DL content (JSON string)
            resource: Resource dictionary (optional)

        Returns:
            Workflow DSL (JSON string)
        """
        if resource:
            tool_id_list = [
                item["tool_id"]
                for item in resource.get("plugins", [])
            ]
            plugins = self.collect_plugin(
                tool_id_list,
                resource.get("plugin_dict", {}),
                resource.get("tool_id_map", {})
            )
            resource["plugins"] = plugins

        json_text = extract_json_from_text(dl_content)
        nodes = JsonUtils.safe_json_loads(json_text)
        if not isinstance(nodes, list):
            raise ValueError(
                f"DL content format error: expected JSON array (list), got {type(nodes)}"
            )
        nodes_dict = {node["id"]: node for node in nodes}
        workflow = Workflow()
        x, y = 0, 0

        for node in nodes:
            converter_class = self._dsl_converter_registry.get(node["type"])
            if not converter_class:
                logger.warning(
                    "Unsupported node type",
                    node_type=node["type"],
                    node_id=node["id"]
                )
                continue

            if node["type"] in [NodeType.Plugin.dl_type]:
                node_converter = converter_class(
                    node,
                    nodes_dict,
                    resource=resource,
                    position=Position(x, y)
                )
            else:
                node_converter = converter_class(
                    node,
                    nodes_dict,
                    position=Position(x, y)
                )

            node_converter.convert()
            workflow.nodes.append(node_converter.node)
            workflow.edges.extend(node_converter.edges)
            x += 20
            y += 20

        logger.debug(
            "DSL transformation completed",
            node_count=len(workflow.nodes),
            edge_count=len(workflow.edges)
        )

        return JsonUtils.safe_json_dumps(
            ConverterUtils.convert_to_dict(workflow),
            ensure_ascii=False
        )
