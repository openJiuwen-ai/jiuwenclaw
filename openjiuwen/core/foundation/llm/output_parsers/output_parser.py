# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import AsyncIterator, Any
from abc import ABC, abstractmethod


class BaseOutputParser(ABC):
    """Base class for parsing LLM output into desired format."""
    
    @abstractmethod
    async def parse(self, inputs) -> Any:
        """Async parse LLM output.
        
        Args:
            inputs: AssistantMessage or its content string
            
        Returns:
            Parsed resultqing
        """
        raise NotImplementedError()
    
    @abstractmethod
    async def stream_parse(self, streaming_inputs: AsyncIterator) -> AsyncIterator[Any]:
        """Async parse streaming LLM output.
        
        Args:
            streaming_inputs: AsyncIterator[AssistantMessageChunk]
            
        Yields:
            Parsed result fragments
        """
        raise NotImplementedError()
