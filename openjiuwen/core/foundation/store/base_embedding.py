# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Embedding Model Abstract Base Class & Configuration

Provides a unified interface for embedding models.
"""

from abc import ABC, abstractmethod
import asyncio
from typing import List, Optional

from pydantic import BaseModel, Field


class EmbeddingConfig(BaseModel):
    """Embedding model configuration"""

    model_name: str = Field(..., description="Model name")
    base_url: str = Field(..., description="API Base URL")
    api_key: Optional[str] = Field(None, description="API Key")


class Embedding(ABC):
    """Embedding model abstract base class"""

    limiter: asyncio.Semaphore

    @abstractmethod
    async def embed_query(self, text: str, **kwargs) -> List[float]:
        """Embed query text"""

    @abstractmethod
    async def embed_documents(
        self,
        texts: List[str],
        batch_size: Optional[int] = None,
        **kwargs,
    ) -> List[List[float]]:
        """Embed document texts"""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Return embedding dimension"""
