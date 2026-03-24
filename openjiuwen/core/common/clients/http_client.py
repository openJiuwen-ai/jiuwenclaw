# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import asyncio
from contextlib import asynccontextmanager
from typing import Any, Callable, Dict, Optional, Union, Awaitable
import inspect
import hashlib

import aiohttp
from aiohttp import ClientSession
from pydantic import BaseModel, Field

from openjiuwen.core.common.clients import get_connector_pool_manager
from openjiuwen.core.common.clients.ref_counted import BaseRefResourceMgr, RefCountedResource
from openjiuwen.core.common.utils.singleton import Singleton
from openjiuwen.core.foundation.tool.service_api.response_parser import ParserRegistry
from openjiuwen.core.common.clients.client_registry import BaseClient
from openjiuwen.core.common.clients.connector_pool import ConnectorPoolConfig


class SessionConfig(BaseModel):
    """
    Configuration model for HTTP session.

    Defines all configurable parameters for creating an HTTP session,
    including connection pooling, timeouts, headers, and other request settings.
    """

    connector_pool_config: ConnectorPoolConfig = Field(
        default_factory=ConnectorPoolConfig,
        description="Configuration for the connection pool"
    )

    headers: Optional[Dict[str, str]] = Field(
        None,
        description="Default HTTP headers to be sent with each request"
    )

    proxy: Optional[str] = Field(
        None,
        description="Proxy servers to be used for requests (list of proxy URLs)"
    )

    timeout: Optional[float] = Field(
        None,
        description="Total timeout for the entire request in seconds"
    )

    connect_timeout: Optional[float] = Field(
        None,
        description="Timeout for establishing a connection in seconds"
    )

    timeout_args: Optional[Dict[str, str]] = Field(
        default_factory=dict,
        description="Additional timeout arguments (sock_read_timeout, sock_connect_timeout, ceil_threshold)"
    )

    auth: Any = Field(
        None,
        description="Authentication credentials (aiohttp.BasicAuth or similar)"
    )

    raise_for_status: bool = Field(
        False,
        description="Whether to raise an exception for HTTP error status codes (4xx or 5xx)"
    )

    trust_env: bool = Field(
        True,
        description="Whether to trust environment variables for proxy settings (HTTP_PROXY, HTTPS_PROXY)"
    )

    extend_args: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional keyword arguments to pass to aiohttp.ClientSession constructor"
    )

    def generate_key(self) -> str:
        """
        Generate a unique key based on the session configuration.

        This key is used for session reuse - sessions with identical configuration
        will have the same key and can be shared.

        Returns:
            str: A unique key representing this configuration, using MD5 hash if the string is too long
        """
        parts = []

        # Get all fields with their values, excluding unset fields if necessary
        for field_name, field_value in self.model_dump().items():
            if field_value is None:
                continue

            # Recursively generate key for connector pool config
            if field_name == 'connector_pool_config' and hasattr(field_value, 'generate_key'):
                parts.append(f"{field_value.generate_key()}")
                continue

            # Handle complex types (dict/list) by sorting their items
            if isinstance(field_value, (dict, list)):
                if isinstance(field_value, dict):
                    # Sort dictionary items for consistent key generation
                    sorted_items = sorted(field_value.items())
                    value_str = str(sorted_items)
                else:
                    # Sort list items for consistent key generation
                    sorted_items = sorted(field_value)
                    value_str = str(sorted_items)
            else:
                value_str = str(field_value)

            parts.append(f"{field_name}:{value_str}")

        # Sort parts to ensure consistent key generation regardless of field order
        parts.sort()
        key_str = "&".join(parts)

        # Hash the key if it's too long
        if len(key_str) > 256:
            md5_hash = hashlib.md5(key_str.encode()).hexdigest()
            return md5_hash

        return key_str

    class Config:
        """Pydantic model configuration."""
        arbitrary_types_allowed = True  # Allow types like aiohttp.BasicAuth for auth field
        extra = "forbid"  # Forbid extra fields not defined in the model


