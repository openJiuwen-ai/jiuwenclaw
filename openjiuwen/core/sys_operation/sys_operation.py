# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import threading
from typing import Optional, Dict
from pydantic import Field, field_validator

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.schema import BaseCard
from openjiuwen.core.sys_operation.base import OperationMode
from openjiuwen.core.sys_operation.code import BaseCodeOperation
from openjiuwen.core.sys_operation.config import ContainerScope, LocalWorkConfig, SandboxGatewayConfig
from openjiuwen.core.sys_operation.fs import BaseFsOperation
from openjiuwen.core.sys_operation.registry import OperationRegistry
from openjiuwen.core.sys_operation.shell import BaseShellOperation
from openjiuwen.core.sys_operation.sandbox.run_config import SandboxRunConfig


# Template placeholder for session_id in isolation key template
_TEMPLATE_SESSION_PLACEHOLDER = "{session_id}"


def generate_isolation_key_template(
    isolation_prefix: Optional[str],
    container_scope: ContainerScope,
    custom_id: Optional[str],
    launcher_type: str = "pre_deploy",
    sandbox_type: str = "aio",
) -> str:
    """Generate isolation key template without actual session_id.

    This template is used for conflict detection - it represents the pattern
    of isolation keys without the dynamic session_id component.

    Format: {container_scope}_{launcher_type}_{sandbox_type}_{prefix}_{identity}
    Where identity is:
    - SYSTEM: "system"
    - SESSION: "{session_id}" (placeholder)
    - CUSTOM: custom_id

    Args:
        isolation_prefix: Namespace prefix for agent isolation
        container_scope: Isolation level (SYSTEM/SESSION/CUSTOM)
        custom_id: Fixed container key for CUSTOM scope
        launcher_type: Launcher type (pre_deploy)
        sandbox_type: Sandbox type (aio)

    Returns:
        Isolation key template string
    """
    prefix = f"{isolation_prefix}_" if isolation_prefix else ""

    if container_scope == ContainerScope.SYSTEM:
        identity = "system"
    elif container_scope == ContainerScope.CUSTOM:
        if custom_id:
            identity = custom_id
        else:
            raise ValueError("container_scope is CUSTOM but custom_id is None")
    elif container_scope == ContainerScope.SESSION:
        identity = _TEMPLATE_SESSION_PLACEHOLDER
    else:
        identity = "default"

    return f"{container_scope.value}_{launcher_type}_{sandbox_type}_{prefix}{identity}"


