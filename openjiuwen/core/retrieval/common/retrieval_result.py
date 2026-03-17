# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Retrieval Result Data Models

Contains SearchResult and RetrievalResult data models.
"""

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class SearchResult(BaseModel):
    """Search result data model"""

    id: str = Field(..., description="Result ID")
    text: str = Field(..., description="Text content")
    score: float = Field(..., description="Relevance score")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Metadata")


class RetrievalResult(BaseModel):
    """Retrieval result data model"""

    text: str = Field(..., description="Text content")
    score: float = Field(..., description="Relevance score")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Metadata")
    doc_id: Optional[str] = Field(None, description="Document ID")
    chunk_id: Optional[str] = Field(None, description="Chunk ID")


class MultiKBRetrievalResult(BaseModel):
    """Multi KB Retrieval result data model"""

    text: str = Field(..., description="Text content")
    score: float = Field(..., description="Relevance score")
    raw_score: float = Field(..., description="Raw relevance score")
    raw_score_scaled: float = Field(..., description="Scaled raw relevance score between 0 and 1")
    kb_ids: list = Field(default_factory=list, description="List of knowledge base IDs where this result was found")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Metadata")
