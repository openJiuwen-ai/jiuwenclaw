# -*- coding: UTF-8 -*-
"""Utility functions for ReMe retrieval operations."""

import json
import re
from typing import List, Optional

from openjiuwen.core.common.logging import context_engine_logger as logger


def parse_json_list_response(response: str, key: str = "ranked_indices") -> List[int]:
    """Parse LLM response to extract a list of integers from JSON.

    Args:
        response: The LLM response string that may contain JSON.
        key: The key to extract from the JSON object.

    Returns:
        List of integers from the specified key, or empty list if parsing fails.
    """
    try:
        # Try to extract JSON blocks from markdown code fences
        json_pattern = r"```json\s*([\s\S]*?)\s*```"
        json_blocks = re.findall(json_pattern, response)

        if json_blocks:
            parsed = json.loads(json_blocks[0])
            if isinstance(parsed, dict) and key in parsed:
                return parsed[key]
            elif isinstance(parsed, list):
                return parsed

        # Fallback: try to extract numbers from text
        numbers = re.findall(r"\b\d+\b", response)
        return [int(num) for num in numbers if int(num) < 100]  # Reasonable upper bound

    except Exception as e:
        logger.error("Error parsing list response for key '%s': %s", key, e)
        return []


def parse_json_field(response: str, key: str) -> Optional[str]:
    """Parse JSON response to extract a specific string field.

    Args:
        response: The response string that may contain JSON.
        key: The key to extract from the JSON object.

    Returns:
        The value associated with the key, or None if parsing fails.
    """
    try:
        # Try to extract JSON blocks from markdown code fences
        json_pattern = r"```json\s*([\s\S]*?)\s*```"
        json_blocks = re.findall(json_pattern, response)

        if json_blocks:
            parsed = json.loads(json_blocks[0])
            if isinstance(parsed, dict) and key in parsed:
                return parsed[key]

        # Fallback: try to parse the entire response as JSON
        parsed = json.loads(response)
        if isinstance(parsed, dict) and key in parsed:
            return parsed[key]

    except json.JSONDecodeError:
        logger.warning("Failed to parse JSON response for key '%s'", key)
        return None

    return None
