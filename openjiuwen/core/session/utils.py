# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import inspect
import re
from dataclasses import dataclass
from typing import Optional, Any, Union

REGEX_MAX_LENGTH = 1000
NESTED_PATH_LIST_PATTERN = re.compile(r'^([\w]+)((?:\[\d+\])*)$')
NESTED_PATH_SPLIT = '.'
NESTED_PATH_LIST_SPLIT = "["


def create_wrapper_class(original_obj, wrapper_name="WrappedObject"):
    """dynamic generate wrapped class instance for instance"""
    class WrapperClass:
        def __init__(self, wrapped_obj):
            self._wrapped = wrapped_obj

        def get_wrapped(self):
            """Return the wrapped object."""
            return self._wrapped

        def __getattr__(self, name):
            return getattr(self.get_wrapped(), name)

    for attr_name in dir(original_obj):
        if not attr_name.startswith('_'):
            attr_value = getattr(original_obj, attr_name)
            if callable(attr_value):
                # Check if the method is an async generator function
                if inspect.isasyncgenfunction(attr_value):
                    def create_async_gen_method(method_name):
                        async def wrapped_method(self, *args, **kwargs):
                            method = getattr(self.get_wrapped(), method_name)
                            async for item in method(*args, **kwargs):
                                yield item
                        return wrapped_method
                    setattr(WrapperClass, attr_name, create_async_gen_method(attr_name))
                # Check if the method is a coroutine function (async but not generator)
                elif inspect.iscoroutinefunction(attr_value):
                    def create_async_method(method_name):
                        async def wrapped_method(self, *args, **kwargs):
                            method = getattr(self.get_wrapped(), method_name)
                            return await method(*args, **kwargs)
                        return wrapped_method
                    setattr(WrapperClass, attr_name, create_async_method(attr_name))
                # Regular synchronous method
                else:
                    def create_method(method_name):
                        def wrapped_method(self, *args, **kwargs):
                            method = getattr(self.get_wrapped(), method_name)
                            return method(*args, **kwargs)
                        return wrapped_method
                    setattr(WrapperClass, attr_name, create_method(attr_name))
    WrapperClass.__name__ = wrapper_name
    WrapperClass.__qualname__ = wrapper_name

    return WrapperClass(original_obj)


def update_dict(update: dict, source: dict, ignore_delete: bool = False) -> None:
    """
    update source dict by update dict
    Note: source is unnested structure, update is nested structure

    :param update: update dict, which key is nested
    :param source: source dict, which key must not be nested
    """
    removed = []
    for key, value in update.items():
        current_key, current = root_to_path(key, source, create_if_absent=True)
        if value is None and not ignore_delete:
            removed.append((current_key, current))
        else:
            update_by_key(current_key, value, current)
    if not ignore_delete:
        for key, value in removed:
            delete_by_key(key, value)


def get_by_schema(schema: Union[str, list, dict], data: dict, nested_path: str = None, is_root: bool = True) -> Any:
    if nested_path is not None and len(nested_path) > 0:
        data = get_value_by_nested_path(nested_path, data)
    if schema is None or data is None:
        return None
    if isinstance(schema, str):
        origin_key = extract_origin_key(schema)
        if origin_key == schema and not is_root:
            return schema
        return get_value_by_nested_path(origin_key, data)
    elif isinstance(schema, dict):
        result = {}
        for target_key, target_schema in schema.items():
            if isinstance(target_schema, list) or isinstance(target_schema, dict) or is_ref_path(target_schema):
                result[target_key] = get_by_schema(target_schema, data, is_root=False)
            else:
                result[target_key] = target_schema
        return result
    elif isinstance(schema, list):
        result = []
        for item in schema:
            result.append(get_by_schema(item, data, is_root=False))
        return result
    else:
        return schema


