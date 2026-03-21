# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Any

from pydantic import ConfigDict

from openjiuwen.core.foundation.llm.schema.message import BaseMessage, AssistantMessage, ToolMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall


def merge_parser_content(left: Any, right: Any) -> Any:
    """
    Intelligently merge parser_content fields.
    
    Merge strategy:
    - If right is empty, return left
    - If left is empty, return right
    - If both are strings, concatenate them
    - If both are lists, merge (concatenate) them
    - If both are dicts, recursively merge the dicts
    - If object implements __add__ method, use + operator
    - If it's a Pydantic Model, recursively merge fields
    - Otherwise, return right (keep the latest value)
    """
    if right is None:
        return left
    if left is None:
        return right
    
    # String concatenation
    if isinstance(left, str) and isinstance(right, str):
        return left + right
    
    # List concatenation
    if isinstance(left, list) and isinstance(right, list):
        return left + right
    
    # Dictionary recursive merge
    if isinstance(left, dict) and isinstance(right, dict):
        return merge_dicts(left, right)
    
    # Handle custom objects: check if __add__ method is implemented
    if (hasattr(left, '__add__') and 
        type(left) == type(right) and
        not isinstance(left, (int, float, bool))):  # Exclude basic numeric types
        try:
            return left + right
        except (TypeError, NotImplementedError):
            pass
    
    # Handle Pydantic Model objects
    if hasattr(left, 'model_fields') and hasattr(right, 'model_fields'):
        try:
            return merge_pydantic_models(left, right)
        except Exception:
            pass
    
    # Otherwise, keep the latest value
    return right


def merge_dicts(left: dict, right: dict) -> dict:
    """
    Recursively merge two dictionaries.
    
    For the same key:
    - If both values are strings, concatenate them
    - If both values are lists, concatenate them
    - If both values are dicts, recursively merge them
    - Otherwise, use the value from the right side
    """
    result = left.copy()
    
    for key, right_value in right.items():
        if key in result:
            left_value = result[key]
            
            # Recursively handle same types
            if isinstance(left_value, str) and isinstance(right_value, str):
                result[key] = left_value + right_value
            elif isinstance(left_value, list) and isinstance(right_value, list):
                result[key] = left_value + right_value
            elif isinstance(left_value, dict) and isinstance(right_value, dict):
                result[key] = merge_dicts(left_value, right_value)
            else:
                # For different types or other types, use the new value from the right side
                result[key] = right_value
        else:
            result[key] = right_value
    
    return result


def merge_pydantic_models(left: Any, right: Any) -> Any:
    """
    Merge two Pydantic Model instances.
    
    Strategy:
    - Iterate through all fields
    - For string fields, concatenate them
    - For list fields, concatenate them
    - For dict fields, recursively merge them
    - For nested Pydantic Models, recursively merge them
    - For other fields, use the non-empty value from the right side
    """
    if type(left) != type(right):
        return right
    
    # Get all fields of the model
    merged_data = {}
    
    # First get all field values from the left side
    left_dict = left.model_dump() if hasattr(left, 'model_dump') else left.dict()
    right_dict = right.model_dump() if hasattr(right, 'model_dump') else right.dict()
    
    for field_name in left_dict.keys() | right_dict.keys():
        left_value = left_dict.get(field_name)
        right_value = right_dict.get(field_name)
        
        # Use the common merge logic
        merged_data[field_name] = merge_parser_content(left_value, right_value)
    
    # Create new model instance
    try:
        return type(left)(**merged_data)
    except Exception:
        # If creation fails, return right side
        return right


class BaseMessageChunk(BaseMessage):
    model_config = ConfigDict(arbitrary_types_allowed=True, json_encoders={type(None): lambda _: None})


    def __add__(self, other: "BaseMessageChunk") -> "BaseMessageChunk":
        if not isinstance(other, BaseMessageChunk):
            raise TypeError(f"Cannot add {self.__class__.__name__} to {type(other)}")

        if isinstance(self.content, str) and isinstance(other.content, str):
            combined_content = self.content + other.content
        elif isinstance(self.content, list) and isinstance(other.content, list):
            combined_content = self.content + other.content
        else:
            combined_content = other.content


        return self.__class__(role=self.role, content=combined_content, name=self.name or other.name)


class AssistantMessageChunk(AssistantMessage, BaseMessageChunk):
    def __add__(self, other: Any) -> "AssistantMessageChunk":
        if not isinstance(other, AssistantMessageChunk):
            raise TypeError(f"Cannot add AssistantMessageChunk to {type(other)}")

        # Handle content merging based on type (consistent with BaseMessageChunk)
        if isinstance(self.content, str) and isinstance(other.content, str):
            combined_content = self.content + other.content
        elif isinstance(self.content, list) and isinstance(other.content, list):
            combined_content = self.content + other.content
        else:
            combined_content = other.content

        # merge tool_calls by concatenating fragments of the same call instead of appending new elements
        merged_tool_calls = []
        if self.tool_calls:
            for tc in self.tool_calls:
                merged_tool_calls.append(ToolCall(
                    id=tc.id,
                    type=tc.type,
                    name=tc.name,
                    arguments=tc.arguments,
                    index=tc.index
                ))

        if other.tool_calls:
            for incoming in other.tool_calls:
                if merged_tool_calls:
                    last = merged_tool_calls[-1]
                    same_id = (last.id and incoming.id and last.id == incoming.id) or (not last.id or not incoming.id)
                    if (same_id and hasattr(last, 'type') and last.type == 'function'
                            and hasattr(incoming, 'type') and incoming.type == 'function'):
                        merged_tool_calls[-1] = ToolCall(
                            id=last.id or incoming.id,
                            type=last.type or incoming.type,
                            name=(last.name or "") + (incoming.name or ""),
                            arguments=(last.arguments or "") + (incoming.arguments or ""),
                            index=last.index
                        )
                        continue
                # otherwise, push as a new tool_call
                merged_tool_calls.append(ToolCall(
                    id=incoming.id,
                    type=incoming.type,
                    name=incoming.name,
                    arguments=incoming.arguments,
                    index=incoming.index
                ))

        merged_finish_reason = other.finish_reason if other.finish_reason != "null" else self.finish_reason

        return AssistantMessageChunk(
            role=self.role,
            content=combined_content,
            tool_calls=merged_tool_calls if merged_tool_calls else None,
            usage_metadata=other.usage_metadata or self.usage_metadata,
            finish_reason=merged_finish_reason,
            parser_content=other.parser_content or self.parser_content,
            reasoning_content=(self.reasoning_content or '') + (other.reasoning_content or '')
        )


class ToolMessageChunk(ToolMessage, BaseMessageChunk):
    def __add__(self, other: Any) -> "ToolMessageChunk":
        if not isinstance(other, ToolMessageChunk):
            raise TypeError(f"Cannot add ToolMessageChunk to {type(other)}")

        return ToolMessageChunk(
            role="tool",
            content=(self.content or "") + (other.content or ""),
            tool_call_id=other.tool_call_id or self.tool_call_id
        )
