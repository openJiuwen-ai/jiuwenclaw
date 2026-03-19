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

        now_ms_timestamp = int(datetime.now(timezone.utc).timestamp() * MS_PER_SECOND)
        dsl["create_time"] = now_ms_timestamp
        dsl["update_time"] = now_ms_timestamp

        return JsonUtils.safe_json_dumps(dsl)
