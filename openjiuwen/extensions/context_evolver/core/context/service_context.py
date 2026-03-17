# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Service context for managing shared services (LLM, embeddings, vector store)."""

from typing import Any, Dict, Optional
from openjiuwen.core.common import logging
# Use the project's lazy `logger` from openjiuwen.core.common.logging
logger = logging.logger


class ServiceContext:
    """Singleton context for managing shared services.

    This provides access to LLM, embedding model, and vector store
    instances that are shared across all operations.
    """

    _instance: Optional["ServiceContext"] = None

    def __new__(cls):
        """Ensure singleton pattern."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """Initialize service context (only once)."""
        if self._initialized:
            return

        self._services: Dict[str, Any] = {}
        self._initialized = True
        logger.info("ServiceContext initialized")

    def register_service(self, name: str, service: Any) -> None:
        """Register a service.

        Args:
            name: Service name (e.g., 'llm', 'embedding_model', 'vector_store')
            service: Service instance
        """
        self._services[name] = service
        logger.info("Registered service: %s", name)

    def get_service(self, name: str) -> Optional[Any]:
        """Get a registered service.

        Args:
            name: Service name

        Returns:
            Service instance or None if not found
        """
        return self._services.get(name)

    @property
    def llm(self) -> Optional[Any]:
        """Get LLM service."""
        return self.get_service("llm")

    @property
    def embedding_model(self) -> Optional[Any]:
        """Get embedding model service."""
        return self.get_service("embedding_model")

    @property
    def vector_store(self) -> Optional[Any]:
        """Get vector store service."""
        return self.get_service("vector_store")

    def clear(self) -> None:
        """Clear all registered services."""
        self._services.clear()
        logger.info("ServiceContext cleared")

    def __repr__(self) -> str:
        """String representation."""
        return f"ServiceContext(services={list(self._services.keys())})"
