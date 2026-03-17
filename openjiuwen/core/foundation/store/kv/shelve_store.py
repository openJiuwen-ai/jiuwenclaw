# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Shelve-based key-value store implementation.

This module provides a key-value store implementation that uses Python's shelve
module for local file-based persistent storage.
"""

import asyncio
import shelve
from pathlib import Path
from typing import (
    List,
    Optional,
)

from openjiuwen.core.foundation.store.base_kv_store import (
    BaseKVStore,
    BasedKVStorePipeline,
)


class ShelveStore(BaseKVStore):
    """
    Shelve-based key-value store implementation.
    
    This implementation uses Python's shelve module for local file-based persistent storage.
    All operations are asynchronous and use thread pool to execute synchronous shelve operations.
    """

    def __init__(self, db_path: str):
        """
        Initialize ShelveStore with a shelve database path.

        Args:
            db_path (str): Path to the shelve database file.
        """
        self._db_path = db_path
        # Ensure parent directory exists
        db_path_obj = Path(db_path)
        db_path_obj.parent.mkdir(parents=True, exist_ok=True)

    def _get_db(self) -> shelve.Shelf:
        """Get or create shelve database connection."""
        return shelve.open(self._db_path, flag='c', writeback=True)

    async def _run_in_thread(self, func, *args, **kwargs):
        """Run a synchronous function in a thread pool."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

    async def set(self, key: str, value: str | bytes):
        """Store or overwrite a key-value pair."""

        def _set():
            with self._get_db() as db:
                db[key] = value
                db.sync()

        await self._run_in_thread(_set)

    async def exclusive_set(
            self, key: str, value: str | bytes, expiry: int | None = None
    ) -> bool:
        """Atomically set a key-value pair only if the key does not already exist."""

        def _exclusive_set():
            with self._get_db() as db:
                if key in db:
                    return False
                db[key] = value
                db.sync()
                return True

        return await self._run_in_thread(_exclusive_set)

    async def get(self, key: str) -> str | bytes | None:
        """Retrieve the value associated with the given key."""

        def _get():
            with self._get_db() as db:
                return db.get(key)

        return await self._run_in_thread(_get)

    async def exists(self, key: str) -> bool:
        """Check whether a key exists in the store."""

        def _exists():
            with self._get_db() as db:
                return key in db

        return await self._run_in_thread(_exists)

    async def delete(self, key: str):
        """Remove the specified key from the store."""

        def _delete():
            with self._get_db() as db:
                if key in db:
                    del db[key]
                    db.sync()

        await self._run_in_thread(_delete)

    async def get_by_prefix(self, prefix: str) -> dict[str, str | bytes]:
        """Retrieve all key-value pairs whose keys start with the given prefix."""

        def _get_by_prefix():
            with self._get_db() as db:
                result = {}
                for key in db.keys():
                    if key.startswith(prefix):
                        result[key] = db[key]
                return result

        return await self._run_in_thread(_get_by_prefix)

    async def delete_by_prefix(
            self, prefix: str, batch_size: Optional[int] = None
    ):
        """
        Remove all key-value pairs whose keys start with the given prefix.

        Args:
            prefix (str): The string prefix to match against existing keys.
            batch_size (Optional[int]): Optional batch size for deletion. If None or <= 0,
                all matching keys are deleted in a single operation. Default is None.
        """

        def _delete_by_prefix():
            with self._get_db() as db:
                keys_to_delete = [
                    key for key in db.keys() if key.startswith(prefix)
                ]
                if batch_size is None or batch_size <= 0:
                    # Delete all at once
                    for key in keys_to_delete:
                        del db[key]
                else:
                    # Delete in batches
                    for i in range(0, len(keys_to_delete), batch_size):
                        batch = keys_to_delete[i:i + batch_size]
                        for key in batch:
                            del db[key]
                db.sync()

        await self._run_in_thread(_delete_by_prefix)

    async def mget(self, keys: List[str]) -> List[str | bytes | None]:
        """Bulk-retrieve values for multiple keys in a single operation."""

        def _mget():
            with self._get_db() as db:
                return [db.get(key) for key in keys]

        return await self._run_in_thread(_mget)

    async def batch_delete(self, keys: List[str], batch_size: Optional[int] = None) -> int:
        """
        Delete a batch of keys.

        Args:
            keys (List[str]): List of keys to delete.
            batch_size (Optional[int]): Optional batch size for deletion. If None or <= 0,
                all keys are deleted in a single operation. Default is None.

        Returns:
            int: Number of keys actually deleted.
        """

        def _batch_delete():
            with self._get_db() as db:
                deleted = 0
                if batch_size is None or batch_size <= 0:
                    # Delete all at once
                    for key in keys:
                        if key in db:
                            del db[key]
                            deleted += 1
                else:
                    # Delete in batches
                    for i in range(0, len(keys), batch_size):
                        batch = keys[i:i + batch_size]
                        for key in batch:
                            if key in db:
                                del db[key]
                                deleted += 1
                db.sync()
                return deleted

        return await self._run_in_thread(_batch_delete)

    def pipeline(self):
        """Create a pipeline-like interface for batch operations."""

        # Shelve doesn't have a pipeline, but we can return a mock object
        # that collects operations and executes them in batch
        async def execute(operations):
            def _execute():
                results = []
                with self._get_db() as db:
                    for op in operations:
                        if op[0] == 'set':
                            db[op[1]] = op[2]
                        elif op[0] == 'get':
                            results.append(db.get(op[1]))
                        elif op[0] == 'exists':
                            results.append(op[1] in db)
                    db.sync()
                return results

            return await self._run_in_thread(_execute)

        return BasedKVStorePipeline(execute)
