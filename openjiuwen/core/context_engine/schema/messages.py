# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Any, Dict
from pydantic import Field, BaseModel

from openjiuwen.core.foundation.llm import (
    ToolMessage,
    UserMessage,
    AssistantMessage,
    SystemMessage,
)


class OffloadMixin(BaseModel):
    """
    A message representing content that has been offloaded from the active context.

    This message type acts as a lightweight placeholder (hint) for content that
    was moved to external storage to manage token budget. It preserves essential
    metadata for retrieval while occupying minimal space in the LLM's context window.

    The actual content is stored externally and can be fully restored using the
    `offload_handle` with an appropriate reloader tool when needed.

    Attributes:
        offload_type: Storage backend type. Either "memory" for in-memory cache
            (e.g., Redis, LRU dict) or "filesystem" for persistent file storage.
        offload_handle: Unique identifier for retrieving the offloaded content.
            UUID string for memory storage, or absolute/relative file path for
            filesystem storage.
        metadata: Additional context about the offloaded content, such as
            original token count, timestamp, content type, or compression info.

        {'role': 'assistant', 'content': '[[HANDLE:abc123]]', 'offload_id': '/tmp/conv_001/turn_42.json'}
    """

    offload_type: str = Field(...)
    offload_handle: str = Field(...)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class OffloadUserMessage(UserMessage, OffloadMixin):
    pass


class OffloadAssistantMessage(AssistantMessage, OffloadMixin):
    pass


class OffloadSystemMessage(SystemMessage, OffloadMixin):
    pass


class OffloadToolMessage(ToolMessage, OffloadMixin):
    pass


def create_offload_message(
        role: str,
        content: str,
        offload_handle: str,
        offload_type: str,
        **kwargs
):
    kwargs.pop("role", None)
    kwargs.pop("content", None)
    if role == "assistant":
        return OffloadAssistantMessage(
            content=content,
            offload_handle=offload_handle,
            offload_type=offload_type,
            **kwargs
        )
    elif role == "tool":
        return OffloadToolMessage(
            content=content,
            offload_handle=offload_handle,
            offload_type=offload_type,
            **kwargs
        )
    elif role == "system":
        return OffloadSystemMessage(
            content=content,
            offload_handle=offload_handle,
            offload_type=offload_type,
            **kwargs
        )
    else:
        return OffloadUserMessage(
            content=content,
            offload_handle=offload_handle,
            offload_type=offload_type,
            **kwargs
        )