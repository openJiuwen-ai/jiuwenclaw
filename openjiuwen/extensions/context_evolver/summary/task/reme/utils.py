# -*- coding: UTF-8 -*-
import json
import re
from typing import List

from openjiuwen.core.common.logging import context_engine_logger as logger


def _is_valid_experience(data: dict) -> bool:
    """Check if data has required experience fields."""
    if not isinstance(data, dict):
        return False
    has_experience = "experience" in data
    has_trigger = "when_to_use" in data or "condition" in data
    return has_experience and has_trigger


def parse_json_experience_response(response: str) -> List[dict]:
    """Parse JSON formatted experience response"""
    try:
        # Extract JSON blocks
        json_pattern = r"```json\s*([\s\S]*?)\s*```"
        json_blocks = re.findall(json_pattern, response)

        if json_blocks:
            parsed = json.loads(json_blocks[0])

            # Handle array format
            if isinstance(parsed, list):
                experiences = [
                    exp_data for exp_data in parsed
                    if _is_valid_experience(exp_data)
                ]
                return experiences

            # Handle single object
            elif _is_valid_experience(parsed):
                return [parsed]

        # Fallback: try to parse entire response
        parsed = json.loads(response)
        if isinstance(parsed, list):
            return parsed
        elif isinstance(parsed, dict):
            return [parsed]

    except json.JSONDecodeError as e:
        logger.warning("Failed to parse JSON experience response: %s", e)

    return []


def calculate_cosine_similarity(embedding1: List[float], embedding2: List[float]) -> float:
    """Calculate cosine similarity between two embeddings.

    Args:
        embedding1: First embedding vector
        embedding2: Second embedding vector

    Returns:
        Cosine similarity score between 0 and 1
    """
    try:
        import numpy as np

        vec1 = np.array(embedding1)
        vec2 = np.array(embedding2)

        # Calculate cosine similarity
        dot_product = np.dot(vec1, vec2)
        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2)

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return float(dot_product / (norm1 * norm2))

    except Exception as e:
        logger.error("Error calculating cosine similarity: %s", e)
        return 0.0
