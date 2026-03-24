# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import asyncio
import time
from typing import Any, Callable, Dict, Optional
from abc import abstractmethod
import hashlib

from aiohttp import TCPConnector
from pydantic import BaseModel, field_validator, Field

from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.clients.ref_counted import RefCountedResource
from openjiuwen.core.common.utils.singleton import Singleton


class ConnectorPoolConfig(BaseModel):
    """Configuration model for connector pools.
    """

    limit: int = Field(default=100, gt=0, description="Total connection limit")
    limit_per_host: int = Field(default=30, gt=0, description="Connections limit per host")
    ssl_verify: bool = Field(default=True, description="Whether to verify SSL")
    ssl_cert: Optional[str] = Field(default=None, description="SSL certificate path")
    force_close: bool = Field(default=False, description="Whether to force close connections")
    keepalive_timeout: Optional[float] = Field(
        default=60.0,
        gt=0,
        description="Keepalive timeout (seconds)"
    )
    ttl: Optional[int] = Field(
        default=3600,
        gt=0,
        description="Connector time-to-live (seconds), default 1 hour"
    )
    max_idle_time: Optional[int] = Field(
        default=300,
        gt=0,
        description="Maximum idle time (seconds), default 5 minutes"
    )
    extend_params: Dict[str, Any] = Field(
        default_factory=dict,
        description="Extended parameters"
    )

    @field_validator('limit', 'limit_per_host', 'ttl', 'max_idle_time', 'keepalive_timeout')
    @classmethod
    def validate_positive(cls, v, info):
        """Validate that numeric fields are positive.

        Args:
            v: Value to validate.
            info: Validation context.

        Returns:
            The validated value.

        Raises:
            ValueError: If value is not positive.
        """
        field_name = info.field_name
        if v is not None and v <= 0:
            raise ValueError(f'{field_name} must be positive')
        return v

    def create_ssl_context(self):
        """Create SSL context based on configuration.

        Returns:
            SSL context object or False if SSL verification is disabled.
        """
        if not self.ssl_verify:
            return False
        from openjiuwen.core.common.security.ssl_utils import SslUtils
        return SslUtils.create_strict_ssl_context(self.ssl_cert)

    def generate_key(self) -> str:
        """Generate a unique key for this configuration.

        Returns:
            A string key that uniquely identifies this configuration.
        """
        # Use model_dump instead of dict() (Pydantic V2)
        config_dict = self.model_dump(exclude_none=True)

        parts = []
        for field_name, field_value in sorted(config_dict.items()):
            if field_name == 'extend_params' and isinstance(field_value, dict):
                # Sort extended params dictionary for consistency
                value_str = str(sorted(field_value.items())) if field_value else "{}"
            elif isinstance(field_value, bool):
                value_str = str(field_value).lower()
            else:
                value_str = str(field_value)
            parts.append(f"{field_name}:{value_str}")

        key_str = "&".join(parts)
        md5_hash = hashlib.md5(key_str.encode()).hexdigest()
        return md5_hash

    class Config:
        """Pydantic model configuration."""
        frozen = True  # Make configuration immutable, suitable for use as a key
        extra = 'forbid'  # Forbid extra fields


class ConnectorPool(RefCountedResource):
    """Base class for connector pools with reference counting.

    This class provides reference counting and lifecycle management for
    various types of connector pools.

    Attributes:
        _config: Configuration for this connector pool.
        _conn: Underlying connector instance.
    """

    def __init__(self, config: ConnectorPoolConfig):
        """Initialize the connector pool.

        Args:
            config: Configuration for the connector pool.
        """
        super().__init__()
        self._config = config
        self._conn = None

    @property
    def config(self):
        return self._config

    @abstractmethod
    def conn(self) -> Any:
        """Get the underlying connector.

        Returns:
            The connector instance.
        """
        pass

    @abstractmethod
    async def _do_close(self, **kwargs) -> None:
        """Perform the actual close operation.

        This method should be implemented by subclasses to handle
        connector-specific cleanup.
        """
        pass

    def is_expired(self) -> bool:
        """Check if the connector pool has expired.

        Returns:
            True if the pool has exceeded its TTL or max idle time, False otherwise.
        """
        current_time = time.time()
        if self._config.ttl and (current_time - self._created_at) > self._config.ttl:
            return True

        if self._config.max_idle_time and (current_time - self._last_used) > self._config.max_idle_time:
            return True

        return False

    def stat(self) -> Dict[str, Any]:
        """Get statistics for this connector pool.

        Returns:
            Dictionary containing pool statistics.
        """
        return {
            'closed': self.closed,
            'ref_detail': self.get_stats()
        }


