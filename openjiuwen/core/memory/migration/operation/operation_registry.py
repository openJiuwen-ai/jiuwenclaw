# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Dict, List

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.memory.migration.operation.base_operation import BaseOperation


class OperationRegistry:
    """
    Registry that manages chained upgrade operations by entity_key.

    Conventions:
        - entity_key is a string, for example:
            "user_messages"
            "vector_summary"
            "kv_global"
        - For the same entity_key:
            - All Operations are monotonically increasing by schema_version.
    """

    def __init__(self) -> None:
        # entity_key -> List[BaseOperation]（ascending order of schema_version）
        self._operations: Dict[str, List[BaseOperation]] = {}

    def register(self, entity_key: str, op: BaseOperation):
        """
        Register an Operation and ensure schema versions are monotonically increasing.

        Constraints:
        - If this is the first time the entity_key appears, a new list is created.
        - Otherwise, op.schema_version must be strictly greater than the
          schema_version of the last Operation in the list.
        - If the constraint is violated, a ValueError is raised (via build_error).

        Args:
            entity_key (str): The key identifying the entity (e.g., "user_messages").
            op (BaseOperation): The Operation instance to register.

        Raises:
            MEMORY_REGISTER_OPERATION_VALIDATION_INVALID: If the schema_version of the new Operation is less than
                or equal to the current maximum schema_version for this entity_key.
        """
        ops = self._operations.get(entity_key)

        # First registration for this entity_key: create the list directly
        if ops is None:
            self._operations[entity_key] = [op]
            return

        # Existing records: check whether the new version is > current maximum (last one)
        last_version = ops[-1].schema_version
        if op.schema_version <= last_version:
            raise build_error(
                StatusCode.MEMORY_REGISTER_OPERATION_VALIDATION_INVALID,
                entity_key=entity_key,
                schema_version=op.schema_version,
                error_msg="the schema number of the new operation must be greater than the current maximum"
            )

        # Constraint satisfied: append to the tail; the list naturally remains sorted
        ops.append(op)

    def get_operations(
        self,
        entity_key: str,
        from_version: int,
        to_version: int,
    ) -> List[BaseOperation]:
        """
        Get all Operations for an entity within the [from_version, to_version] range.

        Version constraints:
            - to_version must be explicitly provided (no default value).
            - If from_version is greater than to_version, an empty list is returned.
            - If the entity has no registered Operations, an empty list is returned.

        Args:
            entity_key (str): The key identifying the entity.
            from_version (int): The lower bound (inclusive) of schema_version to filter from.
            to_version (int): The upper bound (inclusive) of schema_version to filter to.

        Returns:
            List[BaseOperation]: A list of Operations sorted by schema_version in ascending order.
        """
        if from_version > to_version:
            return []

        ops = self._operations.get(entity_key, [])
        if not ops:
            # No registered Operations for this entity; return an empty list
            return []

        # The list is already sorted by schema_version
        return [
            op for op in ops
            if from_version <= op.schema_version <= to_version
        ]

    def get_current_version(self, entity_key: str) -> int:
        """
        Get the current latest schema_version for the given entity.

        The current version is derived from the last registered Operation for the specified entity_key.

        Args:
            entity_key (str): The key identifying the entity.

        Returns:
            int: The latest schema_version registered for this entity_key;
            returns 0 if no Operation has ever been registered.
        """
        ops = self._operations.get(entity_key, [])
        return ops[-1].schema_version if ops else 0

    def get_all_entities(self) -> List[str]:
        """
        Get a list of all registered entity keys.

        Returns:
            List[str]: A list containing all registered entity keys.
        """
        return list(self._operations.keys())

    def get_all_operations(self) -> Dict[str, List[BaseOperation]]:
        """
        Get a shallow copy of the internal operation mapping.

        Returns:
            Dict[str, List[BaseOperation]]: A dictionary mapping entity keys to their corresponding operation lists.
        """
        return self._operations.copy()

    def clear(self) -> None:
        """
        Clear all registered operations.

        This method is primarily used for testing purposes to reset the registry state.
        """
        self._operations.clear()

    def set_operations(self, operations: Dict[str, List[BaseOperation]]) -> None:
        """
        Set the internal operations mapping.

        This method is primarily used for testing purposes to restore the registry state.

        Args:
            operations (Dict[str, List[BaseOperation]]): The operations mapping to set.
        """
        self._operations = operations
