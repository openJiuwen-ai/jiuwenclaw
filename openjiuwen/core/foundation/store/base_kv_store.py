# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from abc import (
    ABC,
    abstractmethod,
)
from typing import (
    Any,
    Callable,
    List,
    Optional,
)


class BaseKVStore(ABC):
    """
    Abstract base class defining a unified interface for a key-value storage.
    """

    @abstractmethod
    async def set(self, key: str, value: str | bytes):
        """
        Store or overwrite a key-value pair.

        Args:
            key (str): The unique string identifier for the entry.
            value (str | bytes): The string or bytes payload to associate with the key.
        """
        pass

    @abstractmethod
    async def exclusive_set(
            self, key: str, value: str | bytes, expiry: int | None = None
    ) -> bool:
        """
        Atomically set a key-value pair only if the key does not already exist.
        Args:
            key (str): the string key to set.
            value (str | bytes): The string or bytes value to associate with the key.
            expiry (int | None): Optional expiry time for the key-value pair.
        Returns:
            bool: True if the key-value pair was successfully set, False if the key already existed.
        """
        pass

    @abstractmethod
    async def get(self, key: str) -> str | bytes | None:
        """
        Retrieve the value associated with the given key.

        Args:
            key (str): The string key to look up.

        Returns:
            str | bytes | None: The stored string or bytes value, or None if the key is absent.
        """
        pass

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """
        Check whether a key exists in the store.

        Args:
            key (str): The string key to check.

        Returns:
            bool: True if the key exists, False otherwise.
        """
        pass

    @abstractmethod
    async def delete(self, key: str):
        """
        Remove the specified key from the store.

        Args:
            key (str): The string key to delete. No action is taken if the key does not exist.
        """
        pass

    @abstractmethod
    async def get_by_prefix(self, prefix: str) -> dict[str, str | bytes]:
        """
        Retrieve all key-value pairs whose keys start with the given prefix.

        Args:
            prefix (str): The string prefix to match against existing keys.

        Returns:
            dict[str, str | bytes]: A dictionary mapping every matching key to its corresponding value.
        """
        pass

    @abstractmethod
    async def delete_by_prefix(self, prefix: str, batch_size: Optional[int] = None):
        """
        Remove all key-value pairs whose keys start with the given prefix.

        Args:
            prefix (str): The string prefix to match against existing keys.
            batch_size (Optional[int]): Optional batch size for deletion. If None or <= 0,
                all matching keys are deleted in a single operation. Default is None.
        """
        pass

    @abstractmethod
    async def mget(self, keys: List[str]) -> List[str | bytes | None]:
        """
        Bulk-retrieve values for multiple keys in a single operation.

        Args:
            keys (List[str]): An list of string keys to fetch.

        Returns:
            List[str | bytes | None]: A list of string or bytes values (or None)
                in the same order as the input ``keys``.
        """
        pass

    @abstractmethod
    async def batch_delete(self, keys: List[str], batch_size: Optional[int] = None) -> int:
        """
        Delete a batch of keys in a single operation.

        Args:
            keys (List[str]): A list of keys to delete.
            batch_size (Optional[int]): Optional batch size for deletion. If None or <= 0,
                all keys are deleted in a single operation. Default is None.

        Returns:
            int: The number of keys successfully deleted.
        """
        pass

    @abstractmethod
    def pipeline(self) -> Any:
        """
        Create a pipeline-like interface for batch operations.

        Returns:
            Any: A pipeline object for batch operations. The exact type depends on
                 the implementation (e.g., Redis Pipeline, custom pipeline object).

        Note:
            Pipeline allows batching multiple operations and executing them together,
            reducing network round-trips and improving performance. Use pipeline.set(),
            pipeline.get(), etc. to queue commands, then call pipeline.execute() to
            execute all queued commands.
        """
        pass


class BasedKVStorePipeline:
    """Pipeline for batch operations on DbBasedKVStore."""

    def __init__(self, func: Callable):
        """
        Initialize pipeline with store instance.
        """
        self._func = func
        self._operations = []

    async def set(
            self, key: str, value: str | bytes, ttl: int | None = None
    ):
        """
        Add a set operation to the pipeline.

        Args:
            key (str): The key to set.
            value (str | bytes): The value to set.
            ttl (int | None): Optional TTL (not used in current implementation).
        """
        self._operations.append(('set', key, value))

    async def get(self, key: str):
        """
        Add a get operation to the pipeline.

        Args:
            key (str): The key to get.
        """
        self._operations.append(('get', key))

    async def exists(self, key: str):
        """
        Add an exists operation to the pipeline.

        Args:
            key (str): The key to check.
        """
        self._operations.append(('exists', key))

    async def execute(self):
        """
        Execute all operations in the pipeline in batch.

        Returns:
            List: Results of operations in the order they were added.
        """
        results = await self._func(self._operations)
        self._operations = []
        return results
