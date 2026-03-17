# -*- coding: UTF-8 -*-
"""Utility functions for ACE operations."""

import json
import re
from typing import Any, Dict
from openjiuwen.core.common.logging import context_engine_logger as logger


def _safe_json_loads(text: str) -> Dict[str, Any]:
    """Safely load JSON from string with error handling.
    
    Args:
        text: String containing JSON data
        
    Returns:
        Parsed JSON as dictionary
        
    Raises:
        ValueError: If JSON parsing fails
    """
    try:
        # Try direct parsing first
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to extract JSON from markdown code blocks
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass
        
        # Try to find any JSON object in the text
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass
        
        logger.error("Failed to parse JSON from text: %s...", text[:200])
        raise ValueError("Could not parse valid JSON from response") from None
