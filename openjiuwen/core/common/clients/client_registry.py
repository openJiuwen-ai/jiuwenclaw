# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from typing import Dict, List, Optional, Any, Callable, Type
from openjiuwen.core.common.logging import logger


class ClientRegistry:
    """Registry for managing client classes and factories.

    This class provides a central registry for client classes, their factories.
    It supports both decorator-based and class-based registration.
    """

    def __init__(self):
        """Initialize an empty registry."""
        self._factories: Dict[str, Callable] = {}
        self._client_classes: Dict[str, Type] = {}

    def register_client(self, name: str, client_type: Optional[str] = "common"):
        """Decorator to register client factory functions.

        Args:
            name: Primary name of the client.
            client_type: Type category of the client (e.g., 'database', 'cache').

        Returns:
            Decorator function that registers the factory.

        Raises:
            ValueError: If the client name is already registered.

        Example:
            @registry.register_client('redis', client_type='cache')
            def create_redis_client(**kwargs):
                return RedisClient(**kwargs)
        """

        def decorator(factory_func: Callable) -> Callable:
            full_name = f'{client_type}_{name}' if client_type else name

            if full_name in self._factories:
                raise ValueError(f"Client type '{full_name}' already registered")

            self._factories[full_name] = factory_func
            return factory_func

        return decorator

    def register_class(self, client_class: Type['BaseClient']) -> None:
        """Register a client class.

        Args:
            client_class: The client class to register. Must have __client_name__
                         and __client_type__ attributes.

        Raises:
            ValueError: If the class doesn't define required attributes or if
                       the client name is already registered.

        Example:
            class MySQLClient(BaseClient):
                __client_name__ = 'mysql'
                __client_type__ = 'database'
        """
        if not hasattr(client_class, '__client_name__'):
            raise ValueError(f"Client class {client_class.__name__} must define __client_name__")

        if not hasattr(client_class, '__client_type__'):
            raise ValueError(f"Client class {client_class.__name__} must define __client_type__")

        name = client_class.__client_name__
        client_type = client_class.__client_type__

        if not client_type:
            raise ValueError(f"Client class {client_class.__name__} __client_type__ cannot be empty, register failed")

        full_name = f'{client_type}_{name}'

        if full_name in self._factories or full_name in self._client_classes:
            logger.warning(f"Client type '{full_name}' already registered")
            return

        # Store the client class
        self._client_classes[full_name] = client_class

        # Create factory function
        def factory(**kwargs):
            return client_class(**kwargs)

        self._factories[full_name] = factory

    def get_client(self, name: str, client_type: Optional[str] = None, **kwargs) -> Any:
        """Get a client instance.

        Args:
            name: Name of the client.
            client_type: Optional type category to narrow down the search.
            **kwargs: Additional arguments to pass to the client constructor.

        Returns:
            An instance of the requested client.

        Raises:
            ValueError: If the client name is empty or unknown.
            RuntimeError: If client creation fails.

        Example:
            client = registry.get_client('mysql', client_type='database', host='localhost')
        """
        if not name:
            raise ValueError("Client name cannot be empty")

        lookup_name = name
        if client_type:
            full_name = f'{client_type}_{name}'
            if full_name in self._factories:
                lookup_name = full_name

        factory = self._factories.get(lookup_name)
        if not factory:
            available = list(self._factories.keys())
            search_key = f'{client_type}_{name}' if client_type else name
            raise ValueError(f"Unknown client type: '{search_key}'. Available: {available}")

        try:
            return factory(**kwargs)
        except Exception as e:
            raise RuntimeError(f"Failed to create client '{lookup_name}': {e}") from e

    def unregister(self, name: str, client_type: Optional[str] = None) -> None:
        """Unregister a client.

        Args:
            name: Name of the client to unregister.
            client_type: Optional type category of the client.

        Raises:
            ValueError: If the client is not registered.
        """
        full_name = f'{client_type}_{name}' if client_type else name

        if full_name not in self._factories:
            raise ValueError(f"Client type '{full_name}' not registered")

        del self._factories[full_name]
        self._client_classes.pop(full_name, None)

    def list_clients(self) -> List[str]:
        """List all registered clients.

        Returns:
            List of registered client full names.
        """
        return list(self._factories.keys())


_client_registry = ClientRegistry()


def get_client_registry():
    return _client_registry


class BaseClient:
    """Abstract base class for asynchronous clients.

    This class serves as a template for creating client classes. Subclasses
    must define __client_name__ and __client_type__ class attributes, and
    implement all abstract methods.

    Attributes:
        __client_name__: Primary name of the client type.
        __client_type__: Type category (e.g., 'database', 'cache', 'api').
        config: Configuration dictionary passed during initialization.
    """

    __client_name__: str = None
    __client_type__: str = "common"

    def __init_subclass__(cls, **kwargs):
        """Initialize subclass and register it if it's a client class.

        This method is called whenever a class inherits from BaseClient.
        It automatically registers any class that defines __client_name__
        and __client_type__ attributes with the global client registry.

        Args:
            **kwargs: Additional keyword arguments passed to the subclass.
        """
        super().__init_subclass__(**kwargs)

        # Skip registration for BaseClient itself (though this method won't be called for BaseClient)
        # Check if client name and type are defined
        if hasattr(cls, '__client_name__') and hasattr(cls, '__client_type__'):
            # Automatically register with the global registry
            if cls.__client_name__ is not None:  # Ensure it's actually set
                get_client_registry().register_class(cls)

    def __init__(self, **kwargs):
        """Initialize the client with configuration.

        Args:
            **kwargs: Configuration parameters for the client.
        """
        self.config = kwargs

    async def close(self) -> bool:
        """Asynchronously close connection to the service.

        Returns:
            True if disconnection successful, False otherwise.
        """
        pass

    async def __aenter__(self):
        """Async context manager entry point.

        Automatically connects when entering the context.

        Returns:
            The client instance.
        """

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit point.

        Automatically disconnects when exiting the context.

        Args:
            exc_type: Exception type if an exception was raised.
            exc_val: Exception value if an exception was raised.
            exc_tb: Exception traceback if an exception was raised.
        """
        await self.close()
