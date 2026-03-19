# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import re
import os
from typing import List, Dict, Any, Optional, Union

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ValidationError
from openjiuwen.core.common.security.json_utils import JsonUtils
from openjiuwen.core.common.logging import LogManager
from openjiuwen.dev_tools.agent_builder.utils.constants import JSON_EXTRACT_PATTERN

logger = LogManager.get_logger("agent_builder")


def extract_json_from_text(text: str) -> str:
    """
    Extract JSON content from text

    Supports extracting JSON from Markdown code blocks (e.g., ```json ... ```).

    Args:
        text: Text containing JSON

    Returns:
        Extracted JSON string, or original text if not found

    Example:
        ```python
        text = "```json\n{\"key\": \"value\"}\n```"
        json_str = extract_json_from_text(text)  # Returns '{"key": "value"}'
        ```
    """
    if not text:
        return text

    matches = re.findall(JSON_EXTRACT_PATTERN, text)
    if matches:
        return matches[0]
    return text


def format_dialog_history(
        dialog_history: List[Dict[str, str]],
        separator: str = "\n"
) -> str:
    """
    Format dialog history to string

    Args:
        dialog_history: Dialog history list, each element contains 'role' and 'content' keys
        separator: Separator between messages, defaults to newline

    Returns:
        Formatted string in "role: content" format

    Example:
        ```python
        history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi! How can I help you?"}
        ]
        formatted = format_dialog_history(history)
        # Returns: "user: Hello\nassistant: Hi! How can I help you?"
        ```
    """
    if not dialog_history:
        return ""

    return separator.join(
        f"{msg.get('role', 'unknown')}: {msg.get('content', '')}"
        for msg in dialog_history
    )


def safe_json_loads(text: str, default: Any = None) -> Optional[Any]:
    """
    Safely parse JSON string

    Returns default value instead of raising exception on parse failure.

    Args:
        text: JSON string
        default: Default value when parsing fails

    Returns:
        Parsed object, or default on failure

    Example:
        ```python
        result = safe_json_loads('{"key": "value"}')  # Returns dict
        result = safe_json_loads('invalid json', {})  # Returns {}
        ```
    """
    if not text:
        return default

    return JsonUtils.safe_json_loads(text, default=default)


def validate_session_id(session_id: str) -> bool:
    """
    Validate session ID format

    Session ID can only contain letters, numbers, underscores and hyphens.

    Args:
        session_id: Session ID

    Returns:
        Whether valid

    Example:
        ```python
        validate_session_id("session_123")  # True
        validate_session_id("session@123")  # False
        ```
    """
    if not session_id:
        return False

    pattern = r'^[a-zA-Z0-9_-]+$'
    return bool(re.match(pattern, session_id))


def merge_dict_lists(
        existing: List[Dict[str, Any]],
        new_items: List[Dict[str, Any]],
        unique_key: str = "resource_id"
) -> List[Dict[str, Any]]:
    """
    Merge dict lists, removing duplicates

    Determines duplicates based on specified unique key, keeps first occurrence.

    Args:
        existing: Existing list
        new_items: New items list
        unique_key: Key for determining uniqueness

    Returns:
        Merged list without duplicates

    Example:
        ```python
        existing = [{"id": "1", "name": "A"}, {"id": "2", "name": "B"}]
        new_items = [{"id": "2", "name": "B2"}, {"id": "3", "name": "C"}]
        result = merge_dict_lists(existing, new_items, "id")
        # Returns: [{"id": "1", "name": "A"}, {"id": "2", "name": "B"}, {"id": "3", "name": "C"}]
        ```
    """
    if not new_items:
        return existing

    existing_keys = {
        item[unique_key] for item in existing
        if unique_key in item and item[unique_key]
    }

    for item in new_items:
        key_value = item.get(unique_key)
        if key_value and key_value not in existing_keys:
            existing.append(item)
            existing_keys.add(key_value)

    return existing


def deep_merge_dict(
        base: Dict[str, Any],
        update: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Deep merge dictionaries

    Recursively merges nested dicts, update dict overwrites same keys in base dict.

    Args:
        base: Base dictionary
        update: Update dictionary

    Returns:
        Merged new dictionary (does not modify original)

    Example:
        ```python
        base = {"a": 1, "b": {"c": 2, "d": 3}}
        update = {"b": {"c": 4}, "e": 5}
        result = deep_merge_dict(base, update)
        # Returns: {"a": 1, "b": {"c": 4, "d": 3}, "e": 5}
        ```
    """
    result = base.copy()

    for key, value in update.items():
        if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
        ):
            result[key] = deep_merge_dict(result[key], value)
        else:
            result[key] = value

    return result


def load_json_file(file_path: str) -> Dict[str, Any]:
    """
    Load JSON file

    Args:
        file_path: JSON file path

    Returns:
        Parsed dict data, returns empty dict if file is empty

    Raises:
        FileNotFoundError: When file does not exist
        ValidationError: When JSON parsing error or other read error

    Example:
        ```python
        config = load_json_file("config.json")
        ```
    """
    if not os.path.exists(file_path):
        error_msg = f"File not found: {file_path}"
        logger.error("File not found", file_path=file_path)
        raise FileNotFoundError(error_msg)

    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            raw = file.read()
            if not raw or raw.strip() == "":
                logger.warning("JSON file is empty", file_path=file_path)
                return {}
            data = JsonUtils.safe_json_loads(raw, default={})
            if data is None:
                return {}
            if not isinstance(data, dict):
                raise ValueError(f"JSON top level must be object/dict, got: {type(data)}")
            return data
    except Exception as e:
        error_msg = f"JSON parse error: {str(e)}"
        logger.error("JSON parse failed", file_path=file_path, error=str(e))
        raise ValidationError(
            StatusCode.CONTEXT_MESSAGE_INVALID,
            msg=error_msg,
            details={"file_path": file_path, "error": str(e)},
            cause=e,
        ) from e
