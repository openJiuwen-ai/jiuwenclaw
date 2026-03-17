# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Utility functions for self-evolving operations.

Includes parameter validation, case/message/template serialization,
and JSON/list parsing from LLM outputs.
"""

import re
import json
from typing import Optional, List, Dict, Any, Union

from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.prompt import PromptTemplate
from openjiuwen.core.foundation.llm import BaseMessage, AssistantMessage
from openjiuwen.agent_evolving.dataset import Case, EvaluatedCase


class TuneUtils:
    """Collection of static utility methods for self-evolving operations."""

    @staticmethod
    def validate_digital_parameter(param: float, param_name: str, lower: float, upper: float) -> None:
        """Validate numeric parameter is within bounds.

        Args:
            param: Value to validate
            param_name: Parameter name for error message
            lower: Minimum allowed value
            upper: Maximum allowed value

        Raises:
            TOOLCHAIN_AGENT_PARAM_ERROR: if param is outside [lower, upper]
        """
        if param < lower or param > upper:
            raise build_error(
                StatusCode.TOOLCHAIN_AGENT_PARAM_ERROR, error_msg=f"{param_name} should be between {lower} and {upper}"
            )

    @staticmethod
    def get_input_string_from_case(case: Case) -> str:
        """Extract readable input string from Case.

        Args:
            case: Case to extract input from

        Returns:
            Formatted input string; uses messages if available, else inputs dict
        """
        return TuneUtils._convert_dict_to_string(case.inputs)

    @staticmethod
    def get_output_string_from_message(message: BaseMessage) -> str:
        """Convert BaseMessage to string for logging/comparison.

        Args:
            message: Message to convert

        Returns:
            Serialized message content; tool_calls included if present
        """
        if isinstance(message, AssistantMessage) and message.tool_calls:
            return "".join(
                "".join(
                    json.dumps(tool_call.model_dump(include={"name", "arguments"})) for tool_call in message.tool_calls
                )
            )
        return message.content

    @staticmethod
    def get_content_string_from_template(template: PromptTemplate) -> str:
        """Convert PromptTemplate to multi-line text.

        Args:
            template: PromptTemplate to convert

        Returns:
            Concatenated message contents separated by newlines
        """
        return "\n".join(msg.content for msg in template.to_messages())

    @staticmethod
    def parse_json_from_llm_response(json_like_string: str) -> Optional[Dict[str, Any]]:
        """Extract and parse JSON from ```json ... ``` block.

        Args:
            json_like_string: String containing JSON block

        Returns:
            Parsed JSON dict, or None on failure
        """
        pattern = r"```json(.*?)```"
        json_data = TuneUtils._parse_llm_response(json_like_string, pattern)
        return json_data

    @staticmethod
    def parse_list_from_llm_response(list_like_string: str) -> Optional[List[Any]]:
        """Extract and parse list from ```list ... ``` block.

        Args:
            list_like_string: String containing list block

        Returns:
            Parsed list, or None on failure
        """
        pattern = r"```list(.*?)```"
        list_data = TuneUtils._parse_llm_response(list_like_string, pattern)
        if not isinstance(list_data, list):
            logger.warning("Parsed data is not a list-type")
            return None
        return list_data

    @staticmethod
    def convert_cases_to_examples(cases: List[Union[Case, EvaluatedCase]]) -> str:
        """Format Case/EvaluatedCase list as few-shot example text.

        Args:
            cases: List of cases to format

        Returns:
            Formatted examples with question and expected answer
        """
        if not cases:
            return ""
        examples_list = [
            f"example {i + 1}:\n"
            f"[question]: {TuneUtils._convert_dict_to_string(case.inputs)}\n"
            f"[expected answer]: {TuneUtils._convert_dict_to_string(case.label)}"
            for i, case in enumerate(cases)
        ]
        return "\n".join(examples_list)

    @staticmethod
    def _convert_dict_to_string(data: Dict) -> str:
        """Convert dict to single-line string.

        Args:
            data: Dict to convert

        Returns:
            String in format "k1:v1 | k2:v2"
        """
        return " | ".join(f"{key}:{value}" for key, value in data.items())
    
    @staticmethod
    def _parse_llm_response(string: str, pattern: Optional[str] = None) -> Optional[Union[List[Any], Dict[str, Any]]]:
        matched_string = string
        if pattern is not None:
            match = re.search(pattern, string, re.DOTALL)
            if not match:
                logger.warning(f"Failed to extract string like `{pattern}` from response")
                return None
            matched_string = match.group(1).strip()

        try:
            data = json.loads(matched_string)
        except Exception:
            logger.warning("Failed to convert list string to python list")
            return None
        return data
