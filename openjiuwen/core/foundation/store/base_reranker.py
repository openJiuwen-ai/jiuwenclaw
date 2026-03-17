# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Reranker Model Abstract Base Class & Configuration

Provides a unified interface for reranker models.
"""

from abc import ABC, abstractmethod
from typing import Any
import uuid

from pydantic import BaseModel, Field


class RerankerConfig(BaseModel):
    """Reranker model configuration"""

    api_key: str = Field(default="")
    api_base: str = Field(min_length=1)
    model_name: str = Field(default="", alias="model")
    timeout: float = Field(default=10, gt=0)
    temperature: float = Field(default=0.95)
    top_p: float = Field(default=0.1)
    yes_no_ids: tuple[int, int] = Field(default=None, description='Token ids for "yes" and "no"')
    extra_body: dict = Field(default_factory=dict, description="special keyword arguments to pass in")


class Document(BaseModel):
    """Document data model"""

    id_: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Document ID")
    text: str = Field(..., description="Document text content")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Document metadata")


class Reranker(ABC):
    """Reranker model abstract base class"""

    @abstractmethod
    async def rerank(
        self, query: str, doc: list[str | Document], instruct: bool | str = True, **kwargs
    ) -> dict[str, float]:
        """
        Rerank documents and return a mapping from document to relevance score
            query: query string
            doc: list of documents to rerank
            instruct: whether to provide instruction to reranker, pass in a string for custom instruction
            **kwargs: extra arguments
        """

    @abstractmethod
    def rerank_sync(
        self, query: str, doc: list[str | Document], instruct: bool | str = True, **kwargs
    ) -> dict[str, float]:
        """
        Rerank documents and return a mapping from document to relevance score
            query: query string
            doc: list of documents to rerank
            instruct: whether to provide instruction to reranker, pass in a string for custom instruction
            **kwargs: extra arguments
        """

    def _request_headers(self, **kwargs: dict) -> dict:
        ...

    def _request_params(self, **kwargs: dict) -> dict:
        ...

    def _parse_response(self, response_data: dict, doc: list[str | Document]) -> dict[str, float]:
        ...
