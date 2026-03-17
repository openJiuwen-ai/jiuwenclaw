# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Union, List, Optional, Any
from pydantic import BaseModel, model_validator

from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall


class UsageMetadata(BaseModel):
    code: int = 0
    err_msg: str = ""
    prompt: str = ""
    task_id: str = ""
    model_name: str = ""
    total_latency: float = 0.
    first_token_time: str = ""
    request_start_time: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_tokens: int = 0


class BaseMessage(BaseModel):
    role: str
    content: Union[str, List[Union[str, dict]]] = ""
    name: Optional[str] = None


class AssistantMessage(BaseMessage):
    role: str = "assistant"
    tool_calls: Optional[List[ToolCall]] = None
    usage_metadata: Optional[UsageMetadata] = None
    finish_reason: str = "null"
    parser_content: Optional[Any] = None
    reasoning_content: Optional[str] = None

    @model_validator(mode='before')
    @classmethod
    def convert_openai_tool_calls_format(cls, data: Any) -> Any:
        """Convert OpenAI API format tool_calls to flat ToolCall format.

        OpenAI API format has nested 'function' object:
        {"id": "xxx", "type": "function", "function": {"name": "...", "arguments": "..."}}

        ToolCall model expects flat format:
        {"id": "xxx", "type": "function", "name": "...", "arguments": "..."}
        """
        if isinstance(data, dict) and 'tool_calls' in data and data['tool_calls']:
            converted_tool_calls = []
            for tc in data['tool_calls']:
                if isinstance(tc, dict) and 'function' in tc and isinstance(tc['function'], dict):
                    # OpenAI format - convert to flat format
                    converted_tc = {
                        'id': tc.get('id'),
                        'type': tc.get('type', 'function'),
                        'name': tc['function'].get('name', ''),
                        'arguments': tc['function'].get('arguments', ''),
                        'index': tc.get('index')
                    }
                    converted_tool_calls.append(converted_tc)
                else:
                    # Already flat format or ToolCall instance
                    converted_tool_calls.append(tc)
            data['tool_calls'] = converted_tool_calls
        return data

    def model_dump(self, **kwargs) -> dict[str, Any]:
        result = {
            "role": self.role,
            "content": self.content,
        }
        if self.tool_calls:
            tool_calls = []
            for call in self.tool_calls:
                tool_calls.append({
                    "id": call.id,
                    "type": call.type,
                    "function": {
                        "name": call.name,
                        "arguments": call.arguments
                    }
                })
            result["tool_calls"] = tool_calls
        if self.usage_metadata is not None:
            result["usage_metadata"] = self.usage_metadata.model_dump(**kwargs)
        if self.finish_reason is not None:
            result["finish_reason"] = self.finish_reason
        if self.parser_content is not None:
            result["parser_content"] = self.parser_content
        if self.reasoning_content is not None:
            result["reasoning_content"] = self.reasoning_content
        return result


class UserMessage(BaseMessage):
    role: str = "user"

class SystemMessage(BaseMessage):
    role: str = "system"


class ToolMessage(BaseMessage):
    role: str = "tool"
    tool_call_id: str
