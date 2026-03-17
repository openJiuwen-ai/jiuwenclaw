# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Processor Abstract Base Class

Base class for all processors (Parser, Chunker, Extractor).
"""

from abc import ABC, abstractmethod
from typing import Any


class Processor(ABC):
    """Processor abstract base class, base class for all processors (Parser, Chunker, Extractor)"""

    @abstractmethod
    async def process(self, *args: Any, **kwargs) -> Any:
        """
        Process data (abstract method, must be implemented by subclasses)

        Args:
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            Processing result
        """
