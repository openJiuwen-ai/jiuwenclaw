# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import re
import asyncio
from typing import Dict, Any

from openjiuwen.core.common.logging import LogManager
from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.foundation.llm import SystemMessage

from openjiuwen.dev_tools.agent_builder.builders.llm_agent.prompts import (
    GENERATE_SYSTEM_PROMPT,
    GENERATE_USER_PROMPT_TEMPLATE
)

logger = LogManager.get_logger("agent_builder")


class Generator:
    """Configuration Generator

    Generates complete Agent configuration based on requirement clarification results.

    Example:
        ```python
        generator = Generator(llm_service)
        agent_info = generator.generate(
            message="User message",
            agent_config_info="Basic elements planning",
            agent_resource_info="Resource planning info",
            resource_id_dict={"plugin": [...]}
        )
        ```
    """

    _EXTRACT_ELEMENTS = {
        "name": "Role Name",
        "description": "Role Description",
        "prompt": "Prompt",
        "opening_remarks": "Agent Opening Remarks",
        "question": "Preset Questions",
    }

    def __init__(self, llm: Model) -> None:
        """
        Initialize Configuration Generator

        Args:
            llm: LLM service instance
        """
        self.llm = llm

    @staticmethod
    def _parse_info(content: str) -> Dict[str, Any]:
        """
        Parse generated configuration info

        Args:
            content: Configuration text returned by LLM

        Returns:
            Dict[str, Any]: Parsed configuration dict
        """
        def _parse_element(content: str, key: str) -> str:
            pattern = rf'<{key}>(.*?)</{key}>'
            match = re.search(pattern, content, re.DOTALL)
            return match.group(1).strip() if match else ""

        info_dict = {}
        for key, value in Generator._EXTRACT_ELEMENTS.items():
            extracted_content = _parse_element(content, value)
            info_dict[key] = extracted_content

        plugin_list = _parse_element(content, "Selected Plugin List")
        knowledge_list = _parse_element(content, "Selected Knowledge Base List")
        workflow_list = _parse_element(content, "Selected Workflow List")

        info_dict["plugin"] = plugin_list
        info_dict["knowledge"] = knowledge_list
        info_dict["workflow"] = workflow_list

        return info_dict

    def generate(
            self,
            message: str,
            agent_config_info: str,
            agent_resource_info: str,
            resource_id_dict: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Generate Agent configuration

        Args:
            message: User message (dialog history string)
            agent_config_info: Agent basic elements planning info
            agent_resource_info: Agent resource planning info
            resource_id_dict: Resource ID dict (for subsequent transformation)

        Returns:
            Dict[str, Any]: Generated Agent configuration info
        """
        user_messages = GENERATE_USER_PROMPT_TEMPLATE.format({
            "user_message": message,
            "agent_config_info": agent_config_info,
            "agent_resource_info": agent_resource_info
        }).to_messages()

        generated_content = asyncio.run(
            self.llm.invoke([SystemMessage(content=GENERATE_SYSTEM_PROMPT)] + user_messages)
        ).content
        logger.debug("Generated Agent configuration", output_length=len(generated_content))

        content_parse = self._parse_info(generated_content)

        if resource_id_dict:
            content_parse.update({
                "plugin": resource_id_dict.get("plugin", []),
                "knowledge": resource_id_dict.get("knowledge", []),
                "workflow": resource_id_dict.get("workflow", [])
            })

        return content_parse
