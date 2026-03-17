# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from abc import ABC, abstractmethod
from typing import List

from openjiuwen.core.foundation.llm import BaseMessage
from openjiuwen.core.foundation.tool import ToolInfo


class TokenCounter(ABC):
    """
    Abstract base class for unified token counting.
    A concrete implementation only needs to override `count`;
    `count_messages` can be reused or overridden as required.
    """

    @abstractmethod
    def count(self, text: str, *, model: str = "", **kwargs) -> int:
        """
        Count tokens in a single text.

        Args:
            text: The input text to tokenize.
            model: The model name that determines the tokenization rule.

        Returns:
            The number of tokens in `text`.
        """

    @abstractmethod
    def count_messages(self, messages: List[BaseMessage], *, model: str = "", **kwargs) -> int:
        """
        Count tokens for a list of chat messages.
        The default convention is OpenAI-style: <|start|>{role}\n{content}<|end|>.

        Args:
            messages: List of message objects (with role/content).
            model: The model name that determines the tokenization rule.

        Returns:
            The total estimated token count for `messages`.
        """

    @abstractmethod
    def count_tools(self, tools: List[ToolInfo], *, model: str = "", **kwargs) -> int:
        """
        Count the number of tokens that a list of tool-calling metadata will consume.

        Args:
            tools: List of ToolInfo objects describing the tools to be injected
                   into the prompt.
            model: The target model name, which determines the tokenization rule.

        Returns:
            int: Total tokens required to represent the tools in the prompt.
        """