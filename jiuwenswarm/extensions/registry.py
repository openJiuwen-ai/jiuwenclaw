import inspect
import logging
from typing import Any, Callable, cast

from openjiuwen.core.runner.callback.framework import AsyncCallbackFramework

from jiuwenswarm.common.security.base_crypto import CryptoProvider
from jiuwenswarm.extensions.callback_compat import unregister_callback_sync
from jiuwenswarm.extensions.harness import (
    HarnessContribution,
    HarnessContributor,
    HarnessFailurePolicy,
    NamedHarnessContribution,
    snapshot_harness_contribution,
)
from jiuwenswarm.extensions.sdk.agent_server_client import AgentServerClientExtension
from jiuwenswarm.extensions.sdk.crypto_utility import CryptoUtility
from jiuwenswarm.extensions.sdk.third_agent import ThirdAgentExtension
from jiuwenswarm.extensions.types import ExtensionConfig
from jiuwenswarm.gateway import AgentServerClient
from jiuwenswarm.gateway.routing.third_agent import ThirdAgent

_module_logger = logging.getLogger(__name__)
_HARNESS_FAILURE_POLICIES = frozenset({"skip", "raise"})


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
        self._third_agent: ThirdAgentExtension | None = None
        self._rpc_handlers: dict[str, Callable] = {}
        self._harness_contributors: dict[
            str,
            tuple[HarnessContributor, HarnessFailurePolicy],
        ] = {}
        self.callback_framework = callback_framework
        self._config = ExtensionConfig(config=config, logger=logger)

    @classmethod
    def get_instance(cls) -> "ExtensionRegistry":
        if cls._instance is None:
            raise RuntimeError(
                "ExtensionRegistry 尚未初始化，请先调用 create_instance()"
            )
        return cls._instance

    @classmethod
    def create_instance(
        cls,
        callback_framework: AsyncCallbackFramework,
        config: dict[str, Any],
        logger: Any,
    ) -> "ExtensionRegistry":
        if cls._instance is not None:
            raise RuntimeError(
                "ExtensionRegistry 已初始化，请勿重复调用 create_instance()"
            )
        cls._instance = cls(
            callback_framework=callback_framework,
            config=config,
            logger=logger,
        )
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    @classmethod
    def get_optional_instance(cls) -> "ExtensionRegistry | None":
        """Return the process registry when initialized, otherwise ``None``."""
        return cls._instance

    def register_agent_server_client(
        self, extension: AgentServerClientExtension
    ) -> None:
        self._agent_server_client = extension

    def register_crypto_utility(self, extension: CryptoUtility) -> None:
        self._crypto_tool = extension

    def register_third_agent(self, extension: ThirdAgentExtension) -> None:
        self._third_agent = extension

    def register_harness_contributor(
        self,
        name: str,
        contributor: HarnessContributor,
        *,
        failure_policy: HarnessFailurePolicy = "skip",
    ) -> None:
        """Register a declarative Tool/Rail contributor for Agent assembly.

        Contributor names are unique and intentionally do not overwrite: silent
        replacement could remove a security Rail while leaving extension tools
        enabled. Extensions that support reload should unregister first.
        """
        contributor_name = str(name or "").strip()
        if not contributor_name:
            raise ValueError("harness contributor name is required")
        if not callable(contributor):
            raise TypeError(
                f"harness contributor '{contributor_name}' must be callable"
            )
        if inspect.iscoroutinefunction(contributor) or inspect.iscoroutinefunction(
            getattr(contributor, "__call__", None)
        ):
            raise TypeError(
                f"harness contributor '{contributor_name}' must be synchronous"
            )
        if contributor_name in self._harness_contributors:
            raise ValueError(
                f"harness contributor '{contributor_name}' is already registered"
            )
        policy_value = str(failure_policy or "").strip().lower()
        if policy_value not in _HARNESS_FAILURE_POLICIES:
            raise ValueError(
                "harness contributor failure_policy must be 'skip' or 'raise'"
            )
        normalized_policy = cast(HarnessFailurePolicy, policy_value)
        self._harness_contributors[contributor_name] = (
            contributor,
            normalized_policy,
        )

    def unregister_harness_contributor(self, name: str) -> None:
        """Remove a previously registered harness contributor, if present."""
        self._harness_contributors.pop(str(name or "").strip(), None)

    def list_harness_contributors(self) -> list[str]:
        """Return contributor names in deterministic registration order."""
        return list(self._harness_contributors)

    def snapshot_harness_contributors(
        self,
    ) -> dict[str, tuple[HarnessContributor, HarnessFailurePolicy]]:
        """Snapshot the complete contributor mapping for loader rollback."""
        return dict(self._harness_contributors)

    def restore_harness_contributors(
        self,
        snapshot: dict[str, tuple[HarnessContributor, HarnessFailurePolicy]],
    ) -> None:
        """Restore an earlier contributor mapping without invoking extensions."""
        self._harness_contributors = dict(snapshot)

    def collect_harness_contributions(
        self,
        context: Any,
    ) -> list[NamedHarnessContribution]:
        """Collect valid contributions, isolating one extension's failure."""
        collected: list[NamedHarnessContribution] = []
        extension_logger = self._config.logger or _module_logger
        # Snapshot protects collection from a contributor unregistering itself
        # (or another contributor) while this synchronous assembly pass runs.
        for name, registration in list(self._harness_contributors.items()):
            contributor, failure_policy = registration
            try:
                contribution = contributor(context)
                if inspect.isawaitable(contribution):
                    if inspect.iscoroutine(contribution):
                        contribution.close()
                    raise TypeError("harness contributor must return synchronously")
                if contribution is None:
                    continue
                if not isinstance(contribution, HarnessContribution):
                    raise TypeError(
                        "contributor must return HarnessContribution or None, got %s"
                        % type(contribution).__name__
                    )
                contribution = snapshot_harness_contribution(contribution)
                if not contribution.tools and not contribution.rails:
                    if failure_policy == "raise":
                        raise ValueError(
                            "required harness contributor returned an empty contribution"
                        )
                    continue
                collected.append(
                    NamedHarnessContribution(
                        name=name,
                        contribution=contribution,
                        failure_policy=failure_policy,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - extension boundary isolation
                if failure_policy == "raise":
                    raise RuntimeError(
                        f"required harness contributor '{name}' failed"
                    ) from exc
                extension_logger.warning(
                    "[ExtensionRegistry] harness contributor %s failed: %s",
                    name,
                    exc,
                )
        return collected

    def register_rpc_handler(self, method: str, handler: Callable) -> None:
        method_name = str(method or "").strip()
        if not method_name:
            raise ValueError("rpc method is required")
        if not callable(handler):
            raise ValueError(f"rpc handler for {method_name} must be callable")
        self._rpc_handlers[method_name] = handler

    def get_rpc_handler(self, method: str) -> Callable | None:
        return self._rpc_handlers.get(str(method or "").strip())

    def list_rpc_methods(self) -> list[str]:
        return sorted(self._rpc_handlers)

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

    def get_third_agent_extension(self) -> ThirdAgentExtension | None:
        return self._third_agent

    def get_third_agent(self) -> ThirdAgent | None:
        """Return registered ThirdAgent, or None when no extension registered."""
        ext = self._third_agent
        return ext.get_third_agent() if ext is not None else None

    def register(
        self,
        event: str,
        handler: Callable,
        priority: int = 100,
        **kwargs,
    ) -> None:
        self.callback_framework.register_sync(
            event, handler, priority=priority, **kwargs
        )

    def unregister(self, event: str, handler: Callable | None = None) -> None:
        unregister_callback_sync(self.callback_framework, event, handler)

    async def trigger(
        self, event: str, context: Any | None = None, **kwargs: Any
    ) -> None:
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
