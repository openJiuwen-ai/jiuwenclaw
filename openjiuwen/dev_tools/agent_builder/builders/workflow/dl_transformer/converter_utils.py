# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import re
import uuid
from enum import Enum
from typing import Optional, Tuple, Any, Dict
from dataclasses import asdict, is_dataclass

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ValidationError
from openjiuwen.core.common.logging import LogManager
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.models import SourceType

logger = LogManager.get_logger("agent_builder")


class ConverterUtils:
    """Converter utilities for DL to DSL transformation.

    Provides common utility functions for DL to DSL conversion.
    """

    LLM_MODEL_CONFIG: Dict[str, str] = {
        "id": "52",
        "name": "siliconf-qwen3-8b",
        "type": "Qwen/Qwen3-8B"
    }
    _VAR_PATTERN = re.compile(r"\$\{\s*(\w+)\.(\w+)\s*\}")

    @staticmethod
    def generate_node_id(prefix: str) -> str:
        """
        Generate a node ID.

        Args:
            prefix: Node prefix

        Returns:
            Generated node ID
        """
        return f"{prefix}_{uuid.uuid4().hex[:5]}"

    @staticmethod
    def extract_variable(expr: str) -> Optional[Tuple[str, str]]:
        """
        Extract node and variable from expression.

        Extracts node and variable from '${node.variable}' format string.

        Args:
            expr: Variable expression, e.g., '${node_start.query}'

        Returns:
            Tuple[str, str]: (node, variable), or None if not matched
        """
        match = ConverterUtils._VAR_PATTERN.match(expr.strip())
        if not match:
            return None
        node, variable = match.groups()
        return node, variable

    @staticmethod
    def convert_ref_variable(expr: str) -> Dict[str, Any]:
        """
        Convert reference variable.

        Args:
            expr: Variable expression

        Returns:
            Converted reference variable dictionary
        """
        ref_node_variable = ConverterUtils.extract_variable(expr)
        if not ref_node_variable:
            raise ValidationError(
                StatusCode.WORKFLOW_EXECUTE_INPUT_INVALID,
                msg=f"Invalid reference variable expression: {expr}",
                details={"expr": expr},
            )

        ref_node, ref_variable = ref_node_variable
        variable_name_list = ref_variable.split("_of_")[::-1]
        return {
            "type": SourceType.ref.value,
            "content": [ref_node, *variable_name_list]
        }

    @staticmethod
    def convert_llm_param(
            system_prompt: str,
            user_prompt: str
    ) -> Dict[str, Any]:
        """
        Convert LLM parameters.

        Args:
            system_prompt: System prompt
            user_prompt: User prompt

        Returns:
            LLM parameter dictionary
        """
        return {
            "systemPrompt": {
                "type": "template",
                "content": system_prompt
            },
            "prompt": {
                "type": "template",
                "content": user_prompt
            },
            "mode": ConverterUtils.LLM_MODEL_CONFIG
        }

    @staticmethod
    def convert_to_dict(obj: Any) -> Dict[str, Any]:
        """
        Convert to dictionary (removing None values).

        Args:
            obj: Object to convert

        Returns:
            Converted dictionary
        """

        def convert(value: Any) -> Any:
            if is_dataclass(value):
                return {
                    k: v for k, v in convert(asdict(value)).items()
                    if v is not None
                }
            elif isinstance(value, Enum):
                return value.value
            elif isinstance(value, list):
                converted_list = [convert(v) for v in value if v is not None]
                return [v for v in converted_list if v is not None]
            elif isinstance(value, dict):
                return {
                    k: convert(v) for k, v in value.items()
                    if v is not None
                }
            else:
                return value

        if obj is None:
            return {}

        if isinstance(obj, list):
            return [
                convert(item) for item in obj if item is not None
            ]
        elif is_dataclass(obj) or isinstance(obj, dict):
            return convert(obj)
        else:
            return getattr(obj, "__dict__", {})