class SysOperationMgr:
    """Manager for SysOperation instances with sandbox key ownership tracking.

    This manager handles:
    - SysOperation instance storage
    - Isolation key template conflict detection
    - Sandbox key to operation_id mapping
    """

    _instance: Optional["SysOperationMgr"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._sys_operations: Dict[str, "SysOperation"] = {}
        self._sandbox_key_owner_map: Dict[str, str] = {}
        self._operation_to_key_map: Dict[str, str] = {}
        self._internal_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "SysOperationMgr":
        """Get singleton instance of SysOperationMgr."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def register_sandbox_key(self, isolation_key_template: str, operation_id: str) -> None:
        """Register an isolation key template and check for conflicts.

        Args:
            isolation_key_template: The isolation key template to register
            operation_id: The operation ID that owns this key

        Raises:
            ValueError: If the key template already exists with a different operation_id
        """
        with self._internal_lock:
            if isolation_key_template in self._sandbox_key_owner_map:
                existing_op_id = self._sandbox_key_owner_map[isolation_key_template]
                if existing_op_id != operation_id:
                    raise ValueError(
                        f"Isolation key template '{isolation_key_template}' is already registered "
                        f"by operation '{existing_op_id}'. Cannot register operation '{operation_id}' "
                        f"with the same sandbox configuration."
                    )
            self._sandbox_key_owner_map[isolation_key_template] = operation_id
            self._operation_to_key_map[operation_id] = isolation_key_template

    def unregister_sandbox_key(self, isolation_key_template: str) -> None:
        """Unregister an isolation key template.

        Args:
            isolation_key_template: The isolation key template to unregister
        """
        with self._internal_lock:
            operation_id = self._sandbox_key_owner_map.pop(isolation_key_template, None)
            if operation_id:
                self._operation_to_key_map.pop(operation_id, None)

    def unregister_by_operation_id(self, operation_id: str) -> None:
        """Unregister the sandbox key associated with the given operation_id.

        Args:
            operation_id: The operation ID whose sandbox key should be unregistered
        """
        with self._internal_lock:
            isolation_key_template = self._operation_to_key_map.pop(operation_id, None)
            if isolation_key_template:
                self._sandbox_key_owner_map.pop(isolation_key_template, None)

    def get_operation_id_by_key(self, isolation_key_template: str) -> Optional[str]:
        """Get the operation ID that owns the given isolation key template.

        Args:
            isolation_key_template: The isolation key template to look up

        Returns:
            The operation ID if found, None otherwise
        """
        with self._internal_lock:
            return self._sandbox_key_owner_map.get(isolation_key_template)

    def get_sandbox_key_templates(self) -> Dict[str, str]:
        """Get a copy of all registered sandbox key templates.

        Returns:
            Dictionary of all isolation_key_template -> operation_id mappings
        """
        with self._internal_lock:
            return self._sandbox_key_owner_map.copy()

    def clear(self) -> None:
        """Clear all internal state. For testing purposes only."""
        with self._internal_lock:
            self._sys_operations.clear()
            self._sandbox_key_owner_map.clear()
            self._operation_to_key_map.clear()

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance. For testing purposes only."""
        with cls._lock:
            if cls._instance is not None:
                cls._instance.clear()
            cls._instance = None


class ToolIdProxy:
    """A helper for generating tool IDs via attribute access.

    Tool ID format: "{card.id}.{op_type}.{method}"

    Example: card.fs.read_file -> "sys_op.fs.read_file"
    """

    def __init__(self, card_id: str, op_type: str):
        self._card_id = card_id
        self._op_type = op_type

    def __getattr__(self, name: str) -> str:
        return SysOperationCard.generate_tool_id(self._card_id, self._op_type, name)


class SysOperationCard(BaseCard):
    """Configuration card for system operations

    Attributes:
        mode: Operation mode (local or sandbox)
        work_config: Local work configuration (required for local mode)
        gateway_config: Sandbox gateway configuration (required for sandbox mode)

    Examples:
        # 1. Create a sys operation card with local mode
        card = SysOperationCard(
            id="sys_op",
            mode=OperationMode.LOCAL,
            work_config=LocalWorkConfig(work_dir="/tmp/test")
        )

        # 2. Register the operation with resource manager
        Runner.resource_mgr.add_sys_operation(card)

        # 3. Direct call
        op = Runner.resource_mgr.get_sys_operation(card.id)
        await op.fs().write_file("test.txt", "content")

        # 4. Call as LocalFunction Tool (Recommended for Agents)
        tool_id = SysOperationCard.generate_tool_id("sys_op", "fs", "read_file")
        tool = Runner.resource_mgr.get_tool(tool_id)
        await tool.invoke({"path": "test.txt"})
    """
    mode: OperationMode = Field(
        default=OperationMode.LOCAL,
        description="Running mode, available values: local / sandbox"
    )
    work_config: Optional[LocalWorkConfig] = Field(
        default=None,
        description="Local work config (required when mode is local)"
    )
    gateway_config: Optional[SandboxGatewayConfig] = Field(
        default=None,
        description="Sandbox gateway config (required when mode is sandbox)"
    )

    @classmethod
    @field_validator("mode")
    def mode_must_be_valid_enum(cls, v):
        """Validate that mode is a valid value in OperationMode enum"""
        if not isinstance(v, OperationMode):
            try:
                return OperationMode(v.lower())
            except ValueError as ex:
                raise build_error(StatusCode.SYS_OPERATION_CARD_PARAM_ERROR,
                                  error_msg=f"mode must be one of {[e.value for e in OperationMode]}, "
                                            f"current value: {v}",
                                  cause=ex) from ex
        return v

    @property
    def fs(self) -> ToolIdProxy:
        return ToolIdProxy(self.id, "fs")

    @property
    def shell(self) -> ToolIdProxy:
        return ToolIdProxy(self.id, "shell")

    @property
    def code(self) -> ToolIdProxy:
        return ToolIdProxy(self.id, "code")

    def __getattr__(self, name) -> ToolIdProxy:
        """Dynamic access to operation proxies.
        
        Example: card.browser.navigate -> "card_id.browser.navigate"
        """
        # Pydantic handles defined fields; __getattr__ is only called for missing ones.
        return ToolIdProxy(self.id, name)

    @staticmethod
    def generate_tool_id(card_id: str, op_type: str, method_name: str) -> str:
        """Centralized tool ID generation for SysOperation methods.

        Format: "{card_id}.{op_type}.{method_name}"
        """
        return f"{card_id}.{op_type}.{method_name}"


class SysOperation:
    """SysOperation"""

    def __init__(self, card: SysOperationCard):
        self.id = card.id
        self.mode = card.mode
        if self.mode == OperationMode.LOCAL:
            self._run_config = card.work_config or LocalWorkConfig()
        else:
            gateway_config = self._validate_sandbox_gateway_config(card.gateway_config)
            isolation_key_template = generate_isolation_key_template(
                isolation_prefix=gateway_config.isolation.prefix,
                container_scope=gateway_config.isolation.container_scope,
                custom_id=gateway_config.isolation.custom_id,
                launcher_type=gateway_config.launcher_config.launcher_type,
                sandbox_type=gateway_config.launcher_config.sandbox_type,
            )
            self._run_config = SandboxRunConfig(config=gateway_config, isolation_key_template=isolation_key_template)
        self._instances = {}

    def __getattr__(self, name):
        """Dynamic access to operations.
        
        This allows calling custom operations like sys_op.calculator().
        """
        # Ensure operation exists before returning lambda to avoid confusing errors
        if OperationRegistry.get_operation_info(name, self.mode):
            return lambda: self._get_operation(name)
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    def fs(self) -> BaseFsOperation:
        return self._get_operation("fs")

    def code(self) -> BaseCodeOperation:
        return self._get_operation("code")

    def shell(self) -> BaseShellOperation:
        return self._get_operation("shell")

    @staticmethod
    def _validate_sandbox_gateway_config(gateway_config: Optional[SandboxGatewayConfig]) -> SandboxGatewayConfig:
        config = gateway_config or SandboxGatewayConfig()
        launcher_config = config.launcher_config
        if launcher_config is None:
            raise build_error(StatusCode.SYS_OPERATION_CARD_PARAM_ERROR,
                              error_msg="sandbox mode requires launcher_config")
        if launcher_config.launcher_type != "pre_deploy":
            raise build_error(StatusCode.SYS_OPERATION_CARD_PARAM_ERROR,
                              error_msg=f"phase 1 sandbox mode only supports pre_deploy launcher, "
                                        f"current value: {launcher_config.launcher_type}")
        if launcher_config.sandbox_type != "aio":
            raise build_error(StatusCode.SYS_OPERATION_CARD_PARAM_ERROR,
                              error_msg=f"phase 1 sandbox mode only supports aio sandbox_type, "
                                        f"current value: {launcher_config.sandbox_type}")
        return config

    def _get_operation(self, name):
        """get operation"""
        if name in self._instances:
            return self._instances[name]
        operation_def = OperationRegistry.get_operation_info(name, self.mode)
        if operation_def is None:
            return None

        instance = operation_def.create_instance(self._run_config)
        self._instances[name] = instance
        return instance
