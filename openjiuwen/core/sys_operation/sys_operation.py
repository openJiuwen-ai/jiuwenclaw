# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from typing import Optional
from pydantic import Field, field_validator

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.schema import BaseCard
from openjiuwen.core.sys_operation.base import OperationMode
from openjiuwen.core.sys_operation.code import BaseCodeOperation
from openjiuwen.core.sys_operation.config import LocalWorkConfig, SandboxGatewayConfig
from openjiuwen.core.sys_operation.fs import BaseFsOperation
from openjiuwen.core.sys_operation.registry import OperationRegistry
from openjiuwen.core.sys_operation.shell import BaseShellOperation


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
        self.mode = card.mode
        if self.mode == OperationMode.LOCAL:
            self._run_config = card.work_config or LocalWorkConfig()
        else:
            self._run_config = card.gateway_config or SandboxGatewayConfig()
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
