# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import List, Dict, Any, Tuple


TYPE_MAP = {
    1: "string",
    2: "integer",
    3: "number",
    4: "boolean",
    5: "array",
    6: "object",
}


def convert_type(param_type: Any) -> str:
    """
    Convert parameter type to string format
    
    Args:
        param_type: Parameter type (could be int or str)
        
    Returns:
        String type name
    """
    if isinstance(param_type, int):
        return TYPE_MAP.get(param_type, "string")
    if isinstance(param_type, str):
        return param_type
    return "string"


class PluginProcessor:
    """Plugin Processor

    Provides preprocessing, formatting and retrieval functions for plugin resources.
    """

    @staticmethod
    def preprocess(raw_plugins: List[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
        """
        Preprocess plugin data

        Converts raw plugin list to dict structure for easy querying.

        Args:
            raw_plugins: Raw plugin list

        Returns:
            Tuple[Dict, Dict]: (plugin_dict, tool_plugin_id_map)
                - plugin_dict: Plugin dict, key is plugin_id
                - tool_plugin_id_map: Mapping from tool ID to plugin ID
        """

        def format_params(params: List[Dict[str, Any]]) -> List[Dict[str, str]]:
            """
            Format parameter list

            Args:
                params: Parameter list

            Returns:
                Formatted parameter list
            """
            return [
                {
                    "name": p.get("name", ""),
                    "description": p.get("description") or p.get("desc", ""),
                    "type": convert_type(p.get("type", 1))
                }
                for p in (params or [])
            ]

        plugin_dict: Dict[str, Dict[str, Any]] = {}
        tool_plugin_id_map: Dict[str, str] = {}

        for plugin in raw_plugins or []:
            plugin_id = plugin.get("plugin_id", "")
            if not plugin_id:
                continue

            formatted_tools: Dict[str, Dict[str, Any]] = {}
            for tool in plugin.get("tools", []):
                tool_id = tool.get("tool_id")
                if not tool_id:
                    continue

                tool_plugin_id_map[tool_id] = plugin_id
                input_params = tool.get("input_parameters", [])
                output_params = tool.get("output_parameters", [])

                tool_dict: Dict[str, Any] = {
                    "tool_id": tool_id,
                    "tool_name": tool.get("tool_name", ""),
                    "tool_desc": tool.get("desc", ""),
                    "code": tool.get("code", ""),
                    "language": tool.get("language", ""),
                    "input_parameters": input_params,
                    "output_parameters": output_params,
                    "ori_inputs": input_params,
                    "ori_outputs": output_params,
                    "inputs_for_dl_gen": format_params(input_params),
                    "outputs_for_dl_gen": format_params(output_params)
                }
                formatted_tools[tool_id] = tool_dict

            plugin_dict[plugin_id] = {
                "plugin_id": plugin_id,
                "plugin_name": plugin.get("plugin_name", ""),
                "plugin_desc": plugin.get("plugin_desc", ""),
                "plugin_version": plugin.get("plugin_version", "draft"),
                "tools": formatted_tools,
            }

        return plugin_dict, tool_plugin_id_map

    @staticmethod
    def format_for_prompt(plugin_dict: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Format plugin info for prompt

        Args:
            plugin_dict: Plugin dict

        Returns:
            Formatted plugin list (simplified version for LLM prompt)
        """
        def convert_params(params: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            """Convert parameters with type mapping"""
            return [
                {
                    "name": p.get("name", ""),
                    "desc": p.get("desc", p.get("description", "")),
                    "type": convert_type(p.get("type", 1))
                }
                for p in (params or [])
            ]

        result: List[Dict[str, Any]] = []
        for plugin in plugin_dict.values():
            tools_brief = [
                {
                    "tool_id": t["tool_id"],
                    "tool_name": t["tool_name"],
                    "tool_desc": t["tool_desc"],
                    "code": t.get("code", ""),
                    "language": t.get("language", ""),
                    "input_parameters": convert_params(t.get("input_parameters", [])),
                    "output_parameters": convert_params(t.get("output_parameters", []))
                }
                for t in plugin.get("tools", {}).values()
            ]
            result.append({
                "plugin_id": plugin["plugin_id"],
                "plugin_name": plugin["plugin_name"],
                "plugin_desc": plugin["plugin_desc"],
                "tools": tools_brief,
            })
        return result

    @staticmethod
    def get_retrieved_info(
            tool_id_list: List[str],
            plugin_dict: Dict[str, Dict[str, Any]],
            tool_plugin_id_map: Dict[str, str],
            need_inputs_outputs: bool = True
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]], Dict[str, str]]:
        """
        Get retrieved plugin info

        Args:
            tool_id_list: Tool ID list
            plugin_dict: Plugin dict
            tool_plugin_id_map: Mapping from tool ID to plugin ID
            need_inputs_outputs: Whether input/output parameters are needed

        Returns:
            Tuple[List, Dict, Dict]: (retrieved_plugin, retrieved_plugin_dict, retrieved_tool_id_map)
                - retrieved_plugin: Retrieved tool details list
                - retrieved_plugin_dict: Retrieved plugin dict
                - retrieved_tool_id_map: Retrieved tool ID mapping
        """
        tool_detail_list: List[Dict[str, Any]] = []
        retrieved_plugin_dict: Dict[str, Dict[str, Any]] = {}
        retrieved_tool_id_map: Dict[str, str] = {}

        for tool_id in tool_id_list:
            if tool_id not in tool_plugin_id_map:
                continue

            plugin_id = tool_plugin_id_map[tool_id]
            tool_info = plugin_dict[plugin_id]["tools"][tool_id]

            tool_detail: Dict[str, Any] = {
                "tool_id": tool_id,
                "tool_name": tool_info.get("tool_name", ""),
                "tool_desc": tool_info.get("tool_desc", "")
            }

            if need_inputs_outputs:
                tool_detail.update({
                    "inputs": tool_info.get("inputs_for_dl_gen", []),
                    "outputs": tool_info.get("outputs_for_dl_gen", [])
                })

            tool_detail_list.append(tool_detail)
            retrieved_plugin_dict.update({plugin_id: plugin_dict[plugin_id]})
            retrieved_tool_id_map.update({tool_id: plugin_id})

        return tool_detail_list, retrieved_plugin_dict, retrieved_tool_id_map
