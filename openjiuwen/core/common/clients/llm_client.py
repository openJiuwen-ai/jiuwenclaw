# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from typing import Any, Dict, Optional, Union
from pydantic import Field

from openjiuwen.core.common.clients.connector_pool import get_connector_pool_manager
from openjiuwen.core.common.security.url_utils import UrlUtils
from openjiuwen.core.common.clients.client_registry import get_client_registry
from openjiuwen.core.common.clients.connector_pool import ConnectorPool, ConnectorPoolConfig


class HttpXConnectorPoolConfig(ConnectorPoolConfig):
    """
    Configuration class for HTTPX connector pool.

    Extends the base ConnectorPoolConfig with HTTPX-specific settings
    for connection pooling and network configuration.
    """

    max_keepalive_connections: int = Field(
        default=20,
        description="Maximum number of keep-alive connections to maintain in the pool. "
                    "These connections are kept open for reuse, reducing connection establishment overhead.",
        ge=1
    )

    local_address: Optional[str] = Field(
        default=None,
        description="Local IP address or hostname to bind to for outgoing connections. "
                    "Useful for multi-homed systems or when a specific network interface is required."
    )

    proxy: Optional[str] = Field(
        default=None,
        description="Proxy server URL to route HTTP requests through"
    )

    need_async: bool = Field(
        default=True,
        description="Enable asynchronous mode for the connector pool. When set to True, "
                    "the pool will use async HTTPX client methods, allowing for concurrent "
                    "request handling with asyncio."
    )


@get_connector_pool_manager().register('httpx')
class HttpXConnectorPool(ConnectorPool):
    """
    HTTPX-based connector pool implementation.

    This class provides a connection pool using HTTPX's AsyncConnectionPool,
    enabling efficient connection reuse for HTTP/1.1 and HTTP/2 requests.
    It integrates with the global connector pool manager for resource sharing.
    """

    def __init__(self, config: HttpXConnectorPoolConfig):
        """
        Initialize the HTTPX connector pool.

        Args:
            config: Configuration object containing pool settings
                   (connection limits, timeouts, proxy settings, etc.)
        """
        super().__init__(config)
        import httpx
        from httpcore import ConnectionPool, AsyncConnectionPool

        # Build connection pool arguments from configuration
        pool_kwargs = {
            'max_connections': config.limit,
            'max_keepalive_connections': config.max_keepalive_connections,
            'keepalive_expiry': config.keepalive_timeout,
            'local_address': config.local_address,
            'ssl_context': config.create_ssl_context(),
            'proxy': httpx.Proxy(config.proxy) if config.proxy else None,
            **config.extend_params
        }
        # Remove None values to avoid passing invalid arguments
        pool_kwargs = {k: v for k, v in pool_kwargs.items() if v is not None}
        if config.need_async:
            self._conn = AsyncConnectionPool(**pool_kwargs)
        else:
            self._conn = ConnectionPool(**pool_kwargs)

    async def _do_close(self) -> None:
        """
        Perform actual cleanup of the connection pool.

        Note: HTTPX's AsyncConnectionPool doesn't require explicit closing
        as it handles cleanup through context managers and garbage collection.
        This method is kept as a no-op for interface compatibility.
        """
        return

    def conn(self) -> Any:
        """
        Get the underlying connection pool instance.

        Returns:
            Any: The HTTPX AsyncConnectionPool instance
        """
        return self._conn