def get_value_by_nested_path(nested_key: str, source: dict) -> Optional[Any]:
    result = root_to_path(nested_key, source)
    if result[1] is None:
        return None
    container, key = result[1], result[0]
    try:
        if isinstance(container, list):
            index = int(key)
            if index < 0:
                if abs(index) <= len(container):
                    return container[index]
                else:
                    return None
            else:
                if index < len(container):
                    return container[index]
                else:
                    return None
        elif hasattr(container, '__getitem__'):
            return container[key]

        else:
            return None

    except (ValueError, TypeError, KeyError, IndexError):
        return None


def split_nested_path(nested_key: str) -> list:
    '''
    Split nested path
    :param nested_key: path
    :return: e.g. a_1.b.c[1].d -> ["a_1", "b", "c", 1, "d"]
             a.b[0]['key'] -> ["a", "b", 0, "key"]
    '''
    if not isinstance(nested_key, str):
        return []
    if ((NESTED_PATH_SPLIT not in nested_key) and (NESTED_PATH_LIST_SPLIT not in nested_key)
            and ("['" not in nested_key)):
        return []
    final_list = []
    params = nested_key.split(NESTED_PATH_SPLIT)
    for param in params:
        if '[' in param:
            base_part = param.split('[')[0]
            if base_part:
                final_list.append(base_part)

            indexes = re.findall(r'\[(-?\d+)\]|\[\'([^\']+)\'\]', param)
            for idx_tuple in indexes:
                if idx_tuple[0]:
                    final_list.append(int(idx_tuple[0]))
                elif idx_tuple[1]:
                    final_list.append(idx_tuple[1])
        else:
            final_list.append(param)
    return final_list


def is_ref_path(path: str) -> bool:
    return isinstance(path, str) and len(path) > 3 and path.startswith("${") and path.endswith("}")


def extract_origin_key(key: str) -> str:
    """
    extract the origin key from given key if the given key is reference structure
    e.g. "${start123.p2}" -> "start123.p2"
    :param key: reference key
    :return: origin key
    """
    if not isinstance(key, str):
        return key
    if '$' not in key:
        return key
    pattern = re.compile(r"\${([^{}]*)}")
    match = pattern.search(key, endpos=REGEX_MAX_LENGTH)
    if match:
        return match.group(1)
    return key


def delete_by_key(key: Union[str, int], source: dict) -> None:
    if key not in source:
        return
    if isinstance(key, int):
        return
    del source[key]


def update_by_key(key: Union[str, int], new_value: Any, source: dict) -> None:
    if key not in source:
        source[key] = expand_nested_structure(new_value)
        return
    if isinstance(source[key], dict) and isinstance(new_value, dict):
        update_dict(new_value, source[key], ignore_delete=False)
    else:
        source[key] = expand_nested_structure(new_value)


def expand_nested_structure(data: Any) -> Any:
    if isinstance(data, list) or isinstance(data, tuple):
        result = []
        for item in data:
            result.append(expand_nested_structure(item))
        return result
    elif isinstance(data, dict):
        result = {}
        for key, value in data.items():
            current_key, current = root_to_path(key, result, create_if_absent=True)
            current[current_key] = expand_nested_structure(value)
        return result
    else:
        return data


def root_to_path(nested_path: str, source: dict, create_if_absent: bool = False) -> tuple[Union[str, int], dict]:
    paths = split_nested_path(nested_path)
    if len(paths) == 0:
        return (nested_path, source)
    current = source
    for i, path in enumerate(paths):
        is_last = (i == len(paths) - 1)
        if isinstance(path, str):
            if isinstance(current, dict):
                if path not in current:
                    if not create_if_absent:
                        return (None, None)
                    if not is_last and i + 1 < len(paths) and isinstance(paths[i + 1], int):
                        current[path] = []
                    else:
                        current[path] = {}

                if is_last:
                    return (path, current)

                if not create_if_absent and current[path] is None:
                    return (None, None)

                current = current[path]
            else:
                return (None, None)

        elif isinstance(path, int):
            if isinstance(current, list):
                if path >= len(current):
                    if not create_if_absent:
                        return (None, None)
                    while len(current) <= path:
                        current.append(None)

                if is_last:
                    return (path, current)
                if not create_if_absent and current[path] is None:
                    return (None, None)
                current = current[path]
            else:
                return (None, None)
    return (None, None)


