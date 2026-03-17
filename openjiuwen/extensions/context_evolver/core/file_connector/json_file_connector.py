# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Generic JSON file connector for persistence."""

import json
from pathlib import Path
from typing import Any, Dict
from openjiuwen.core.common.logging import context_engine_logger as logger


class JSONFileConnector:
    """Generic connector for saving and loading JSON data to/from files.

    This connector handles file I/O operations for JSON data. It's agnostic
    to the data structure - callers are responsible for serialization/deserialization
    of their objects to/from dictionaries.

    Example:
        # Basic usage
        connector = JSONFileConnector()
        data = {"key": "value", "items": [1, 2, 3]}
        connector.save_to_file("output.json", data)
        loaded = connector.load_from_file("output.json")

        # With custom formatting
        connector = JSONFileConnector(indent=4, ensure_ascii=True)
        connector.save_to_file("formatted.json", data)
    """

    def __init__(self, indent: int = 2, ensure_ascii: bool = False):
        """Initialize the JSON file connector.

        Args:
            indent: Number of spaces for JSON indentation (default: 2)
            ensure_ascii: If True, escape non-ASCII characters (default: False)
        """
        self.indent = indent
        self.ensure_ascii = ensure_ascii

    def save_to_file(self, file_path: str, data: Dict[str, Any]) -> None:
        """Save dictionary data to a JSON file.

        Creates parent directories if they don't exist. Overwrites existing files.

        Args:
            file_path: Path to save the JSON file
            data: Dictionary data to save

        Raises:
            Exception: If file writing fails
        """
        try:
            # Create directory if it doesn't exist
            Path(file_path).parent.mkdir(parents=True, exist_ok=True)

            # Save to JSON file
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=self.indent, ensure_ascii=self.ensure_ascii)

            logger.info("Saved data to %s (%s top-level keys)", file_path, len(data))
        except Exception as e:
            logger.error("Failed to save data to %s: %s", file_path, e)
            raise

    @staticmethod
    def load_from_file(file_path: str) -> Dict[str, Any]:
        """Load dictionary data from a JSON file.

        Args:
            file_path: Path to the JSON file

        Returns:
            Dictionary data loaded from the file

        Raises:
            Exception: If file reading or JSON parsing fails
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            logger.info("Loaded data from %s (%s top-level keys)", file_path, len(data))
            return data
        except Exception as e:
            logger.error("Failed to load data from %s: %s", file_path, e)
            raise

    @staticmethod
    def exists(file_path: str) -> bool:
        """Check if a file exists.

        Args:
            file_path: Path to check

        Returns:
            True if file exists, False otherwise
        """
        return Path(file_path).exists()

    @staticmethod
    def delete(file_path: str) -> bool:
        """Delete a file if it exists.

        Args:
            file_path: Path to the file to delete

        Returns:
            True if file was deleted, False if it didn't exist

        Raises:
            Exception: If deletion fails
        """
        try:
            path = Path(file_path)
            if path.exists():
                path.unlink()
                logger.info("Deleted file: %s", file_path)
                return True
            return False
        except Exception as e:
            logger.error("Failed to delete %s: %s", file_path, e)
            raise


def safe_model_dump(obj: Any) -> Dict[str, Any]:
    """Safely serialize a Pydantic model to dictionary.

    This helper function handles compatibility across Pydantic versions:
    - Pydantic v2: uses model_dump()
    - Pydantic v1: uses dict()

    Args:
        obj: Pydantic model instance or any object with to_dict/dict/model_dump

    Returns:
        Dictionary representation of the object

    Raises:
        AttributeError: If object has no serialization method
    """
    # Try Pydantic v2 first
    if hasattr(obj, 'model_dump'):
        try:
            return obj.model_dump()
        except Exception as e:
            logger.debug("model_dump() failed, trying fallback methods: %s", e)

    # Try custom to_dict method
    if hasattr(obj, 'to_dict'):
        return obj.to_dict()

    # Try Pydantic v1 or standard dict
    if hasattr(obj, 'dict'):
        return obj.dict()

    raise AttributeError(
        f"Object of type {type(obj).__name__} has no serialization method "
        "(model_dump, to_dict, or dict)"
    )
