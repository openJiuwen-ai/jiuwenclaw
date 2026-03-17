# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import importlib
import pkgutil
from dataclasses import dataclass
from typing import Dict, Type, Optional, List

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.sys_operation.base import BaseOperation, OperationMode


@dataclass
class OperationDef:
    """Definition and factory for an operation."""
    cls: Type[BaseOperation]
    description: str
    name: str
    mode: OperationMode

    def create_instance(self, run_config) -> BaseOperation:
        """Create an operation instance with the given configuration."""
        return self.cls(self.name, self.mode, self.description, run_config)


class OperationRegistry:
    """Operation registry that auto-discovers operations via package scanning."""

    # Storage: mode -> name -> OperationDef
    _repository: Dict[OperationMode, Dict[str, OperationDef]] = {}

    @classmethod
    def register(cls, operation_cls: Type[BaseOperation], *,
                 name: Optional[str] = None,
                 mode: Optional[OperationMode] = None,
                 description: Optional[str] = None):
        """Register an operation.
        Allows users to add new operation types or override built-in ones

        Args:
            operation_cls: The class implementing the operation logic.
            name: Unique identifier for the operation (e.g., "fs", "shell", "code").
            mode: Running mode (e.g., OperationMode.LOCAL or OperationMode.SANDBOX).
            description: Optional human-readable description.

        Example:
            # 1. Custom operation implementation
            @operation(name="fs", mode=OperationMode.LOCAL, description="Custom FS override test")
            class MyFsOperation(BaseOperation):
                def list_tools(self) -> List[ToolCard]:
                    return self._generate_tool_cards(["read_file"])

                async def read_file(self, path: str) -> str:
                    return f"custom_content_of_{path}"

            # 2. Register to override built-in 'fs' for local mode
            OperationRegistry.register(MyFsOperation)

            # 3. Use via SysOperation
            card = SysOperationCard(id="my_op", mode=OperationMode.LOCAL)
            Runner.resource_mgr.add_sys_operation(card)
            sys_op = Runner.resource_mgr.get_sys_operation(card.id)

            # This calls MyFsOperation.read_file
            content = await sys_op.fs().read_file("test.txt")
        """
        # 1. Try to extract metadata from class attribute if not provided
        op_def = getattr(operation_cls, "op_def", None)
        name = name or (op_def.name if op_def else None)
        mode = mode or (op_def.mode if op_def else None)
        description = description if description is not None else (op_def.description if op_def else "")

        if not name or not mode:
            raise build_error(StatusCode.SYS_OPERATION_REGISTRY_ERROR, process="register",
                              error_msg=f"Operation name and mode must be provided for {operation_cls.__name__} "
                                        "either as arguments or via @operation decorator.")

        # 2. Ensure built-in operations for this mode are loaded before we add ours (idempotent)
        cls._load_build_in_operation(mode)

        # 3. Idempotency check: Skip if already registered with exact same metadata
        new_def = OperationDef(cls=operation_cls, description=description, name=name, mode=mode)
        existing_def = cls._repository[mode].get(name)
        if existing_def == new_def:
            return

        # 4. Store the definition
        cls._repository[mode][name] = OperationDef(
            cls=operation_cls,
            description=description,
            name=name,
            mode=mode
        )

    @classmethod
    def get_operation_info(cls, name: str, mode: OperationMode) -> Optional[OperationDef]:
        """Get operation information. Attempts lazy loading if not found."""
        cls._load_build_in_operation(mode)
        return cls._repository.get(mode, {}).get(name)

    @classmethod
    def get_supported_operations(cls, mode: OperationMode) -> List[str]:
        """Get list of supported operation names for the given mode."""
        cls._load_build_in_operation(mode)
        return sorted(list(cls._repository.get(mode, {}).keys()))

    @classmethod
    def _load_build_in_operation(cls, mode: OperationMode):
        """Ensure built-in operations for the given mode are discovered."""
        if mode not in cls._repository:
            # Mark as "discovery in progress" to avoid infinite recursion
            cls._repository[mode] = {}
            cls._discover_package(f"openjiuwen.core.sys_operation.{mode.value}")

    @classmethod
    def _discover_package(cls, package_name: str):
        """Discover and register operations in a package via @operation decorators."""
        try:
            pkg = importlib.import_module(package_name)
            if not hasattr(pkg, "__path__"):
                return
            for _, modname, ispkg in pkgutil.walk_packages(pkg.__path__, prefix=f"{package_name}."):
                if not ispkg:
                    try:
                        importlib.import_module(modname)
                    except Exception as e:
                        raise build_error(StatusCode.SYS_OPERATION_REGISTRY_ERROR, process="register",
                                          error_msg=f"Import operation: {modname} error", cause=e) from e
        except ImportError:
            pass


def operation(name: str, mode: OperationMode, description: str = ""):
    """Decorator for registering a class as an operation in the OperationRegistry.

    Args:
        name: Unique identifier for the operation.
        mode: Running mode associated with the operation.
        description: Human-readable description of the operation, default value is "".

    Returns:
        Stores metadata on the class to allow automatic registration during discovery.
    """

    def decorator(cls):
        # Attach metadata to class for Registry to pick up
        cls.op_def = OperationDef(cls=cls, name=name, mode=mode, description=description)
        OperationRegistry.register(cls)
        return cls

    return decorator
