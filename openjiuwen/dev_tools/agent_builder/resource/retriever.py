# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import os
from typing import List, Dict, Any, Optional

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ValidationError, ExecutionError
from openjiuwen.core.common.logging import LogManager
from openjiuwen.core.common.security.json_utils import JsonUtils
from openjiuwen.core.foundation.llm import Model

from openjiuwen.dev_tools.agent_builder.resource.processor import PluginProcessor
from openjiuwen.dev_tools.agent_builder.resource.prompt import RETRIEVE_SYSTEM_TEMPLATE
from openjiuwen.dev_tools.agent_builder.utils.utils import (
    extract_json_from_text,
    format_dialog_history,
    load_json_file,
)

logger = LogManager.get_logger("agent_builder")


class ResourceRetriever:
    """Resource Retriever

    Intelligently retrieves relevant plugins, knowledge bases, workflows, etc.
    from dialog history.

    Example:
        ```python
        retriever = ResourceRetriever(llm_service)
        resources = retriever.retrieve(dialog_history, for_workflow=True)
        ```
    """

    def __init__(self, llm: Model) -> None:
        """
        Initialize resource retriever

        Args:
            llm: LLM service instance
        """
        self.llm: Model = llm

        # Load and preprocess plugin resources
        raw_plugins = self.load_resources()
        self.plugin_dict: Dict[str, Dict[str, Any]]
        self.tool_plugin_id_map: Dict[str, str]
        self.plugin_dict, self.tool_plugin_id_map = PluginProcessor.preprocess(
            raw_plugins
        )

        logger.debug(
            "Resource retriever initialized",
            plugin_count=len(self.plugin_dict),
            tool_count=len(self.tool_plugin_id_map)
        )

    @staticmethod
    def load_resources(source_path: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Load resource file

        Args:
            source_path: Resource directory path (JSON file); if empty, uses default `plugins.json`

        Returns:
            Raw plugin list

        Raises:
            FileNotFoundError: When resource file does not exist
        """
        current_dir = os.path.dirname(__file__)
        plugin_json = source_path or os.path.join(current_dir, "plugins.json")

        if not os.path.exists(plugin_json):
            logger.warning("Plugin config file not found, using empty list", file_path=plugin_json)
            return []

        data = load_json_file(plugin_json)
        return data.get("plugins", [])

    def retrieve(
            self,
            dialog_history: List[Dict[str, str]],
            for_workflow: bool = True
    ) -> Dict[str, Any]:
        """
        Retrieve relevant resources

        Args:
            dialog_history: Dialog history list, each element contains 'role' and 'content'
            for_workflow: Whether for workflow builder (affects returned data format)

        Returns:
            Retrieved resource dict, containing:
                - plugins: Plugin list
                - plugin_dict: Plugin dict
                - tool_id_map: Tool ID mapping

        Raises:
            ExecutionError: When retrieval fails
        """
        try:
            # Format dialog history
            dialog_history_query = format_dialog_history(dialog_history)
            plugin_info_list = PluginProcessor.format_for_prompt(self.plugin_dict)

            # Build system prompt (using core PromptTemplate)
            messages = RETRIEVE_SYSTEM_TEMPLATE.format({
                "dialog_history": dialog_history_query,
                "plugin_info_list": str(plugin_info_list),
            }).to_messages()

            # Call LLM for retrieval
            data = self._llm_retrieve(messages)

            # Process retrieval results
            tool_id_list = data.get("tool_id_list", [])
            retrieved_plugin, retrieved_plugin_dict, retrieved_tool_id_map = (
                PluginProcessor.get_retrieved_info(
                    tool_id_list,
                    self.plugin_dict,
                    self.tool_plugin_id_map,
                    need_inputs_outputs=for_workflow
                )
            )

            logger.info(
                "Resource retrieval completed",
                plugin_count=len(retrieved_plugin),
                for_workflow=for_workflow
            )

            return {
                "plugins": retrieved_plugin,
                "plugin_dict": retrieved_plugin_dict,
                "tool_id_map": retrieved_tool_id_map
            }

        except Exception as e:
            error_msg = f"Resource retrieval failed: {str(e)}"
            logger.error(
                "Resource retrieval failed",
                error=str(e),
                for_workflow=for_workflow
            )
            raise ExecutionError(
                StatusCode.AGENT_BUILDER_RESOURCE_RETRIEVE_ERROR,
                msg=error_msg,
                details={"for_workflow": for_workflow},
                cause=e,
            ) from e

    def _llm_retrieve(self, prompts: List) -> Dict[str, Any]:
        """
        Use LLM to retrieve resources

        Args:
            prompts: Assembled message list (from PromptTemplate.format().to_messages())

        Returns:
            Retrieval result dict, containing tool_id_list

        Raises:
            ValidationError: When LLM call fails or returns format error
        """
        try:
            import asyncio
            response = asyncio.run(self.llm.invoke(prompts))

            # Extract JSON
            json_text = extract_json_from_text(response.content)

            # Parse JSON
            data = JsonUtils.safe_json_loads(json_text, default={})

            if not isinstance(data, dict):
                raise ValueError(
                    f"LLM returned format error, expected dict, got: {type(data)}"
                )

            return data

        except Exception as e:
            error_msg = f"LLM retrieval call failed: {str(e)}"
            logger.error("LLM retrieval call failed", error=str(e))
            raise ValidationError(
                StatusCode.RESOURCE_VALUE_INVALID,
                msg=error_msg,
                details={"error": str(e)},
                cause=e,
            ) from e
