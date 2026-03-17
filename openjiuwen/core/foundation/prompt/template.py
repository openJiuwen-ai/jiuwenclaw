# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import copy
from typing import Union, List

from pydantic import BaseModel, Field

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.llm import BaseMessage, UserMessage
from openjiuwen.core.foundation.prompt.assemble.assembler import PromptAssembler


class PromptTemplate(BaseModel):
    """
    Interpolatable text prompt template with configurable placeholders.
    Supports both string and BaseMessage list as content,
    and provides to_messages() and format() methods for placeholder replacement.

    Attributes
    ----------
    name : str
        Template name
    content : str | List[BaseMessage]
        Template content (string or message list).
    placeholder_prefix : str
        Left delimiter for placeholders (default "{{").
    placeholder_suffix : str
        Right delimiter for placeholders (default "}}").
    """
    name: str = Field(default="")
    content: Union[str, List[BaseMessage]] = Field(default="")
    placeholder_prefix: str = Field(default="{{")
    placeholder_suffix: str = Field(default="}}")


    def to_messages(self) -> List[BaseMessage]:
        """
        Converts the prompt template content (string or BaseMessage list) to a list of BaseMessage objects.
        If content is a string, it is wrapped as a single UserMessage; if it is already a list,
        the list is returned as-is.
        """
        if not self.content:
            return []

        if isinstance(self.content, str):
            return [UserMessage(content=self.content)]

        if not all(isinstance(msg, BaseMessage) for msg in self.content):
            raise build_error(
                StatusCode.PROMPT_TEMPLATE_INVALID,
                error_msg="prompt template type must be in str or list[BaseMessage]"
            )

        return [copy.deepcopy(msg) for msg in self.content]


    def format(self, keywords: dict = None) -> "PromptTemplate":
        """
        Replaces all placeholders in the prompt template content with the provided keywords
        and returns a new PromptTemplate instance with the interpolated content.
        Placeholders are identified by the configured prefix and suffix.
        If keywords is None or empty, the original prompt template is returned unchanged.
        """
        if not keywords:
            return copy.deepcopy(self)
        assembler = PromptAssembler(
            prompt_template_content=copy.deepcopy(self.content),
            placeholder_prefix=self.placeholder_prefix,
            placeholder_suffix=self.placeholder_suffix
        )
        input_keys = assembler.input_keys
        valid_keywords = dict([(key, keywords[key]) for key in input_keys if key in keywords])
        content = assembler.prompt_assemble(**valid_keywords)
        return PromptTemplate(
            name=self.name,
            content=content,
            placeholder_prefix=self.placeholder_prefix,
            placeholder_suffix=self.placeholder_suffix
        )