class HttpSession(RefCountedResource):
    """
    Wrapper for aiohttp ClientSession with reference counting support.

    This class extends RefCountedResource to enable automatic resource management
    and cleanup when the session is no longer in use.
    """

    def __init__(self, session: ClientSession, config: SessionConfig):
        """
        Initialize the HTTP session wrapper.

        Args:
            session: The underlying aiohttp ClientSession instance
            config: The configuration used to create this session
        """
        super().__init__()
        self._session = session
        self._config = config

    @property
    def config(self):
        return self._config

    def session(self) -> ClientSession:
        """
        Get the underlying aiohttp ClientSession.

        Returns:
            ClientSession: The wrapped aiohttp session

        Raises:
            RuntimeError: If the session has been closed
        """
        if self._closed:
            raise RuntimeError("Session is closed")
        return self._session

    async def _do_close(self):
        """
        Perform the actual cleanup of the session.

        This method is called when the reference count reaches zero.
        Closes the underlying aiohttp ClientSession.
        """
        if self._session and not self._session.close():
            await self._session.close()


class HttpSessionManager(BaseRefResourceMgr[HttpSession], metaclass=Singleton):
    """
    Manager for HTTP sessions that handles creation, reuse, and cleanup.

    This class maintains a pool of HTTP sessions and ensures that sessions
    with identical configurations are reused, reducing resource usage.

    Examples:
        Basic usage with context manager:
        ```python
        # Get the global session manager instance
        manager = HttpSessionManager()

        # Use default configuration
        async with manager.get_session() as session:
            # Make requests using the session
            async with session.session().get("https://api.example.com/users") as resp:
                data = await resp.json()
                print(data)

        config1 = SessionConfig(timeout=10)
        session1, is_new = await manager.acquire(config1)  # is_new1 = True
        session2, is_new = await manager.acquire(config1)  # is_new1 = False
        print(session1 is session2)
        await manager.release(session1)
    """

    def __init__(self):
        """Initialize the session manager with default configuration."""
        super().__init__()
        self._default_config = SessionConfig()

    def _get_resource_key(self, config: SessionConfig) -> str:
        """
        Generate a unique key for the given configuration.

        Args:
            config: The session configuration

        Returns:
            str: A unique key for identifying sessions with this configuration
        """
        return config.generate_key()

    @asynccontextmanager
    async def get_session(self, config: Optional[SessionConfig] = None):
        """
        Context manager for acquiring and releasing a session.

        Args:
            config: Optional session configuration (uses default if not provided)

        Yields:
            HttpSession: The acquired session
        """
        config = config or self._default_config
        resource, _ = await self.acquire(config)
        try:
            yield resource
        finally:
            await self.release_session(config)

    async def release_session(self, config: SessionConfig):
        """
        Release a session back to the manager.

        Args:
            config: The session config to release
        """
        await self.release(config)

    async def _create_resource(self, config: SessionConfig) -> HttpSession:
        """
        Create a new HTTP session based on the given configuration.

        Args:
            config: The session configuration

        Returns:
            HttpSession: A new HTTP session instance
        """
        # Build kwargs for aiohttp.ClientSession
        kwargs = {
            'connector': (
                await get_connector_pool_manager().get_connector_pool(config=config.connector_pool_config)).conn(),
            'headers': config.headers,
            'proxy': config.proxy,
            'auth': config.auth,
            'timeout': aiohttp.ClientTimeout(
                total=config.timeout,
                connect=config.connect_timeout,
                sock_read=config.timeout_args.get("sock_read_timeout"),
                sock_connect=config.timeout_args.get("sock_connect_timeout"),
                ceil_threshold=config.timeout_args.get("ceil_threshold_timeout", 5),
            ),
            'raise_for_status': config.raise_for_status,
            'trust_env': config.trust_env,
            **config.extend_args,
        }
        kwargs.update({"connector_owner": False})  # Connector is managed by connector pool
        kwargs = {k: v for k, v in kwargs.items() if v is not None}

        session = ClientSession(**kwargs)
        return HttpSession(session, config)


