# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
from typing import List, Dict

from openjiuwen.core.foundation.llm import BaseMessage, AssistantMessage
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.context_engine.token.base import TokenCounter


class TiktokenCounter(TokenCounter):
    """
    A fast and exact token counter powered by tiktoken.
    Supports all publicly released OpenAI models (gpt-3.5-turbo, gpt-4, gpt-4o, ...).
    Thread-safe: tiktoken.Encoding objects are stateless and reusable.
    """

    # Mapping from user-friendly model names to tiktoken encoding names
    _MODEL2ENC = {
        "gpt-3.5-turbo": "cl100k_base",
        "gpt-4": "cl100k_base",
        "gpt-4-turbo": "cl100k_base",
        "gpt-4o": "o200k_base",
        "gpt-4o-mini": "o200k_base",
        "text-embedding-ada-002": "cl100k_base",
        "text-embedding-3-small": "cl100k_base",
        "text-embedding-3-large": "cl100k_base",
    }

    __slots__ = ("_enc", "_model")

    def __init__(self, model: str = "gpt-4") -> None:
        import tiktoken

        self._model = model
        enc_name = self._MODEL2ENC.get(model, "cl100k_base")
        self._enc = tiktoken.get_encoding(enc_name)

    # ------------------------------------------------------------------
    # Core interfaces
    # ------------------------------------------------------------------
    def count(self, text: str, *, model: str = "", **kwargs) -> int:
        try:
            return len(self._enc.encode(text, disallowed_special=()))
        except Exception:
            return len(text) // 4

    def count_messages(self, messages: List[BaseMessage], *, model: str = "", **kwargs) -> int:
        if not messages:
            return 0
        total = 0
        for msg in messages:
            piece = f"<|start|>{msg.role}\n{msg.content}<|end|>"
            total += self.count(piece, model=model, **kwargs)
            if isinstance(msg, AssistantMessage):
                dict_msg = msg.model_dump()
                # count tool calls
                tool_calls = dict_msg.get("tool_calls")
                if tool_calls:
                    total += self.count(json.dumps(dict_msg["tool_calls"], ensure_ascii=False), model=model, **kwargs)
        return total + 3

    def count_tools(self, tools: List[ToolInfo], *, model: str = "", **kwargs) -> int:
        if not tools:
            return 0
        total = 0
        for idx, tool in enumerate(tools):
            function_obj = {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.parameters  # 期望是 JSON Schema dict
            }
            json_str = json.dumps(function_obj, ensure_ascii=False, separators=(",", ":"))

            # message format：functions.{name}:{index}
            piece = f"<|start|>functions.{tool.name}:{idx}\n{json_str}<|end|>"
            total += self.count(piece)

        # Consistent with count_messages, reserve 3 tokens for the assistant
        return total + 3