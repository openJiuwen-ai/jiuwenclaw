# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import time
from typing import (
    Dict,
    List,
    Optional,
)

from openjiuwen.core.foundation.store.base_kv_store import (
    BaseKVStore,
    BasedKVStorePipeline,
)


class InMemoryKVStore(BaseKVStore):
    def __init__(self):
        self._store: dict[str, tuple[str | bytes, Optional[int]]] = {}  # (value, expiry_timestamp or None)
        self._lock = asyncio.Lock()

    async def set(self, key: str, value: str | bytes):
        """Set a key-value pair."""
        async with self._lock:
            self._store[key] = (value, None)

    async def get(self, key: str) -> str | bytes | None:
        """Get value by key. Returns None if key doesn't exist or is expired."""
        async with self._lock:
            return await self._get_without_lock(key)

    async def delete(self, key: str):
        """Delete a key."""
        async with self._lock:
            if key in self._store:
                del self._store[key]

    async def exclusive_set(
            self, key: str, value: str | bytes, expiry: int | None = None
    ) -> bool:
        """
        Set the key only if it does NOT already exist (even if expired).
        However, if the key exists but is expired, we ALLOW setting it.
        """
        async with self._lock:
            current_time = time.time()
            if key in self._store:
                _, expiry_ts = self._store[key]
                if expiry_ts is not None and current_time > expiry_ts:
                    # Expired: allow to overwrite
                    pass
                else:
                    # Not expired: reject
                    return False

            # Either not present or expired → set it
            expiry_ts = int(current_time + expiry) if expiry is not None else None
            self._store[key] = (value, expiry_ts)
            return True

    async def exists(self, key: str) -> bool:
        """Check if key exists and is not expired."""
        return await self.get(key) is not None

    async def get_by_prefix(self, prefix: str) -> Dict[str, str | bytes]:
        async with self._lock:
            return {
                k: await self._get_without_lock(k)
                for k in self._store.keys()
                if k.startswith(prefix)
            }

    async def delete_by_prefix(self, prefix: str, batch_size: Optional[int] = None) -> None:
        """
        Remove all key-value pairs whose keys start with the given prefix.

        Args:
            prefix (str): The string prefix to match against existing keys.
            batch_size (Optional[int]): Optional batch size for deletion. If None or <= 0,
                all matching keys are deleted in a single operation. Default is None.
        """
        async with self._lock:
            to_del = [k for k in self._store if k.startswith(prefix)]
            if batch_size is None or batch_size <= 0:
                # Delete all at once
                for k in to_del:
                    del self._store[k]
            else:
                # Delete in batches
                for i in range(0, len(to_del), batch_size):
                    batch = to_del[i:i + batch_size]
                    for k in batch:
                        del self._store[k]

    async def mget(self, keys: List[str]) -> List[str | bytes | None]:
        async with self._lock:
            return [await self._get_without_lock(k) for k in keys]

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
        if not keys:
            return 0
        async with self._lock:
            deleted = 0
            if batch_size is None or batch_size <= 0:
                # Delete all at once
                for key in keys:
                    if key in self._store:
                        del self._store[key]
                        deleted += 1
            else:
                # Delete in batches
                for i in range(0, len(keys), batch_size):
                    batch = keys[i:i + batch_size]
                    for key in batch:
                        if key in self._store:
                            del self._store[key]
                            deleted += 1
            return deleted

    def pipeline(self):
        """Create a pipeline-like interface for batch operations."""

        async def execute(operations):
            results = []
            async with self._lock:
                for op in operations:
                    op_type = op[0]
                    if op_type == 'set':
                        self._store[op[1]] = (op[2], None)
                        results.append(None)
                    elif op_type == 'get':
                        results.append(
                            await self._get_without_lock(op[1])
                        )
                    elif op_type == 'exists':
                        value = await self._get_without_lock(op[1])
                        results.append(value is not None)
            return results

        return BasedKVStorePipeline(execute)

    async def _get_without_lock(self, key: str) -> str | bytes | None:
        if key not in self._store:
            return None
        value, expiry_ts = self._store[key]
        if expiry_ts is not None and time.time() > expiry_ts:
            # Note: we do NOT auto-delete expired keys to allow re-set later
            return None
        return value