_http_session_manager = HttpSessionManager()


def get_http_session_manager():
    return _http_session_manager


class HttpClient(BaseClient):
    """
    HTTP client with session management and connection pooling.

    This client supports both reusable sessions (for multiple requests with the same client)
    and one-time sessions (for individual requests). It integrates with the connection pool
    manager for efficient TCP connection reuse.

    Example:
        async with HttpClient() as client:
            result = await client.get("https://api.example.com/data")
            print(result["data"])

        # With custom configuration
        config = SessionConfig(timeout=30, headers={"User-Agent": "MyApp"})
        client = HttpClient(config)
        result = await client.post("https://api.example.com/submit", body={"key": "value"})
        await client.close()
    """
    __client_name__ = "http"

    def __init__(self,
                 config: Optional[Union[SessionConfig, Dict[str, Any]]] = None, *,
                 reuse_session: bool = True):
        """
        Initialize the HTTP client.

        Args:
            config: Optional session configuration (can be SessionConfig object or dict)
            reuse_session: If True, reuse the same session for multiple requests;
                          If False, create a new session for each request
        """
        super().__init__()
        self._config = self._normalize_config(config)
        self._session_manager = get_http_session_manager()
        self._reuse_session = reuse_session
        self._session: Optional[HttpSession] = None
        self._lock = asyncio.Lock()
        self._closed = False

    def _normalize_config(self, config: Optional[Union[SessionConfig, Dict]]) -> SessionConfig:
        """
        Convert input configuration to SessionConfig object.

        Args:
            config: Raw configuration (None, dict, or SessionConfig)

        Returns:
            SessionConfig: Normalized configuration object
        """
        if config is None:
            return SessionConfig()
        if isinstance(config, SessionConfig):
            return config
        return SessionConfig(**config)

    async def __aenter__(self):
        """Enter async context manager."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit async context manager and close the client."""
        await self.close()

    async def _acquire_session(self) -> HttpSession:
        """
        Acquire an HTTP session based on the reuse strategy.

        Returns:
            HttpSession: The acquired session

        Raises:
            RuntimeError: If the client is closed and trying to acquire a session
        """
        if self._reuse_session:
            # For reusable sessions, maintain a single session instance
            if self._closed:
                raise RuntimeError("HttpClient is closed")

            # Create session if it doesn't exist or was closed
            if self._session is None or self._session.closed:
                async with self._lock:
                    if self._session is None or self._session.closed:
                        self._session, is_new = await self._session_manager.acquire(self._config)

            return self._session
        else:
            # For one-time use, acquire a new session each time
            session, is_new = await self._session_manager.acquire(self._config)
            return session

    async def _release_session(self, session: HttpSession):
        """
        Release a session after use.

        Args:
            session: The session to release
        """
        if self._reuse_session:
            # For reusable sessions, don't release until client is closed
            pass
        else:
            # For one-time sessions, release immediately
            await self._session_manager.release(session.config)

    async def close(self):
        """Close the HTTP client and release any held sessions."""
        if self._closed:
            return

        if self._reuse_session and self._session:
            async with self._lock:
                if self._session:
                    await self._session_manager.release(self._session.config)
                    self._session = None

        self._closed = True

    def _build_request_kwargs(self,
                              headers: Optional[Dict] = None,
                              timeout: Optional[float] = None,
                              timeout_args: Optional[Dict] = None,
                              **kwargs) -> Dict:
        """
        Build keyword arguments for aiohttp request.

        Args:
            headers: Request-specific headers (merged with default headers)
            timeout: Request timeout in seconds
            timeout_args: Detailed timeout configuration
            **kwargs: Additional request arguments

        Returns:
            Dict: Combined request kwargs
        """
        req_kwargs = {
            'headers': self._config.headers.copy() if self._config.headers else None,
            **kwargs
        }
        if req_kwargs["headers"] and headers:
            req_kwargs["headers"].update(headers)
        elif headers:
            req_kwargs["headers"] = headers

        # Configure timeout (detailed args take precedence over simple timeout)
        if timeout_args:
            req_kwargs['timeout'] = aiohttp.ClientTimeout(**timeout_args)
        elif timeout:
            req_kwargs['timeout'] = aiohttp.ClientTimeout(total=timeout)

        return req_kwargs

    async def _request(self,
                       method: str,
                       url: str,
                       headers: Optional[Dict[str, Any]] = None,
                       timeout: Optional[float] = None,
                       timeout_args: Optional[Dict[str, Any]] = None,
                       chunked: bool = False,
                       chunk_size: int = 1024,
                       response_bytes_size_limit: int = 10 * 1024 * 1024,  # 10MB default
                       **kwargs) -> Dict:
        """
        Internal method to perform HTTP requests.

        Args:
            method: HTTP method (GET, POST, etc.)
            url: Request URL
            headers: Request headers
            timeout: Request timeout
            timeout_args: Detailed timeout configuration
            chunked: Whether to read response in chunks
            chunk_size: Size of each chunk when chunked=True
            response_bytes_size_limit: Maximum response size when chunked=True
            **kwargs: Additional request parameters

        Returns:
            Dict: Response containing status code, data, headers, etc.

        Raises:
            ValueError: If response size exceeds limit
        """
        session = await self._acquire_session()
        try:
            req_kwargs = self._build_request_kwargs(headers, timeout, timeout_args, **kwargs)
            async with session.session().request(method, url, **req_kwargs) as response:
                status = response.status

                if chunked:
                    # Read response in chunks with size limit
                    bytes_content = bytearray()
                    async for chunk in response.content.iter_chunked(chunk_size):
                        bytes_content.extend(chunk)
                        if len(bytes_content) > response_bytes_size_limit:
                            raise ValueError(
                                f"Response too large: {len(bytes_content)} > {response_bytes_size_limit}"
                            )
                    # Parse the complete response
                    content = ParserRegistry().parse(
                        response_headers=response.headers,
                        response_data=bytes_content,
                        status_code=status
                    )
                else:
                    # Auto-detect response type based on Content-Type header
                    content_type = response.headers.get("Content-Type", "")
                    if "application/json" in content_type:
                        content = await response.json()
                    elif "text/" in content_type:
                        content = await response.text()
                    else:
                        content = await response.read()

                return {
                    "code": status,
                    "data": content,
                    "url": str(response.url),
                    "headers": dict(response.headers),
                    "reason": response.reason
                }
        finally:
            await self._release_session(session)

    async def _stream_request(self,
                              method: str,
                              url: str,
                              headers: Optional[Dict[str, Any]] = None,
                              timeout: Optional[float] = None,
                              timeout_args: Optional[Dict[str, Any]] = None,
                              chunked: bool = False,
                              chunk_size: int = 1024,
                              on_stream_received: Optional[
                                  Union[Callable[[bytes], Any], Callable[[bytes], Awaitable[Any]]]
                              ] = None,
                              **kwargs):
        """
        Internal method for streaming HTTP responses.

        Args:
            method: HTTP method
            url: Request URL
            headers: Request headers
            timeout: Request timeout
            timeout_args: Detailed timeout configuration
            chunked: Whether to use chunked reading
            chunk_size: Size of each chunk
            on_stream_received: Optional callback for each chunk (sync or async)
            **kwargs: Additional request parameters

        Yields:
            Processed chunks (either raw data or callback results)
        """
        session = await self._acquire_session()

        try:
            req_kwargs = self._build_request_kwargs(headers, timeout, timeout_args, **kwargs)
            async with session.session().request(method, url, **req_kwargs) as response:
                if chunked:
                    # Read in fixed-size chunks
                    async for chunk in response.content.iter_chunked(chunk_size):
                        if on_stream_received:
                            if inspect.iscoroutinefunction(on_stream_received):
                                result = await on_stream_received(chunk)
                            else:
                                result = on_stream_received(chunk)
                            yield result
                        else:
                            yield chunk
                else:
                    # Read line by line (useful for text streams)
                    async for line in response.content:
                        if on_stream_received:
                            if inspect.iscoroutinefunction(on_stream_received):
                                result = await on_stream_received(line)
                            else:
                                result = on_stream_received(line)
                            yield result
                        else:
                            yield line
        finally:
            await self._release_session(session)

    # Standard HTTP methods
    async def get(self, url: str, params: Optional[Dict] = None, **kwargs) -> Dict:
        """
        Perform HTTP GET request.

        Args:
            url: Request URL
            params: Query parameters
            **kwargs: Additional request parameters

        Returns:
            Dict: Response data
        """
        return await self._request('GET', url, params=params, **kwargs)

    async def post(self, url: str, body: Optional[Dict] = None, **kwargs) -> Dict:
        """
        Perform HTTP POST request.

        Args:
            url: Request URL
            body: Request body (will be JSON-encoded)
            **kwargs: Additional request parameters

        Returns:
            Dict: Response data
        """
        return await self._request('POST', url, json=body, **kwargs)

    async def put(self, url: str, body: Optional[Dict] = None, **kwargs) -> Dict:
        """
        Perform HTTP PUT request.

        Args:
            url: Request URL
            body: Request body (will be JSON-encoded)
            **kwargs: Additional request parameters

        Returns:
            Dict: Response data
        """
        return await self._request('PUT', url, json=body, **kwargs)

    async def delete(self, url: str, **kwargs) -> Dict:
        """
        Perform HTTP DELETE request.

        Args:
            url: Request URL
            **kwargs: Additional request parameters

        Returns:
            Dict: Response data
        """
        return await self._request('DELETE', url, **kwargs)

    async def patch(self, url: str, body: Optional[Dict] = None, **kwargs) -> Dict:
        """
        Perform HTTP PATCH request.

        Args:
            url: Request URL
            body: Request body (will be JSON-encoded)
            **kwargs: Additional request parameters

        Returns:
            Dict: Response data
        """
        return await self._request('PATCH', url, json=body, **kwargs)

    async def head(self, url: str, **kwargs) -> Dict:
        """
        Perform HTTP HEAD request.

        Args:
            url: Request URL
            **kwargs: Additional request parameters

        Returns:
            Dict: Response data (headers only, no body)
        """
        return await self._request('HEAD', url, **kwargs)

    async def options(self, url: str, **kwargs) -> Dict:
        """
        Perform HTTP OPTIONS request.

        Args:
            url: Request URL
            **kwargs: Additional request parameters

        Returns:
            Dict: Response data
        """
        return await self._request('OPTIONS', url, **kwargs)

    # Streaming methods
    async def stream_get(self, url: str, params: Optional[Dict] = None, **kwargs):
        """
        Stream HTTP GET response.

        Args:
            url: Request URL
            params: Query parameters
            **kwargs: Additional parameters (chunked, chunk_size, on_stream_received, etc.)

        Yields:
            Response chunks
        """
        async for chunk in self._stream_request('GET', url, params=params, **kwargs):
            yield chunk

    async def stream_post(self, url: str, body: Optional[Dict] = None, **kwargs):
        """
        Stream HTTP POST response.

        Args:
            url: Request URL
            body: Request body (will be JSON-encoded)
            **kwargs: Additional parameters (chunked, chunk_size, on_stream_received, etc.)

        Yields:
            Response chunks
        """
        async for chunk in self._stream_request('POST', url, json=body, **kwargs):
            yield chunk

    @property
    def closed(self) -> bool:
        """
        Check if the client is closed.

        Returns:
            bool: True if the client is closed, False otherwise
        """
        return self._closed
