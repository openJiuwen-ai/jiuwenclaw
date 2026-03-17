# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

# Core classes
from openjiuwen.core.foundation.llm.model import Model, init_model
from openjiuwen.core.foundation.llm.model_clients.base_model_client import BaseModelClient
from openjiuwen.core.foundation.llm.output_parsers.output_parser import BaseOutputParser

# Configuration
from openjiuwen.core.foundation.llm.schema.config import ModelRequestConfig, ModelClientConfig, ProviderType
from openjiuwen.core.foundation.llm.schema.mode_info import BaseModelInfo, ModelConfig
# Messages
from openjiuwen.core.foundation.llm.schema.message import (
    BaseMessage,
    AssistantMessage,
    UserMessage,
    SystemMessage,
    ToolMessage,
    UsageMetadata
)
from openjiuwen.core.foundation.llm.schema.message_chunk import (
    AssistantMessageChunk,
)

# Tools
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall

# Built-in implementations
from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient
from openjiuwen.core.foundation.llm.output_parsers.json_output_parser import JsonOutputParser
from openjiuwen.core.foundation.llm.output_parsers.markdown_output_parser import MarkdownOutputParser


# ============ Public API exports ============

# Core classes
_CORE_CLASSES = [
    "Model",
    "init_model",
    "BaseModelClient",
    "BaseOutputParser",
]

# Configuration classes
_CONFIG_CLASSES = [
    "ModelRequestConfig",
    "ModelClientConfig",
    "ProviderType",
    "BaseModelInfo",
    "ModelConfig"
]

# Message classes
_MESSAGE_CLASSES = [
    "BaseMessage",
    "AssistantMessage",
    "UserMessage",
    "SystemMessage",
    "ToolMessage",
    "UsageMetadata",
]

# Streaming message classes
_MESSAGE_CHUNK_CLASSES = [
    "AssistantMessageChunk",
]

# Tool-related classes
_TOOL_CLASSES = [
    "ToolCall",
]

# Built-in ModelClient implementations
_PREBUILT_MODEL_CLIENTS = [
    "OpenAIModelClient",
]

# Built-in OutputParser implementations
_PREBUILT_OUTPUT_PARSERS = [
    "JsonOutputParser",
    "MarkdownOutputParser",
]

# Combine all public APIs
__all__ = (
    _CORE_CLASSES
    + _CONFIG_CLASSES
    + _MESSAGE_CLASSES
    + _MESSAGE_CHUNK_CLASSES
    + _TOOL_CLASSES
    + _PREBUILT_MODEL_CLIENTS
    + _PREBUILT_OUTPUT_PARSERS
)