@get_client_registry().register_client("httpx")
async def create_httpx_client(config: Union[HttpXConnectorPoolConfig, Dict[str, Any]],
                              need_async: bool = False) -> Union['httpx.Client', 'httpx.AsyncClient']:
    """
    Create an HTTPX client with connection pooling.

    This factory function creates either synchronous or asynchronous HTTPX clients
    that share a common connection pool managed by the global connector pool manager.

    Args:
        config: Configuration for the connection pool. Can be either:
               - HttpXConnectorPoolConfig instance
               - Dictionary with configuration parameters
        need_async: If True, returns an async client; otherwise returns a sync client

    Returns:
        Union[httpx.Client, httpx.AsyncClient]: Configured HTTPX client instance

    Example:
        # Create sync client
        client = create_httpx_client({"proxy": "http://proxy:8080"})

        # Create async client
        async_client = create_httpx_client(config, need_async=True)
    """
    import httpx
    if not isinstance(config, HttpXConnectorPoolConfig):
        config["need_async"] = need_async
        config = HttpXConnectorPoolConfig(**config)
    if config.need_async != need_async:
        config = config.model_copy(update={"need_async": need_async})
    connector_pool = await get_connector_pool_manager().get_connector_pool("httpx", config=config)

    if need_async:
        return httpx.AsyncClient(transport=connector_pool.conn(), verify=config.create_ssl_context(),
                                 proxy=config.proxy)
    else:
        return httpx.Client(transport=connector_pool.conn(), verify=config.create_ssl_context(), proxy=config.proxy)


@get_client_registry().register_client("async_openai")
async def create_async_openai_client(config: Union["ModelClientConfig", Dict[str, Any]],
                                     **kwargs) -> 'AsyncOpenAI':
    """
    Create an asynchronous OpenAI client with proper HTTP connection management.

    This factory function creates an AsyncOpenAI client configured with a shared
    HTTPX connection pool for efficient connection reuse and proxy support.

    Args:
        config: Configuration for the OpenAI client. Can be either:
               - ModelClientConfig instance
               - Dictionary with configuration parameters
        **kwargs: Additional keyword arguments passed to the HTTPX client configuration

    Returns:
        AsyncOpenAI: Configured asynchronous OpenAI client instance

    Example:
        config = {
            "api_key": "sk-...",
            "api_base": "https://api.openai.com/v1",
            "timeout": 30
        }
        client = create_async_openai_client(config)

        # Use with async context
        response = await client.chat.completions.create(...)
    """
    from openai import AsyncOpenAI
    from openjiuwen.core.foundation.llm import ModelClientConfig
    # Normalize configuration to ModelClientConfig
    if not isinstance(config, ModelClientConfig):
        config = ModelClientConfig(**config)

    # Create HTTPX client with connection pooling
    httpx_client = await create_httpx_client(
        config=dict(
            proxy=UrlUtils.get_global_proxy_url(config.api_base),
            ssl_verify=config.verify_ssl,
            ssl_cert=config.ssl_cert,
            **kwargs
        ),
        need_async=True
    )

    return AsyncOpenAI(
        api_key=config.api_key,
        base_url=config.api_base,
        http_client=httpx_client,
        timeout=config.timeout,
        max_retries=config.max_retries
    )


@get_client_registry().register_client("openai")
async def create_openai_client(config: Union["ModelClientConfig", Dict[str, Any]],
                               **kwargs) -> 'OpenAI':
    """
    Create a synchronous OpenAI client with proper HTTP connection management.

    This factory function creates an OpenAI client configured with a shared
    HTTPX connection pool for efficient connection reuse and proxy support.

    Args:
        config: Configuration for the OpenAI client. Can be either:
               - ModelClientConfig instance
               - Dictionary with configuration parameters
        **kwargs: Additional keyword arguments passed to the HTTPX client configuration

    Returns:
        OpenAI: Configured synchronous OpenAI client instance

    Example:
        config = {
            "api_key": "sk-...",
            "api_base": "https://api.openai.com/v1",
            "timeout": 30
        }
        client = create_openai_client(config)

        # Use for synchronous requests
        response = client.chat.completions.create(...)
    """
    from openai import OpenAI
    from openjiuwen.core.foundation.llm import ModelClientConfig

    # Normalize configuration to ModelClientConfig
    if not isinstance(config, ModelClientConfig):
        config = ModelClientConfig(**config)

    # Create HTTPX client with connection pooling
    httpx_client = await create_httpx_client(
        config=dict(
            proxy=UrlUtils.get_global_proxy_url(config.api_base),
            ssl_verify=config.verify_ssl,
            ssl_cert=config.ssl_cert,
            **kwargs
        ),
        need_async=False
    )

    return OpenAI(
        api_key=config.api_key,
        base_url=config.api_base,
        http_client=httpx_client,
        timeout=config.timeout,
        max_retries=config.max_retries
    )
