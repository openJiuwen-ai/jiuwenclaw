# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Any, Iterable, List, Tuple, Union, Optional


def create_nested_dict(path: str, value: Any = None, separator: str = '.') -> dict:
    """Create a nested dictionary from a dotted path string.

    Args:
        path: Dotted path (for example "a.b.c"). If falsy, return the value itself.
        value: Value to place at the leaf.
        separator: Path separator (default '.')

    Returns:
        A nested dictionary representing the path with the value at the final key.

    Examples:
        >>> create_nested_dict('a.b', 1)
        {'a': {'b': 1}}
    """
    if not path:
        return value

    keys = path.split(separator)
    result: dict = {}
    current = result

    for i, key in enumerate(keys):
        if i == len(keys) - 1:
            current[key] = value
        else:
            current[key] = {}
            current = current[key]

    return result


def flatten_dict(data: dict[str, Any]):
    nodes = extract_leaf_nodes(data)
    result = {format_path(node[0]): node[1] for node in nodes}
    return result


def extract_leaf_nodes(data: Any, current_path: Optional[List[str]] = None) -> List[Tuple[List[str], Any]]:
    """Extract all leaf nodes from a nested structure of dicts/lists.

    This function walks nested dictionaries and lists and returns a list
    of tuples where each tuple contains the path (as a list of keys/indexes)
    and the leaf value.

    Args:
        data: The nested data structure (dicts/lists/values).
        current_path: Internal use only. The current traversal path.

    Returns:
        A list of (path, value) tuples. Paths are represented as lists of
        strings; list indices are formatted as "[index]".

    Example:
        >>> extract_leaf_nodes({'a': [1, {'b': 2}]})
        [(['a', '[0]'], 1), (['a', '[1]', 'b'], 2)]
    """
    if not data:
        return []
    if current_path is None:
        current_path = []

    results: List[Tuple[List[str], Any]] = []

    # If dict, iterate keys and recurse
    if isinstance(data, dict):
        for key, value in data.items():
            new_path = current_path + [key]
            results.extend(extract_leaf_nodes(value, new_path))

    # If list, handle each element and include the index in path
    elif isinstance(data, list):
        for index, item in enumerate(data):
            new_path = current_path + [f"[{index}]"]
            results.extend(extract_leaf_nodes(item, new_path))

    # Otherwise it's a leaf node
    else:
        results.append((current_path, data))

    return results


def format_path(path: Iterable[str]) -> str:
    """Format a path list into a dotted string representation.

    List indices (like "[0]") are appended directly. Dictionary keys are
    joined with a dot separator.

    Args:
        path: Iterable of path elements (strings).

    Returns:
        A formatted path string.
    """
    path_str = ""
    for key in path:
        # Use equality comparison for empty string checks
        if path_str == "" or key.startswith('['):
            path_str += key
        else:
            path_str += f".{key}"
    return path_str


def rebuild_dict_from_paths(path_value_pairs: Iterable[Tuple[List[str], Any]]) -> dict:
    """Rebuild a nested dict from (path, value) pairs.

    This function assumes paths are lists of keys (no list-index handling).

    Args:
        path_value_pairs: Iterable of (path, value) tuples where path is a
            list of keys.

    Returns:
        A nested dictionary reconstructed from paths.
    """
    result: dict = {}

    for path, value in path_value_pairs:
        current = result

        # Traverse the path except the last key
        for key in path[:-1]:
            # Create a dict if missing and move deeper
            if key not in current:
                current[key] = {}
            current = current[key]

        # Set the final value
        last_key = path[-1]
        current[last_key] = value

    return result


def rebuild_dict(path_value_pairs: Iterable[Tuple[List[str], Any]]) -> Any:
    """Rebuild a nested structure (dicts/lists) from path-value pairs.

    This function supports list-index path elements formatted as "[index]".
    It attempts to create lists when an index element appears in the path.

    Args:
        path_value_pairs: Iterable of (path, value) tuples. Path elements are
            strings; list indices must be formatted as "[index]".

    Returns:
        The reconstructed nested structure (usually a dict).
    """
    result: Any = {}

    for path, value in path_value_pairs:
        current = result

        for i, key in enumerate(path[:-1]):
            # Handle list index elements like "[0]"
            if isinstance(key, str) and key.startswith('[') and key.endswith(']'):
                index = int(key[1:-1])
                # Ensure current level is a list
                if not isinstance(current, list):
                    # If current is an empty placeholder, replace with a list
                    # Note: if current was a dict with existing keys this
                    # transformation may be ambiguous; we handle the common
                    # reconstruction case.
                    current_parent_ref = current
                    current = []
                # Expand list to required length
                while len(current) <= index:
                    current.append({})
                current = current[index]
            else:
                # Handle dictionary keys; if missing create dict or list
                if key not in current:
                    next_key = path[i + 1]
                    if (isinstance(next_key, str) and
                            next_key.startswith('[') and next_key.endswith(']')):
                        current[key] = []
                    else:
                        current[key] = {}
                current = current[key]

        # Set the final value, handling possible list index
        last_key = path[-1]
        if isinstance(last_key, str) and last_key.startswith('[') and last_key.endswith(']'):
            index = int(last_key[1:-1])
            if not isinstance(current, list):
                current = []
            while len(current) <= index:
                current.append(None)
            current[index] = value
        else:
            current[last_key] = value

    return result
