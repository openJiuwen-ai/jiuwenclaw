# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import ast
import asyncio
from typing import Dict, Any, Tuple, List

from openjiuwen.core.common.logging import LogManager
from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.foundation.llm import SystemMessage
from openjiuwen.core.common.security.json_utils import JsonUtils

from openjiuwen.dev_tools.agent_builder.builders.llm_agent.prompts import (
    FACTOR_SYSTEM_PROMPT,
    RESOURCE_SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
    RESOURCE_USER_PROMPT_TEMPLATE
)
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ApplicationError

logger = LogManager.get_logger("agent_builder")


RESOURCE_CONFIG = {
    "plugin": {
        "label": "Plugin",
        "id_key": "tool_id",
        "name_key": "tool_name",
        "desc_key": "tool_desc"
    },
    "knowledge": {
        "label": "Knowledge Base",
        "id_key": "knowledge_id",
        "name_key": "knowledge_name",
        "desc_key": "knowledge_desc"
    },
    "workflow": {
        "label": "Workflow",
        "id_key": "workflow_id",
        "name_key": "workflow_name",
        "desc_key": "workflow_desc"
    }
}


class Clarifier:
    """Requirement Clarifier

    Responsible for analyzing user requirements, extracting Agent basic elements,
    and planning required resources.

    Example:
        ```python
        clarifier = Clarifier(llm_service)
        factor_output, display_resource, resource_id_dict = clarifier.clarify(
            messages="Create a customer service assistant",
            resource={"plugin": [...]}
        )
        ```
    """

    def __init__(self, llm: Model) -> None:
        """
        Initialize Requirement Clarifier

        Args:
            llm: LLM service instance
        """
        self.llm = llm

    @staticmethod
    def _parse_resource_output(
            resource_output: str,
            available_resources: Dict[Any, Any]
    ) -> Tuple[str, Dict[str, List[str]]]:
        """
        Parse resource planning output

        Args:
            resource_output: Resource planning text returned by LLM
            available_resources: Available resources dict

        Returns:
            Tuple[str, Dict[str, List[str]]]: (display content, resource ID dict)
        """
        if "## Agent资源规划" not in resource_output:
            return "", {}

        resource_planning = resource_output.split("## Agent资源规划")[1].strip()

        display_content = []
        id_dict = {}

        for resource_type, config in RESOURCE_CONFIG.items():
            section_start = f"【选择的{config['label']}】"
            if section_start not in resource_planning:
                continue

            section_content = resource_planning.split(section_start)[1].split("【选择")[0].strip()

            try:
                resource_list = ast.literal_eval(section_content)
                if not isinstance(resource_list, list):
                    continue

                valid_resources = []
                id_list = []

                if resource_type == "plugin":
                    available_key = "plugins"
                else:
                    available_key = resource_type

                available_ids = set()
                if available_key in available_resources:
                    for item in available_resources[available_key]:
                        item_id = item.get(config["id_key"])
                        if item_id:
                            available_ids.add(item_id)

                for idx, resource in enumerate(resource_list, 1):
                    if not isinstance(resource, dict):
                        continue

                    name = resource.get(config["name_key"], "")
                    desc = resource.get(config["desc_key"], "")
                    resource_id = resource.get(config["id_key"])

                    if resource_id and resource_id in available_ids:
                        if name and desc:
                            valid_resources.append(f"{idx}. {name}: {desc}")
                        id_list.append(resource_id)
                    else:
                        logger.warning(f"Resource ID {resource_id} not in available resources",
                         resource_type=resource_type)

                if valid_resources:
                    display_content.append(f"【Selected {config['label']}】\n" + "\n".join(valid_resources))
                    if id_list:
                        id_dict[resource_type] = id_list

            except Exception as e:
                logger.error("Resource parsing failed", error=str(e), resource_type=resource_type)
                raise ApplicationError(
                    StatusCode.AGENT_BUILDER_RESOURCE_PARSE_ERROR,
                    msg=f"NL2LLM Agent requirement clarification resource parsing exception: {str(e)}",
                    details={
                        "resource_type": resource_type,
                        "error": str(e),
                    },
                    cause=e,
                ) from e

        return "\n".join(display_content), id_dict

    def clarify(
            self,
            messages: str,
            resource: Dict[Any, Any]
    ) -> Tuple[str, str, Dict[str, List[str]]]:
        """
        Clarify requirements and plan resources

        Args:
            messages: User messages (dialog history string)
            resource: Available resources dict

        Returns:
            Tuple[str, str, Dict[str, List[str]]]: (basic elements output, display resources, resource ID dict)
        """
        user_messages = USER_PROMPT_TEMPLATE.format({"user_messages": messages}).to_messages()

        factor_output = asyncio.run(
            self.llm.invoke([SystemMessage(content=FACTOR_SYSTEM_PROMPT)] + user_messages)
        ).content

        resource_str = JsonUtils.safe_json_dumps(resource)
        resource_user_messages = RESOURCE_USER_PROMPT_TEMPLATE.format({
            "agent_factor_info": factor_output,
            "resource": resource_str
        }).to_messages()

        resource_output = asyncio.run(
            self.llm.invoke([SystemMessage(content=RESOURCE_SYSTEM_PROMPT)] + resource_user_messages)
        ).content

        display_resource, resource_id_dict = self._parse_resource_output(resource_output, resource)

        return factor_output, display_resource, resource_id_dict