class ConnectorPoolManager(metaclass=Singleton):
    """Manager for connector pools with lifecycle management.

    This class manages a collection of connector pools, handling creation,
    reference counting, cleanup, and resource limits.

    Attributes:
        _connector_pools: Dictionary mapping keys to connector pools.
        _default_config: Default configuration for new pools.
        _max_pools: Maximum number of pools to maintain.
        _lock: Asyncio lock for thread safety.
        _closed: Whether the manager is closed.
    """

    _connector_pool_providers: Dict[str, Callable] = {}

    def __init__(self, max_pools: int = 100):
        """Initialize the connector pool manager.

        Args:
            max_pools: Maximum number of pools to maintain.
        """
        self._connector_pools: Dict[str, ConnectorPool] = {}
        self._default_config = ConnectorPoolConfig()
        self._default_config_key = self._default_config.generate_key()
        self._max_pools = max_pools
        self._lock = asyncio.Lock()
        self._closed = False
        self._instance_id = id(self)

    async def __aenter__(self):
        """Enter the async context manager.

        Returns:
            The connector pool manager instance.
        """
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit the async context manager.

        Args:
            exc_type: Exception type if an exception was raised.
            exc_val: Exception value if an exception was raised.
            exc_tb: Exception traceback if an exception was raised.
        """
        await self.close_all()

    async def _force_remove_pool(self, key: str):
        """Forcefully remove a connector pool.

        Args:
            key: Key of the pool to remove.
        """
        logger.info(f"Force removing connector pool: {key}")
        pool = self._connector_pools.pop(key, None)
        if pool:
            ref_count = pool.ref_count
            try:
                await pool.close()
                logger.info(f"Successfully removed connector pool: {key}, had ref_count={ref_count}")
            except Exception as e:
                logger.error(f"Error closing connector pool {key}: {e}", exc_info=True)
        else:
            logger.warning(f"Attempted to remove non-existent connector pool: {key}")

    @classmethod
    def register(cls, connector_pool_type: str):
        """Register a connector pool type.

        This decorator registers a factory function for creating connector pools
        of a specific type.

        Args:
            connector_pool_type: Type identifier for the connector pool.

        Returns:
            Decorator function.
        """

        def decorator(factory_func: Callable):
            cls._connector_pool_providers[connector_pool_type] = factory_func
            logger.info(f"Registered connector pool type: {connector_pool_type}")
            return factory_func

        return decorator

    async def get_connector_pool(self, connector_pool_type: str = "default", *,
                                 config: Optional[ConnectorPoolConfig] = None) -> ConnectorPool:
        """Get or create a connector pool.

        Args:
            connector_pool_type: Type of connector pool to get/create.
            config: Optional configuration for the pool.

        Returns:
            A connector pool instance.

        Raises:
            RuntimeError: If the manager is closed.
            ValueError: If the connector type is unknown.
        """
        if self._closed:
            raise RuntimeError("ConnectorPoolManager is closed")

        connector_config = config or self._default_config
        key = connector_config.generate_key() if config else self._default_config_key

        logger.debug(f"Getting connector pool: type={connector_pool_type}, key={key}")

        async with self._lock:
            # Check for existing pool
            if key in self._connector_pools:
                connector_pool = self._connector_pools[key]
                logger.debug(f"Found existing connector pool with key={key}, ref_count={connector_pool.ref_count}")

                # Check if pool is already closed
                if connector_pool.closed:
                    # Pool is closed, remove from dictionary
                    del self._connector_pools[key]
                    logger.warning(f"Removed closed connector pool, key={key}, config={config}")
                else:
                    # Increment reference count and return
                    connector_pool.increment_ref()
                    logger.debug(f"Incremented ref count for pool {key}, now ref_count={connector_pool.ref_count}")
                    return connector_pool

            # Check if maximum number of pools is reached
            if len(self._connector_pools) >= self._max_pools:
                logger.warning(f"Maximum pools reached ({self._max_pools}), evicting oldest pool")
                await self._evict_oldest_pool()

            # Create new pool
            connector_pool = await self._create_connector_pool(connector_pool_type, connector_config)

            self._connector_pools[key] = connector_pool
            logger.info(
                f"Creating new connector pool: type={connector_pool_type}, key={key}, config={config}, "
                f"current total pools={len(self._connector_pools)}")

            return connector_pool

    async def _evict_oldest_pool(self):
        """Evict the oldest unused connector pool to free up space."""
        logger.debug("Starting pool eviction process")

        # Find all pools with zero references
        idle_pools = [
            (k, v) for k, v in self._connector_pools.items()
            if v.ref_count == 0 and not v.closed
        ]

        if idle_pools:
            # Sort by last used time (oldest first)
            sorted_pools = sorted(idle_pools, key=lambda x: x[1].last_used)
            oldest_key, oldest_pool = sorted_pools[0]

            logger.info(f"Evicting oldest idle pool: {oldest_key}, last_used={oldest_pool.last_used:.2f}")
            await self._force_remove_pool(oldest_key)
        else:
            # If no idle pools, evict the oldest (even with references)
            if self._connector_pools:
                # Sort by creation time (oldest first)
                sorted_pools = sorted(
                    self._connector_pools.items(),
                    key=lambda x: x[1].created_at
                )
                oldest_key, oldest_pool = sorted_pools[0]

                logger.warning(f"No idle pools available, evicting oldest active pool: {oldest_key}, "
                               f"ref_count={oldest_pool.ref_count}, created_at={oldest_pool.created_at:.2f}")
                await self._force_remove_pool(oldest_key)

    async def _create_connector_pool(self, connector_pool_type: str,
                                     config: ConnectorPoolConfig) -> ConnectorPool:
        """Create a new connector pool.

        Args:
            connector_pool_type: Type of connector pool to create.
            config: Configuration for the new pool.

        Returns:
            A new connector pool instance.

        Raises:
            ValueError: If the connector type is unknown.
        """
        if connector_pool_type not in self._connector_pool_providers:
            logger.error(f"Unknown connector type: {connector_pool_type}")
            raise ValueError(f"Unknown connector type: {connector_pool_type}")

        connector_class = self._connector_pool_providers[connector_pool_type]
        logger.debug(f"Creating connector pool using provider: {connector_pool_type}")

        result = connector_class(config)
        if asyncio.iscoroutine(result):
            logger.debug(f"Awaiting coroutine result from provider: {connector_pool_type}")
            result = await result

        logger.info(f"Successfully created connector pool of type: {connector_pool_type}")
        return result

    async def release_connector_pool(self, config: Optional[ConnectorPoolConfig] = None):
        """Release a reference to a connector pool.

        Args:
            config: Configuration of the pool to release.
        """
        if self._closed:
            logger.debug("Manager closed, skipping release_connector_pool")
            return

        if config:
            key = config.generate_key()
            logger.debug(f"Releasing connector pool: key={key}")

            async with self._lock:
                if key in self._connector_pools:
                    pool = self._connector_pools[key]
                    old_ref = pool.ref_count
                    pool.decrement_ref()
                    logger.debug(f"Released pool {key}: ref_count changed from {old_ref} to {pool.ref_count}")
                else:
                    logger.warning(f"Attempted to release non-existent pool: {key}")
        else:
            logger.debug("release_connector_pool called with no config")

    async def close_connector_pool(self, *, config: Optional[ConnectorPoolConfig] = None,
                                   force: bool = False):
        """Close a specific connector pool.

        Args:
            config: Configuration of the pool to close.
            force: If True, close even if references exist.
        """
        if self._closed:
            return

        if config:
            key = config.generate_key()
            logger.info(f"Closing connector pool: key={key}, force={force}")

            async with self._lock:
                if key in self._connector_pools:
                    pool = self._connector_pools[key]
                    if force or pool.closed or pool.ref_count == 0:
                        logger.info(
                            f"Closing pool {key}: force={force}, closed={pool.closed}, ref_count={pool.ref_count}")
                        await self._force_remove_pool(key)
                    else:
                        logger.warning(
                            f"Cannot close connector pool {key} with "
                            f"ref_count={pool.ref_count}"
                        )
                else:
                    logger.warning(f"Attempted to close non-existent pool: {key}")

    async def close_all(self):
        """Close all connector pools and shutdown the manager."""
        if self._closed:
            return

        logger.info(f"Closing all connector pools, total={len(self._connector_pools)}")
        self._closed = True

        async with self._lock:
            close_tasks = []
            for key, connector in list(self._connector_pools.items()):
                if connector.ref_count > 0:
                    logger.warning(
                        f"Closing connector pool {key} with "
                        f"ref_count={connector.ref_count}"
                    )
                close_tasks.append(self._force_remove_pool(key))

            if close_tasks:
                logger.debug(f"Waiting for {len(close_tasks)} pools to close")
                results = await asyncio.gather(*close_tasks, return_exceptions=True)

                # Log any errors from close operations
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        logger.error(f"Error closing pool: {result}")

        logger.info("All connector pools closed")

    def get_stats(self) -> Dict:
        """Get statistics for all connector pools.

        Returns:
            Dictionary containing overall manager statistics and per-pool stats.
        """
        stats = {
            'total_connector_pools': len(self._connector_pools),
            'max_pools': self._max_pools,
            'closed': self._closed,
            'connectors': {},
        }
        for key, connector_pool in self._connector_pools.items():
            stats['connectors'][key] = connector_pool.stat()

        return stats


def get_connector_pool_manager():
    return ConnectorPoolManager()


# Global instance
_connector_pool_manager = get_connector_pool_manager()


@ConnectorPoolManager.register("default")
class TcpConnectorPool(ConnectorPool):
    """TCP connector pool based on aiohttp.TCPConnector.

    This class provides a connector pool implementation using aiohttp's
    TCPConnector with reference counting and lifecycle management.
    """

    def __init__(self, config: ConnectorPoolConfig):
        """Initialize the TCP connector pool.

        Args:
            config: Configuration for the connector pool.

        Raises:
            Exception: If TCPConnector creation fails.
        """
        super().__init__(config)

        # Prepare parameters for TCPConnector
        kwargs = {
            'limit': config.limit,
            'limit_per_host': config.limit_per_host,
            'ssl': config.create_ssl_context() if config.ssl_verify else False,
            'keepalive_timeout': config.keepalive_timeout,
            'force_close': config.force_close,
            **config.extend_params
        }

        try:
            self._conn = TCPConnector(**kwargs)
        except Exception as e:
            logger.error(f"Failed to create TCPConnector: {e}", exc_info=True)
            raise

    async def _do_close(self) -> None:
        """Close the TCP connector."""
        logger.debug("Closing TCP connector")
        if self._conn and not self._conn.closed:
            try:
                await self._conn.close()
                logger.info("TCPConnector closed successfully")
            except Exception as e:
                logger.error(f"Error closing TCPConnector: {e}", exc_info=True)
                raise
        else:
            logger.debug("TCPConnector already closed or None")

    def conn(self) -> Optional[TCPConnector]:
        """Get the TCP connector instance.

        Returns:
            The TCPConnector instance or None if closed.
        """
        return self._conn

    def stat(self) -> Dict:
        """Get statistics for this TCP connector.

        Returns:
            Dictionary containing connector statistics.
        """
        if self._conn and not self._conn.closed:
            stats = {
                'limit': self._conn.limit,
                'limit_per_host': self._conn.limit_per_host,
                'closed': self._conn.closed,
                'created_at': self._created_at,
                'last_used': self._last_used,
                'ref_count': self.ref_count
            }
            return stats
        return {'closed': True}
