# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Union, List, Optional, AsyncIterator

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.llm.schema.message import BaseMessage, AssistantMessage
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.foundation.llm.schema.config import ModelRequestConfig, ModelClientConfig
from openjiuwen.core.foundation.llm.output_parsers.output_parser import BaseOutputParser
from openjiuwen.core.foundation.llm.model_clients.inference_affinity_model_client import InferenceAffinityModelClient


class InferenceAffinityModel:
    """InferenceAffinity (vLLM) model unified invocation entry point

    Responsibilities:
    1. Manage InferenceAffinityModelClient instances
    2. Provide unified asynchronous interfaces (ainvoke, astream)
    3. Support release functionality

    Usage:
        model = InferenceAffinityModel(model_config, client_config)
        response = await model.ainvoke("Hello")
    """

    def __init__(
            self,
            model_client_config: Optional[ModelClientConfig],
            model_config: ModelRequestConfig = None,
    ):
        """Initialize Model instance

        Args:
            :param model_config: Model parameter configuration
            :param model_client_config: Client configuration
        """
        self.model_config = model_config
        self.model_client_config = model_client_config
        self._client: Optional[InferenceAffinityModelClient] = None

        if model_client_config is not None:
            self._client = InferenceAffinityModelClient(model_config, model_client_config)
        else:
            raise build_error(StatusCode.MODEL_SERVICE_CONFIG_ERROR,
                              error_msg="model client config is none")

    async def invoke(
            self,
            messages: Union[str, List[BaseMessage], List[dict]],
            *,
            tools: Union[List[ToolInfo], List[dict], None] = None,
            temperature: Optional[float] = None,
            top_p: Optional[float] = None,
            max_tokens: Optional[int] = None,
            stop: Union[Optional[str], None] = None,
            model: str = None,
            output_parser: Optional[BaseOutputParser] = None,
            session_id: str = None,
            enable_cache_sharing: bool = False,
            **kwargs
    ) -> AssistantMessage:
        """Asynchronous LLM invocation

        Args:
            :param messages:
            :param tools:
            :param temperature:
            :param top_p:
            :param max_tokens:
            :param stop:
            :param model:
            :param output_parser:
            :param session_id:
            :param enable_cache_sharing:
            **kwargs: Other parameters

        Returns:
            AssistantMessage
        """
        return await self._client.invoke(
            messages=messages,
            stop=stop,
            model=model,
            tools=tools,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            output_parser=output_parser,
            session_id=session_id,
            enable_cache_sharing=enable_cache_sharing,
            **kwargs
        )

    async def stream(
            self,
            messages: Union[str, List[BaseMessage], List[dict]],
            *,
            tools: Union[List[ToolInfo], List[dict], None] = None,
            temperature: Optional[float] = None,
            top_p: Optional[float] = None,
            max_tokens: Optional[int] = None,
            stop: Union[Optional[str], None] = None,
            model: str = None,
            output_parser: Optional[BaseOutputParser] = None,
            session_id: str = None,
            enable_cache_sharing: bool = False,
            **kwargs
    ) -> AsyncIterator[AssistantMessageChunk]:
        """Asynchronous streaming LLM invocation

        Args:
            :param messages:
            :param tools:
            :param temperature:
            :param top_p:
            :param max_tokens:
            :param stop:
            :param model:
            :param output_parser:
            :param session_id:
            :param enable_cache_sharing:
            **kwargs: Other parameters

        Yields:
            AssistantMessageChunk
        """
        async for chunk in self._client.stream(
                messages=messages,
                stop=stop,
                model=model,
                tools=tools,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
                output_parser=output_parser,
                session_id=session_id,
                enable_cache_sharing=enable_cache_sharing,
                **kwargs
        ):
            yield chunk

    async def release(
            self,
            session_id: str,
            messages: List,
            messages_released_index: int,
            *,
            tools: List = None,
            tools_released_index: Optional[int] = None,
            model: str = None
    ) -> bool:
        """Release model cache

        Args:
            :param session_id: Session_id
            :param messages: Message list
            :param messages_released_index: Message release index
            :param tools: Tool list
            :param tools_released_index: Tool release index
            :param model: Model name

        Returns:
            bool: Whether the release was successful
        """
        return await self._client.release(
            model=model,
            session_id=session_id,
            messages=messages,
            messages_released_index=messages_released_index,
            tools=tools,
            tools_released_index=tools_released_index
        )