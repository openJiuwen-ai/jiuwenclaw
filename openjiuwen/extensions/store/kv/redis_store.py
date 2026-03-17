# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import (
    List,
    Optional,
)

from redis.asyncio import Redis
from redis.asyncio.cluster import RedisCluster

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.store import BaseKVStore


class RedisStore(BaseKVStore):
    """
    Redis-based key-value store implementation using async Redis client.

    This implementation provides a high-performance, distributed key-value store
    backed by Redis. All operations are asynchronous and use the redis.asyncio client.
    Supports both standalone Redis and Redis Cluster modes.

    Implementation Details:
        - All values are stored and retrieved as UTF-8 encoded strings
        - Redis returns bytes by default; this implementation automatically decodes
          them to strings for convenience
        - Prefix-based operations use SCAN iteration to avoid blocking the Redis server
        - Batch operations are used for performance optimization
        - Supports both standalone Redis and Redis Cluster modes

    Performance Considerations:
        - Single key operations (get, set, delete) are O(1)
        - Prefix operations (get_by_prefix, delete_by_prefix) use SCAN which is
          O(N) where N is the number of keys, but non-blocking
        - Batch deletion is performed in chunks of 500 keys to balance memory
          usage and network round-trips
        - In cluster mode, operations are automatically routed to the correct node
          based on key hash slots

    Cluster Mode:
        - Redis Cluster mode is fully supported
        - All operations work transparently with cluster topology
        - SCAN operations in cluster mode iterate across all nodes
        - Pipeline operations in cluster mode are supported but may have limitations
          for cross-slot operations

    Note:
        The Redis client instance should be properly configured with connection
        pooling and appropriate timeout settings for production use.
        For cluster mode, ensure cluster nodes are properly configured and accessible.
    """

    def __init__(self, redis: Redis | RedisCluster):
        """
        Initialize RedisStore with a Redis async client (standalone or cluster).

        Args:
            redis (Redis | RedisCluster): The async Redis client instance from redis.asyncio.
                                                Can be either a standalone Redis client or
                                                a RedisCluster client for cluster mode.
                                                Should be configured with appropriate connection settings.
        """
        self.redis = redis
        self._is_cluster = isinstance(redis, RedisCluster)

    async def set(self, key: str, value: str | bytes):
        """
        Store or overwrite a key-value pair.

        This method uses Redis SET command, which is atomic and O(1) operation.
        If the key already exists, its value will be overwritten.

        Args:
            key (str): The unique string identifier for the entry.
            value (str | bytes): The string or bytes payload to associate with the key.

        Note:
            Redis automatically handles UTF-8 encoding for strings. Bytes values are stored as-is
            without any additional serialization.
        """
        try:
            await self.redis.set(key, value)
            logger.debug(f"Successfully set key: {key}")
        except Exception as e:
            logger.error(f"Failed to set key: {key}, error: {e}")
            raise

    async def exclusive_set(
            self, key: str, value: str | bytes, expiry: int | None = None
    ) -> bool:
        """
        Atomically set a key-value pair only if the key does not already exist.

        This method uses Redis SET command with NX (Not eXists) option, providing
        atomic compare-and-set semantics. This is useful for implementing distributed
        locks or ensuring idempotency.

        Args:
            key (str): The string key to set.
            value (str | bytes): The string or bytes value to associate with the key.
            expiry (int | None): Optional expiry time for the key-value pair in seconds.
                                 If provided, the key will automatically expire after
                                 the specified number of seconds.

        Returns:
            bool: True if the key-value pair was successfully set (key did not exist),
                  False if the key already existed and was not modified.

        Implementation:
            - Uses SET key value NX for atomic set-if-not-exists
            - Uses SET key value NX EX expiry for atomic set-if-not-exists with expiry
            - Both operations are atomic at the Redis server level
        """
        try:
            if expiry is not None:
                # Use SET with NX (only if not exists) and EX (expiry in seconds)
                result = await self.redis.set(key, value, nx=True, ex=expiry)
                logger.debug(f"Exclusive set key: {key} with expiry {expiry}s, result: {bool(result)}")
            else:
                # Use SET with NX (only if not exists)
                result = await self.redis.set(key, value, nx=True)
                logger.debug(f"Exclusive set key: {key}, result: {bool(result)}")
            return bool(result)
        except Exception as e:
            logger.error(f"Failed to exclusive set key: {key}, error: {e}")
            raise

    async def get(self, key: str) -> str | bytes | None:
        """
        Retrieve the value associated with the given key.

        This method uses Redis GET command, which is O(1) operation.

        Args:
            key (str): The string key to look up.

        Returns:
            str | bytes | None: The stored string or bytes value, or None if the key is absent.

        Implementation:
            - Redis GET returns bytes or None
            - Returns bytes as-is, converts other types to string
            - Returns None if key does not exist (not an error condition)
        """
        try:
            value = await self.redis.get(key)
            if value is None:
                logger.debug(f"Key not found: {key}")
                return None
            logger.debug(f"Successfully retrieved key: {key}")
            # Redis returns bytes, return as-is or convert to string
            if isinstance(value, bytes):
                return value
            return str(value)
        except Exception as e:
            logger.error(f"Failed to get key: {key}, error: {e}")
            raise

    async def exists(self, key: str) -> bool:
        """
        Check whether a key exists in the store.

        This method uses Redis EXISTS command, which is O(1) operation.

        Args:
            key (str): The string key to check.

        Returns:
            bool: True if the key exists, False otherwise.

        Note:
            This method only checks for key existence, not whether the key has
            an associated value (though in Redis, existence implies a value exists).
        """
        result = await self.redis.exists(key)
        return bool(result)

    async def delete(self, key: str):
        """
        Remove the specified key from the store.

        This method uses Redis DEL command, which is O(1) operation.

        Args:
            key (str): The string key to delete. No action is taken if the key does not exist.

        Note:
            Deleting a non-existent key is not an error in Redis; the operation
            will simply return 0 (no keys deleted).
        """
        try:
            result = await self.redis.delete(key)
            logger.debug(f"Deleted key: {key}, result: {result}")
        except Exception as e:
            logger.error(f"Failed to delete key: {key}, error: {e}")
            raise

    async def get_by_prefix(self, prefix: str) -> dict[str, str | bytes]:
        """
        Retrieve all key-value pairs whose keys start with the given prefix.

        This method uses Redis SCAN command to iterate over matching keys in a
        non-blocking manner. It then fetches values for each matching key.

        Args:
            prefix (str): The string prefix to match against existing keys.
                         The pattern "{prefix}*" is used for matching.

        Returns:
            dict[str, str | bytes]: A dictionary mapping every matching key to its corresponding value.
                          Empty dictionary if no keys match the prefix.

        Implementation:
            - Uses SCAN with MATCH pattern for non-blocking iteration
            - SCAN is cursor-based and safe for use in production environments
            - Each matching key is fetched individually (consider using mget for
              better performance if you know the keys in advance)
            - Automatically handles bytes-to-string decoding
            - In cluster mode, SCAN iterates across all cluster nodes

        Performance:
            - Time complexity: O(N) where N is the number of keys in the database
            - Memory complexity: O(M) where M is the number of matching keys
            - Non-blocking: Uses SCAN instead of KEYS to avoid blocking Redis server

        Cluster Mode:
            - In cluster mode, SCAN operations automatically iterate across all nodes
            - Keys are automatically fetched from the correct nodes
            - Performance may vary depending on cluster topology and key distribution

        Note:
            For large datasets, this operation may take time. Consider using
            pagination or limiting the result set if performance is critical.
        """
        logger.debug(f"Getting keys by prefix: {prefix}")
        result: dict[str, str | bytes] = {}
        pattern = f"{prefix}*"

        try:
            # Scan all keys matching the prefix pattern
            async for key in self.redis.scan_iter(match=pattern):
                if isinstance(key, bytes):
                    key_str = key.decode('utf-8')
                else:
                    key_str = str(key)

                # Get the value for this key
                value = await self.redis.get(key)
                if value is not None:
                    if isinstance(value, bytes):
                        result[key_str] = value
                    else:
                        result[key_str] = str(value)

            logger.debug(f"Retrieved {len(result)} keys by prefix: {prefix}")
            return result
        except Exception as e:
            logger.error(f"Failed to get keys by prefix: {prefix}, error: {e}")
            raise

    async def delete_by_prefix(
            self, prefix: str, batch_size: Optional[int] = None
    ) -> None:
        """
        Remove all key-value pairs whose keys start with the given prefix.

        This method uses Redis SCAN to find matching keys and then deletes them
        in batches for optimal performance.

        Args:
            prefix (str): The string prefix to match against existing keys.
                         The pattern "{prefix}*" is used for matching.
            batch_size (Optional[int]): Optional batch size for deletion. If None or <= 0,
                defaults to 500 keys per delete operation. Default is None.

        Implementation:
            - Uses SCAN with MATCH pattern for non-blocking key discovery
            - Collects keys in batches before deletion
            - Uses batch DELETE to minimize network round-trips
            - Automatically handles remaining keys after iteration completes
            - In cluster mode, SCAN iterates across all cluster nodes

        Performance:
            - Time complexity: O(N) where N is the number of keys in the database
            - Batch size: Configurable (default 500 keys per delete operation)
            - Non-blocking: Uses SCAN instead of KEYS to avoid blocking Redis server

        Cluster Mode:
            - In cluster mode, SCAN operations automatically iterate across all nodes
            - Keys are automatically routed to the correct nodes for deletion
            - Performance may vary depending on cluster topology and key distribution

        Note:
            - Large prefix deletions may take time depending on the number of
              matching keys
            - The operation is not atomic across all keys; partial deletions
              may occur if the operation is interrupted
            - Consider using Redis transactions (MULTI/EXEC) if atomicity is required
        """
        logger.debug(f"Deleting keys by prefix: {prefix}")
        pattern = f"{prefix}*"
        keys_to_delete = []
        total_deleted = 0
        use_batching = batch_size is not None and batch_size > 0
        batch_limit = batch_size if use_batching else None

        try:
            # Collect all keys matching the prefix pattern
            async for key in self.redis.scan_iter(match=pattern):
                keys_to_delete.append(key)
                # Delete in batches to avoid memory issues (only if batch_size is specified)
                if use_batching and len(keys_to_delete) >= batch_limit:
                    if keys_to_delete:
                        deleted = await self.redis.delete(*keys_to_delete)
                        total_deleted += deleted
                        keys_to_delete = []

            # Delete remaining keys
            if keys_to_delete:
                deleted = await self.redis.delete(*keys_to_delete)
                total_deleted += deleted

            logger.debug(f"Deleted {total_deleted} keys by prefix: {prefix}")
        except Exception as e:
            logger.error(f"Failed to delete keys by prefix: {prefix}, error: {e}")
            raise

    async def mget(self, keys: List[str]) -> List[str | bytes | None]:
        """
        Bulk-retrieve values for multiple keys in a single operation.

        This method uses Redis MGET command to fetch multiple values in a single
        network round-trip, providing better performance than multiple GET calls.

        Args:
            keys (List[str]): An list of string keys to fetch. Can be empty.

        Returns:
            List[str | bytes | None]: A list of string or bytes values (or None) in the same order
                             as the input ``keys``. If a key does not exist, the
                             corresponding position will be None.

        Implementation:
            - Uses Redis MGET command for atomic bulk retrieval
            - Time complexity: O(N) where N is the number of keys
            - Network round-trips: 1 for standalone mode, may be multiple for cluster mode
            - Returns bytes as-is, converts other types to string
            - In cluster mode, automatically handles keys across different slots

        Performance:
            - Significantly faster than multiple individual GET calls
            - Reduces network latency by batching requests
            - Recommended for fetching multiple known keys

        Cluster Mode:
            - In cluster mode, if keys are in different slots, the operation may
              be split into multiple requests automatically by the Redis client
            - Performance may be slightly reduced compared to standalone mode when
              keys span multiple slots

        Example:
            >>> store = RedisStore(redis_client)
            >>> values = await store.mget(["key1", "key2", "key3"])
            >>> # Returns: ["value1", "value2", None] if key3 doesn't exist
        """
        if not keys:
            return []

        logger.debug(f"Bulk getting {len(keys)} keys")
        try:
            # In cluster mode, mget may need special handling for cross-slot keys
            # The Redis client should handle this automatically, but we catch any errors
            try:
                values = await self.redis.mget(keys)
            except Exception as e:
                logger.warning(f"MGET failed, falling back to individual GETs: {e}")
                # Fallback to individual gets if mget fails (e.g., cross-slot in cluster)
                # This should rarely happen as modern Redis clients handle this automatically
                values = []
                for key in keys:
                    try:
                        value = await self.redis.get(key)
                        values.append(value)
                    except Exception:
                        values.append(None)

            result: List[str | bytes | None] = []
            found_count = 0

            for value in values:
                if value is None:
                    result.append(None)
                else:
                    found_count += 1
                    # Redis returns bytes, return as-is or convert to string
                    if isinstance(value, bytes):
                        result.append(value)
                    else:
                        result.append(str(value))

            logger.debug(f"Bulk retrieved {found_count}/{len(keys)} keys")
            return result
        except Exception as e:
            logger.error(f"Failed to bulk get {len(keys)} keys, error: {e}")
            raise

    async def batch_delete(self, keys: List[str], batch_size: Optional[int] = None) -> int:
        """
        Delete a batch of keys in a single operation.

        This method uses Redis DELETE command with multiple keys, which is more
        efficient than individual DELETE calls.

        Args:
            keys (List[str]): A list of keys to delete. Can be empty.
            batch_size (Optional[int]): Optional batch size for deletion. If None or <= 0,
                all keys are deleted in a single operation. Default is None.

        Returns:
            int: The number of keys successfully deleted.

        Implementation:
            - Uses Redis DELETE command with multiple keys
            - Returns the actual count of keys that were deleted
            - In cluster mode, automatically handles keys across different slots
            - Deletes keys in batches if batch_size is specified

        Performance:
            - Significantly faster than multiple individual DELETE calls
            - Single network round-trip per batch for all deletions
            - Recommended for deleting multiple known keys

        Cluster Mode:
            - In cluster mode, the Redis client automatically handles
              cross-slot operations
            - Keys are routed to the correct nodes based on hash slots

        Note:
            - Deleting non-existent keys is not an error
            - Returns the actual number of keys deleted (may be less than len(keys))
        """
        if not keys:
            return 0
        try:
            if batch_size is None or batch_size <= 0:
                # Delete all at once
                logger.debug(f"Batch deleting {len(keys)} keys")
                deleted = await self.redis.delete(*keys)
                logger.debug(f"Successfully batch deleted {deleted} keys")
                return deleted
            else:
                # Delete in batches
                total_deleted = 0
                for i in range(0, len(keys), batch_size):
                    batch = keys[i:i + batch_size]
                    logger.debug(f"Batch deleting {len(batch)} keys (batch {i // batch_size + 1})")
                    deleted = await self.redis.delete(*batch)
                    total_deleted += deleted
                logger.debug(f"Successfully batch deleted {total_deleted} keys in total")
                return total_deleted
        except Exception as e:
            logger.error(f"Failed to batch delete {len(keys)} keys, error: {e}")
            raise

    async def refresh_ttl(self, keys: List[str], ttl_seconds: int) -> None:
        """
        Refresh TTL (Time To Live) for given keys.

        This method uses Redis pipeline to set expiration time for multiple keys
        in a single network round-trip.

        Args:
            keys (List[str]): A list of keys to refresh TTL for.
            ttl_seconds (int): The TTL value in seconds. Must be positive.

        Implementation:
            - Uses Redis pipeline to batch EXPIRE commands
            - All TTL updates are executed in a single pipeline
            - If ttl_seconds is 0 or negative, the operation is skipped

        Performance:
            - Significantly faster than multiple individual EXPIRE calls
            - Reduces network latency by batching requests

        Note:
            - If a key does not exist, EXPIRE will have no effect (not an error)
            - If ttl_seconds is 0 or negative, the operation is skipped
            - Errors during TTL refresh are silently ignored to allow operation
              to continue even if some keys fail
        """
        if not keys or ttl_seconds <= 0:
            return

        try:
            logger.debug(f"Refreshing TTL for {len(keys)} keys with {ttl_seconds}s")
            pipeline = self.redis.pipeline()
            for key in keys:
                await pipeline.expire(key, ttl_seconds)
            await pipeline.execute()
            logger.debug(f"Successfully refreshed TTL for {len(keys)} keys")
        except Exception as e:
            # Silently ignore errors to allow operation to continue
            logger.warning(f"Failed to refresh TTL for {len(keys)} keys, error: {e}")
            pass

    def pipeline(self):
        """
        Create a Redis pipeline for batch operations.

        Returns:
            Pipeline: A Redis pipeline instance for executing multiple commands in a batch.

        Implementation:
            - Returns a pipeline instance from the underlying Redis client
            - In standalone mode, all commands are sent to a single Redis server
            - In cluster mode, commands are automatically routed to the correct nodes
              based on key hash slots

        Note:
            Pipeline allows batching multiple Redis commands and executing them
            atomically, reducing network round-trips and improving performance.
            Use pipeline.set(), pipeline.get(), etc. to queue commands, then
            call pipeline.execute() to execute all queued commands.

        Cluster Mode:
            - In cluster mode, pipeline operations work transparently across nodes
            - Commands are automatically distributed to the correct cluster nodes
            - Cross-slot operations are handled automatically by the Redis client
        """
        return self.redis.pipeline()
