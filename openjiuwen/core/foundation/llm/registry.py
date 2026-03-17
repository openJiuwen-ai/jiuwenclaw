# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Model Client Registry

Provides non-intrusive registration mechanism for custom ModelClient implementations.
Supports both manual registration and decorator-based registration.

Usage:
    # Method 1: Manual registration
    from openjiuwen.core.foundation.llm import register_model_client
    register_model_client("MyProvider", MyModelClient)

    # Method 2: Decorator registration
    from openjiuwen.core.foundation.llm import model_client, BaseModelClient

    @model_client("MyProvider")
    class MyModelClient(BaseModelClient):
        ...
"""
from typing import Dict, Type

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model_clients.base_model_client import BaseModelClient

# ---------------------------------------------------------------------------
# Internal registry – seeded with built-in implementations in _init_builtin()
# ---------------------------------------------------------------------------
_CLIENT_TYPE_REGISTRY: Dict[str, Type[BaseModelClient]] = {}
_BUILTIN_INITIALIZED: bool = False


def _init_builtin() -> None:
    """Lazily register the built-in model clients.

    Called automatically on first access so that circular-import issues with
    concrete client modules are avoided.
    """
    global _BUILTIN_INITIALIZED
    if _BUILTIN_INITIALIZED:
        return
    _BUILTIN_INITIALIZED = True

    from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient
    from openjiuwen.core.foundation.llm.model_clients.siliconflow_model_client import SiliconFlowModelClient
    from openjiuwen.core.foundation.llm.model_clients.dashscope_model_client import DashScopeModelClient

    _builtin_clients: Dict[str, Type[BaseModelClient]] = {
        "OpenAI": OpenAIModelClient,
        "OpenRouter": OpenAIModelClient,
        "SiliconFlow": SiliconFlowModelClient,
        "DashScope": DashScopeModelClient,
    }

    for provider, cls in _builtin_clients.items():
        if provider not in _CLIENT_TYPE_REGISTRY:
            _CLIENT_TYPE_REGISTRY[provider] = cls


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def register_model_client(
        client_provider: str,
        client_class: Type[BaseModelClient],
) -> None:
    """Register a custom ModelClient implementation.

    Args:
        client_provider: Provider name used in ``ModelClientConfig.client_provider``
            (e.g. ``"DashScope"``, ``"MyProvider"``).
        client_class: A class that inherits from :class:`BaseModelClient`.

    Raises:
        TypeError: If *client_class* is not a subclass of :class:`BaseModelClient`.
        ValueError: If *client_provider* is already registered.
    """
    _init_builtin()

    if not (isinstance(client_class, type) and issubclass(client_class, BaseModelClient)):
        raise TypeError(
            f"client_class must be a subclass of BaseModelClient, "
            f"got {client_class!r}"
        )

    if client_provider in _CLIENT_TYPE_REGISTRY:
        raise ValueError(
            f"client_provider '{client_provider}' is already registered. "
            f"Call unregister_model_client('{client_provider}') first if you want to replace it."
        )

    _CLIENT_TYPE_REGISTRY[client_provider] = client_class
    logger.info(f"Registered model client: '{client_provider}' -> {client_class.__name__}")


def unregister_model_client(client_provider: str) -> None:
    """Remove a previously registered ModelClient.

    Args:
        client_provider: The provider name to remove.

    Raises:
        ValueError: If *client_provider* is not currently registered.
    """
    _init_builtin()

    if client_provider not in _CLIENT_TYPE_REGISTRY:
        raise ValueError(
            f"client_provider '{client_provider}' is not registered. "
            f"Currently registered: {', '.join(_CLIENT_TYPE_REGISTRY.keys())}"
        )

    del _CLIENT_TYPE_REGISTRY[client_provider]
    logger.info(f"Unregistered model client: '{client_provider}'")


def get_registered_clients() -> Dict[str, Type[BaseModelClient]]:
    """Return a **copy** of the current client registry.

    Returns:
        Dict mapping provider names to their :class:`BaseModelClient` subclasses.
    """
    _init_builtin()
    return dict(_CLIENT_TYPE_REGISTRY)


def get_client_class(client_provider: str) -> Type[BaseModelClient]:
    """Look up a registered client class by provider name.

    This is used internally by :class:`Model` but is also part of the public
    API for advanced use-cases.

    Args:
        client_provider: The provider name to look up.

    Returns:
        The registered :class:`BaseModelClient` subclass.

    Raises:
        KeyError: If the provider is not registered.
    """
    _init_builtin()

    client_class = _CLIENT_TYPE_REGISTRY.get(client_provider)
    if client_class is None:
        supported = ", ".join(sorted(_CLIENT_TYPE_REGISTRY.keys()))
        raise KeyError(
            f"Unsupported client_provider: '{client_provider}'. "
            f"Registered providers: {supported}"
        )
    return client_class


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------

def model_client(client_provider: str):
    """Decorator that auto-registers a :class:`BaseModelClient` subclass.

    Example::

        @model_client("MyProvider")
        class MyModelClient(BaseModelClient):
            ...

    The class is registered when the containing module is imported.

    Args:
        client_provider: Provider name used in ``ModelClientConfig.client_provider``.

    Returns:
        A class decorator.
    """

    def decorator(cls: Type[BaseModelClient]) -> Type[BaseModelClient]:
        register_model_client(client_provider, cls)
        return cls

    return decorator