def _safe_extend_container(container: list, target_index: int, is_final_index: bool = False) -> bool:
    if not isinstance(container, list):
        return False

    if target_index < 0 or target_index > 10000:
        return False

    current_length = len(container)
    if target_index < current_length:
        return True  # No extension needed

    expansion_needed = target_index - current_length + 1
    if expansion_needed > 10000:
        return False

    try:
        # Fill intermediate positions with None
        if target_index > current_length:
            container += [None] * (target_index - current_length)

        # Append the appropriate value at target position
        if is_final_index:
            container.append({})  # Final position gets empty dict
        else:
            container.append([])  # Intermediate position gets empty list

        return True
    except (MemoryError, TypeError):
        return False


def root_to_index(indexes: list[int], source: Union[list[Any], tuple[Any]], create_if_absent: bool = False):
    """
    Navigates through a nested list/tuple structure using a path of indexes.

    This function traverses a hierarchical data structure following the given index path.
    If create_if_absent is True, it will automatically create missing list elements
    (with None placeholders) to reach the target location. Tuples are immutable and
    cannot be modified.

    Returns:
        tuple: (index, container) - The final index and its container
    """
    # Input validation
    if not isinstance(indexes, list):
        raise TypeError("indexes must be a list")
    if not all(isinstance(idx, int) for idx in indexes):
        raise TypeError("all elements in indexes must be integers")
    # Check source type, but don't raise error immediately - just return None, None
    if source is not None and not isinstance(source, (list, tuple)):
        return None, None
    if source is None or not indexes:
        return None, None
    if len(indexes) > 10:
        raise ValueError('Nesting level too deep, level limit is 10')

    current = source

    # Process intermediate indexes
    if len(indexes) > 1:
        for idx in indexes[:-1]:
            # Handle negative indexes first
            if idx < 0:
                adjusted_idx = idx + len(current)
                if adjusted_idx < 0:  # Negative index remains out of bounds after adjustment
                    return None, None
            else:
                adjusted_idx = idx
                if adjusted_idx > 10000:
                    raise ValueError('Index must be between [0,10000]')

            # Check bounds (using adjusted index)
            if adjusted_idx >= len(current):
                if not create_if_absent or isinstance(current, tuple):
                    return None, None
                # Use safe extension method for intermediate index (append [])
                if not _safe_extend_container(current, adjusted_idx, is_final_index=False):
                    return None, None

            # Safe access
            try:
                current = current[adjusted_idx]
            except (IndexError, TypeError):
                return None, None

            if current is not None and not isinstance(current, (list, tuple)):
                return None, None

    # Process final index
    if not isinstance(current, (list, tuple)):
        return None, None

    # Handle negative index for final index
    final_idx = indexes[-1]
    if final_idx < 0:
        adjusted_final_idx = final_idx + len(current)
        if adjusted_final_idx < 0:
            return None, None
    else:
        adjusted_final_idx = final_idx
        if adjusted_final_idx > 10000:
            raise ValueError('Index must be between [0,10000]')

    # Check final index bounds
    if adjusted_final_idx >= len(current):
        if not create_if_absent or isinstance(current, tuple):
            return None, None
        # Use safe extension method for final index (append {})
        if not _safe_extend_container(current, adjusted_final_idx, is_final_index=True):
            return None, None

    # Final validation: ensure adjusted_final_idx is valid after potential extension
    if adjusted_final_idx < 0 or (adjusted_final_idx >= len(current) and isinstance(current, tuple)):
        return None, None

    # Return adjusted index (handles negative index conversion)
    return adjusted_final_idx, current


@dataclass
class EndFrame:
    source: str


Frame = Union[Any, EndFrame]
