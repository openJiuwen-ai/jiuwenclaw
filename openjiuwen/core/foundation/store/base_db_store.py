# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from abc import ABC, abstractmethod
from sqlalchemy.ext.asyncio import AsyncEngine


class BaseDbStore(ABC):
    """
    Abstract base class defining a unified interface for a db storage.
    """

    @abstractmethod
    def get_async_engine(self) -> AsyncEngine:
        """
        Return the asynchronous SQLAlchemy engine，allowing callers to perform async database operations
        such as issuing raw SQL statements or using SQLAlchemy's asyncio extension.

        Returns:
            AsyncEngine: The asynchronous SQLAlchemy engine instance.
        """
        pass
