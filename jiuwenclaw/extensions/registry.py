from typing import Any, Callable

from openjiuwen.core.runner.callback.framework import AsyncCallbackFramework

from jiuwenclaw.extensions.callback_compat import unregister_callback_sync
from jiuwenclaw.gateway import AgentServerClient
from jiuwenclaw.extensions.sdk.agent_server_client import AgentServerClientExtension
from jiuwenclaw.extensions.sdk.crypto_utility import CryptoUtility
from jiuwenclaw.extensions.types import ExtensionConfig
from jiuwenclaw.common.security.base_crypto import CryptoProvider


class ExtensionRegistry:
    _instance: "ExtensionRegistry | None" = None

    def __init__(
        self,
        callback_framework: AsyncCallbackFramework,
        config: dict[str, Any],
        logger: Any,
    ):
        self._agent_server_client: AgentServerClientExtension | None = None
        self._crypto_tool: CryptoUtility | None = None
        self.callback_framework = callback_framework
        self._config = ExtensionConfig(config=config, logger=logger)

    @classmethod
    def get_instance(cls) -> "ExtensionRegistry":
        if cls._instance is None:
            raise RuntimeError("ExtensionRegistry 尚未初始化，请先调用 create_instance()")
        return cls._instance

    @classmethod
    def create_instance(
        cls,
        callback_framework: AsyncCallbackFramework,
        config: dict[str, Any],
        logger: Any,
    ) -> "ExtensionRegistry":
        if cls._instance is not None:
            raise RuntimeError("ExtensionRegistry 已初始化，请勿重复调用 create_instance()")
        cls._instance = cls(
            callback_framework=callback_framework,
            config=config,
            logger=logger,
        )
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    def register_agent_server_client(self, extension: AgentServerClientExtension) -> None:
        self._agent_server_client = extension

    def register_crypto_utility(self, extension: CryptoUtility) -> None:
        self._crypto_tool = extension

    def get_agent_server_client_extension(self) -> AgentServerClientExtension | None:
        return self._agent_server_client

    def get_agent_server_client(self) -> AgentServerClient | None:
        ext = self._agent_server_client
        return ext.get_client() if ext is not None else None

    def get_crypto_utility_extension(self) -> CryptoUtility | None:
        return self._crypto_tool

    def get_crypto_provider(self) -> CryptoProvider | None:
        ext = self._crypto_tool
        return ext.get_crypto() if ext is not None else None

    def register(
        self,
        event: str,
        handler: Callable,
        priority: int = 100,
        **kwargs,
    ) -> None:
        self.callback_framework.register_sync(event, handler, priority=priority, **kwargs)

    def unregister(self, event: str, handler: Callable | None = None) -> None:
        unregister_callback_sync(self.callback_framework, event, handler)

    async def trigger(self, event: str, context: Any | None = None, **kwargs: Any) -> None:
        """触发事件。约定由调用方传入的 context 承载回调副作用"""
        if context is None and not kwargs:
            await self.callback_framework.trigger(event)
        elif context is not None:
            await self.callback_framework.trigger(event, context, **kwargs)
        else:
            await self.callback_framework.trigger(event, **kwargs)

    @property
    def config(self) -> ExtensionConfig:
        return self._config
