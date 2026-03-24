# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from typing import Any, Dict, Optional, Type, TYPE_CHECKING

if TYPE_CHECKING:
    from openjiuwen.core.sys_operation.config import SandboxGatewayConfig
    from openjiuwen.core.sys_operation.sandbox.gateway.gateway import SandboxEndpoint
    from openjiuwen.core.sys_operation.sandbox.launchers.base import SandboxLauncher


class SandboxRegistry:
    """Registry for sandbox launchers and operation providers."""

    _launchers: Dict[str, Type] = {}
    _operations: Dict[str, Dict[str, Type]] = {}

    @classmethod
    def register_launcher(cls, name: str, launcher_cls: Type) -> None:
        cls._launchers[name] = launcher_cls

    @classmethod
    def get_launcher_cls(cls, name: str) -> Optional[Type]:
        return cls._launchers.get(name)

    @classmethod
    def unregister_launcher(cls, name: str) -> None:
        cls._launchers.pop(name, None)

    @classmethod
    def create_launcher(cls, launcher_type: str) -> "SandboxLauncher":
        launcher_cls = cls.get_launcher_cls(launcher_type)
        if not launcher_cls:
            raise ValueError(f"Unknown launcher_type: {launcher_type}")
        return launcher_cls()

    @classmethod
    def launcher(cls, name: str):
        def decorator(launcher_cls):
            cls.register_launcher(name, launcher_cls)
            return launcher_cls

        return decorator

    @classmethod
    def register_provider(cls, sandbox_type: str, operation_type: str, provider_cls: Type) -> None:
        if sandbox_type not in cls._operations:
            cls._operations[sandbox_type] = {}
        cls._operations[sandbox_type][operation_type] = provider_cls

    @classmethod
    def get_provider_cls(cls, sandbox_type: str, operation_type: str) -> Optional[Type]:
        return cls._operations.get(sandbox_type, {}).get(operation_type)

    @classmethod
    def unregister_provider(cls, sandbox_type: str, operation_type: str) -> None:
        if sandbox_type in cls._operations:
            cls._operations[sandbox_type].pop(operation_type, None)
            if not cls._operations[sandbox_type]:
                cls._operations.pop(sandbox_type, None)

    @classmethod
    def create_provider(
            cls,
            sandbox_type: str,
            operation_type: str,
            endpoint: "SandboxEndpoint",
            config: Optional["SandboxGatewayConfig"] = None,
    ) -> Any:
        provider_cls = cls.get_provider_cls(sandbox_type, operation_type)
        if not provider_cls:
            raise NotImplementedError(f"Sandbox type '{sandbox_type}' does not support operation '{operation_type}'")
        return provider_cls(endpoint=endpoint, config=config)

    @classmethod
    def provider(cls, sandbox_type: str, operation_type: str):
        def decorator(provider_cls):
            cls.register_provider(sandbox_type, operation_type, provider_cls)
            return provider_cls

        return decorator
