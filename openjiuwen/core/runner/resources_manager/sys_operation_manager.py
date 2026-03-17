# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from typing import Optional

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.runner.resources_manager.thread_safe_dict import ThreadSafeDict
from openjiuwen.core.sys_operation import SysOperation


class SysOperationMgr:
    """Manager for SysOperation instances"""

    def __init__(self):
        self._sys_operations: ThreadSafeDict[str, SysOperation] = ThreadSafeDict()

    def add_sys_operation(self, sys_operation_id: str, sys_operation_instance: SysOperation):
        """Add a new system operation instance to the system operation registry.

        Args:
            sys_operation_id: Unique identifier for the system operation. Must be non-duplicate in the registry.
            sys_operation_instance: System operation instance object (of SysOperation type),
                containing the specific configuration and implementation of the operation.

        Raises:
            Exception: Raised with status code StatusCode.SYS_OPERATION_MANAGER_PROCESS_ERROR:
                1. The input sys_operation_id is empty (None or empty string)
                2. The input sys_operation_instance is None
                3. The sys_operation_id already exists in the system operation registry (duplicate ID)
        """
        if sys_operation_id is None:
            raise build_error(StatusCode.SYS_OPERATION_MANAGER_PROCESS_ERROR, process="add",
                              error_msg="sys_operation_id can not be none")
        if sys_operation_id in self._sys_operations:
            raise build_error(StatusCode.SYS_OPERATION_MANAGER_PROCESS_ERROR, process="add",
                              error_msg=f"already exists sys_operation_card {sys_operation_id}")
        self._sys_operations[sys_operation_id] = sys_operation_instance

    def remove_sys_operation(self, sys_operation_id: str) -> Optional[SysOperation]:
        """Unregister and remove the SysOperation by its unique ID.

        Args:
            sys_operation_id: Unique string ID of the system operation to remove.

        Returns:
            The removed `SysOperation` instance if the ID exists in the registry; `None` otherwise.

        Raises:
            Exception: Raised with `StatusCode.SYS_OPERATION_MANAGER_PROCESS_ERROR` if `sys_operation_id` is None.
        """
        if sys_operation_id is None:
            raise build_error(StatusCode.SYS_OPERATION_MANAGER_PROCESS_ERROR, process="remove",
                              error_msg="sys_operation_id can not be none")
        return self._sys_operations.pop(sys_operation_id, None)

    def get_sys_operation(self, sys_operation_id: str) -> Optional[SysOperation]:
        """Retrieve the registered SysOperation instance by its unique ID.

        Args:
            sys_operation_id: Unique string ID of the system operation to retrieve.

        Returns:
            The `SysOperation` instance associated with the ID if found; `None` otherwise.

        Raises:
            Exception: Raised with `StatusCode.SYS_OPERATION_MANAGER_PROCESS_ERROR` if `sys_operation_id` is None.
        """
        if sys_operation_id is None:
            raise build_error(StatusCode.SYS_OPERATION_MANAGER_PROCESS_ERROR, process="get",
                              error_msg="sys_operation_id can not be none")
        return self._sys_operations.get(sys_operation_id)
