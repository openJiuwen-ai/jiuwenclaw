# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any

from openjiuwen.core.common.security.json_utils import JsonUtils

from openjiuwen.dev_tools.agent_builder.builders.llm_agent.template import LLM_AGENT_TEMPLATE
from openjiuwen.core.common.logging import LogManager

logger = LogManager.get_logger("agent_builder")

MS_PER_SECOND = 1000


class Transformer:
    """DSL Transformer

    Transforms generated Agent configuration to standard DSL format.

    Example:
        ```python
        transformer = Transformer()
        dsl = transformer.transform_to_dsl(
            agent_info={"name": "Customer Service Assistant", ...},
            resource={"plugin_dict": {...}, "workflow_dict": {...}}
        )
        ```
    """

    @staticmethod
    def collect_plugin(
            tool_id_list: List[str],
            plugin_dict: Dict[str, dict],
            tool_id_map: Dict[str, str]
    ) -> List[dict]:
        """
        Collect plugin info

        Args:
            tool_id_list: Tool ID list
            plugin_dict: Plugin dict
            tool_id_map: Tool ID map

        Returns:
            List[dict]: Plugin info list
        """
        collected = []
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
            })
        return collected

    @staticmethod
    def collect_workflow(
            workflow_id_list: List[str],
            workflow_dict: Dict[str, dict]
    ) -> List[dict]:
        """
        Collect workflow info

        Args:
            workflow_id_list: Workflow ID list
            workflow_dict: Workflow dict

        Returns:
            List[dict]: Workflow info list
        """
        collected = []
        for workflow_id in workflow_id_list:
            workflow = workflow_dict.get(workflow_id, {})
            collected.append({
                "workflow_id": workflow_id,
                "workflow_name": workflow.get("workflow_name"),
                "workflow_version": workflow.get("workflow_version"),
                "description": workflow.get("workflow_desc"),
            })
        return collected

    @staticmethod
    def convert_input_parameters(params: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert input parameters to platform format"""
        result = []
        for p in params or []:
            result.append({
                "name": p.get("name", ""),
                "desc": p.get("desc", p.get("description", "")),
                "type": p.get("type", 1),
                "value": p.get("value", ""),
                "method": p.get("method", 0),
                "priority": p.get("priority", 0),
                "is_runtime": p.get("is_runtime", True),
                "is_required": p.get("is_required", False)
            })
        return result

    @staticmethod
    def convert_output_parameters(params: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert output parameters to platform format"""
        result = []
        for p in params or []:
            result.append({
                "name": p.get("name", ""),
                "desc": p.get("desc", p.get("description", "")),
                "type": p.get("type", 1),
                "value": p.get("value", ""),
                "method": p.get("method", 0),
                "priority": p.get("priority", 0),
                "is_runtime": p.get("is_runtime", False),
                "is_required": p.get("is_required", False)
            })
        return result

    @staticmethod
    def _convert_tool_to_platform(
            tool: Dict[str, Any],
            plugin_id: str,
            plugin_version: str,
            current_ts: int
    ) -> Dict[str, Any]:
        """Convert tool definition to platform format"""
        return {
            "code": tool.get("code", ""),
            "language": tool.get("language", "python"),
            "request_params": Transformer.convert_input_parameters(tool.get("input_parameters", [])),
            "response_params": Transformer.convert_output_parameters(tool.get("output_parameters", [])),
            "primary_id": None,
            "tool_id": tool.get("tool_id", ""),
            "name": tool.get("tool_name", tool.get("name", "")),
            "desc": tool.get("desc", tool.get("tool_desc", "")),
            "space_id": "",
            "plugin_id": plugin_id,
            "plugin_type": 1 if tool.get("language") else 2,
            "plugin_version": plugin_version,
            "input_parameters": Transformer.convert_input_parameters(tool.get("input_parameters", [])),
            "output_parameters": Transformer.convert_output_parameters(tool.get("output_parameters", [])),
            "available": True,
            "create_time": current_ts,
            "update_time": current_ts
        }

    @staticmethod
    def build_plugin_dependencies(
            tool_id_list: List[str],
            plugin_dict: Dict[str, dict],
            tool_id_map: Dict[str, str],
            current_ts: int
    ) -> List[dict]:
        """
        Build plugin dependencies with full metadata

        Args:
            tool_id_list: Tool ID list
            plugin_dict: Plugin dict
            tool_id_map: Tool ID map
            current_ts: Current timestamp

        Returns:
            List[dict]: Plugin dependencies list
        """
        dependencies = []
        processed_plugin_ids = set()

        for tool_id in tool_id_list:
            if tool_id not in tool_id_map:
                continue

            plugin_id = tool_id_map[tool_id]
            if plugin_id in processed_plugin_ids:
                continue

            plugin = plugin_dict.get(plugin_id, {})
            if not plugin:
                continue

            plugin_version = plugin.get("plugin_version", "draft")
            plugin_name = plugin.get("plugin_name", "")
            plugin_desc = plugin.get("plugin_desc", "")

            tool_list = []
            tools = plugin.get("tools", {})
            if isinstance(tools, dict):
                for tid, tool in tools.items():
                    if tid == tool_id:
                        tool_list.append(Transformer._convert_tool_to_platform(
                            tool, plugin_id, plugin_version, current_ts
                        ))

            dependencies.append({
                "plugin_id": plugin_id,
                "plugin_version": plugin_version,
                "primary_id": None,
                "name": plugin_name,
                "desc": plugin_desc,
                "desc_mk": "",
                "url": "",
                "space_id": "",
                "icon_uri": "",
                "plugin_type": 2,
                "tools": None,
                "inputs": [],
                "create_time": current_ts,
                "update_time": current_ts,
                "tool_list": tool_list
            })

            processed_plugin_ids.add(plugin_id)

        return dependencies

    @staticmethod
    def build_workflow_dependencies(
            workflow_id_list: List[str],
            workflow_dict: Dict[str, dict],
            current_ts: int
    ) -> List[dict]:
        """
        Build workflow dependencies with full metadata

        Args:
            workflow_id_list: Workflow ID list
            workflow_dict: Workflow dict
            current_ts: Current timestamp

        Returns:
            List[dict]: Workflow dependencies list
        """
        dependencies = []
        for workflow_id in workflow_id_list:
            workflow = workflow_dict.get(workflow_id, {})
            dependencies.append({
                "workflow_id": workflow_id,
                "workflow_version": workflow.get("workflow_version", "draft"),
                "primary_id": None,
                "name": workflow.get("workflow_name", ""),
                "desc": workflow.get("workflow_desc", ""),
                "space_id": "",
                "url": "template",
                "icon_uri": "",
                "schema": "",
                "input_parameters": workflow.get("input_parameters", []),
                "output_parameters": workflow.get("output_parameters", []),
                "create_time": current_ts,
                "update_time": current_ts
            })
        return dependencies

    def transform_to_dsl(
            self,
            agent_info: dict,
            resource: Dict[str, Any]
    ) -> str:
        """
        Transform to DSL format

        Args:
            agent_info: Agent configuration info
            resource: Resource dict (containing plugin_dict, workflow_dict, tool_id_map, etc.)

        Returns:
            str: JSON format DSL string
        """
        now_ms_timestamp = int(datetime.now(timezone.utc).timestamp() * MS_PER_SECOND)

        dsl = LLM_AGENT_TEMPLATE.copy()
        dsl["agent_id"] = str(uuid.uuid4())
        dsl["name"] = agent_info.get("name", "")
        dsl["description"] = agent_info.get("description", "")
        dsl["configs"]["system_prompt"] = agent_info.get("prompt", "")
        dsl["opening_remarks"] = agent_info.get("opening_remarks", "")

        plugin_id_list = agent_info.get("plugin", [])
        if plugin_id_list and isinstance(plugin_id_list, list):
            dsl["plugins"] = self.collect_plugin(
                plugin_id_list,
                resource.get("plugin_dict", {}),
                resource.get("tool_id_map", {})
            )

        workflow_id_list = agent_info.get("workflow", [])
        if workflow_id_list and isinstance(workflow_id_list, list):
            dsl["workflows"] = self.collect_workflow(
                workflow_id_list,
                resource.get("workflow_dict", {})
            )

        dsl["create_time"] = now_ms_timestamp
        dsl["update_time"] = now_ms_timestamp

        dependencies = {
            "plugins": self.build_plugin_dependencies(
                plugin_id_list if plugin_id_list else [],
                resource.get("plugin_dict", {}),
                resource.get("tool_id_map", {}),
                now_ms_timestamp
            ),
            "workflows": self.build_workflow_dependencies(
                workflow_id_list if workflow_id_list else [],
                resource.get("workflow_dict", {}),
                now_ms_timestamp
            ),
            "knowledge_bases": [],
            "prompt_templates": []
        }

        dsl["dependencies"] = dependencies

        return JsonUtils.safe_json_dumps(dsl)
