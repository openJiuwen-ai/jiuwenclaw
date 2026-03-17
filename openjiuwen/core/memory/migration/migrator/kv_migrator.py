# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
import time
from typing import List

from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore
from openjiuwen.core.memory.migration.operation.base_operation import BaseOperation
from openjiuwen.core.memory.migration.operation.operations import UpdateKVOperation
from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.common.logging.events import LogEventType
from openjiuwen.core.memory.migration.migration_plan import kv_registry
from openjiuwen.core.memory.common.kv_prefix_registry import kv_prefix_registry


KV_SCHEMA_VERSION = "MEMORY_MIGRATION_KV_SCHEMA_VERSION"
KV_ENTITY_KEY = "kv_global"


class KVMigrator:
    def __init__(self, kv_store: BaseKVStore):
        self.kv_store = kv_store

    async def try_migrate(self, entity_key: str, operations: List[BaseOperation]) -> bool:
        """
        Try to migrate KV data to target version by applying operations.

        This method:
        1. Validates that entity_key matches KV_ENTITY_KEY
        2. Validates that all operations have schema_version in ascending order
        3. Compares the last operation's schema_version with current store version
        4. Only executes migration if needed
        5. Creates backup before migration and rolls back on failure

        Args:
            entity_key: Entity key to validate (must be KV_ENTITY_KEY)
            operations: List of migration operations to execute

        Returns:
            bool: True if migration succeeded or not needed, yet False otherwise
        """
        if entity_key != KV_ENTITY_KEY:
            memory_logger.error(
                f"Unsupported entity_key: '{entity_key}'. Expected: '{KV_ENTITY_KEY}'",
                event_type=LogEventType.MEMORY_INIT
            )
            return False
        if not operations:
            return True

        if not self._validate_operations_order(operations):
            memory_logger.error(
                f"Operations are not in ascending order by schema_version",
                event_type=LogEventType.MEMORY_INIT
            )
            return False

        current_version = await self._get_current_version()
        last_operation_version = operations[-1].schema_version
        if current_version is not None and current_version >= last_operation_version:
            memory_logger.info(
                f"Current version {current_version} is already >= last operation version {last_operation_version}, "
                f"no migration needed",
                event_type=LogEventType.MEMORY_INIT
            )
            return True

        pending_operations = [
            op for op in operations
            if current_version is None or op.schema_version > current_version
        ]
        if not pending_operations:
            return True
        memory_logger.info(
            f"Found {len(pending_operations)} pending operations to execute", event_type=LogEventType.MEMORY_INIT)

        backup_key = None
        try:
            backup_key = await self._create_backup(current_version)

            last_version = current_version
            for idx, operation in enumerate(pending_operations, 1):
                memory_logger.info(
                    f"Executing operation {idx}/{len(pending_operations)}: "
                    f"{operation.__class__.__name__} (schema_version={operation.schema_version})",
                    event_type=LogEventType.MEMORY_INIT
                )
                await self._execute_operation(operation)
                last_version = operation.schema_version
                memory_logger.info(
                    f"Successfully executed operation {operation.__class__.__name__} "
                    f"with schema_version {operation.schema_version}",
                    event_type=LogEventType.MEMORY_INIT
                )

            if last_version != current_version:
                await self._update_version(last_version)
                memory_logger.info(
                    f"KV schema version updated from {current_version} to {last_version}",
                    event_type=LogEventType.MEMORY_INIT
                )

            await self._cleanup_backup(backup_key)
            return True

        except Exception as e:
            memory_logger.error(
                f"Error during KV migration: {str(e)}", event_type=LogEventType.MEMORY_INIT, exception=str(e))
            if backup_key:
                await self._restore_from_backup(backup_key)
            return False

    async def _get_current_version(self) -> int | None:
        """
        Get current schema version from KV store.

        Returns:
            int | None: Current version number or None if not set

        Raises:
            ValueError: If SCHEMA_VERSION field exists but contains invalid format (non-numeric string)
        """
        version_value = await self.kv_store.get(KV_SCHEMA_VERSION)
        if version_value is None:
            has_memory_data = await self._has_memory_module_data()
            
            if not has_memory_data:
                # KV store is newly initialized (no memory module data found).
                # Set KV_SCHEMA_VERSION to {initial_version} from kv_registry.
                initial_version = kv_registry.get_current_version(KV_ENTITY_KEY)
                await self._update_version(initial_version)
                return initial_version
            else:
                # KV store contains memory module data but KV_SCHEMA_VERSION key is missing.
                # This indicates old data that has never been migrated. Returning None to trigger migration.
                return None

        if isinstance(version_value, str):
            if version_value.isdigit():
                return int(version_value)
            else:
                error_msg = f"Invalid SCHEMA_VERSION format: '{version_value}'. Expected numeric string or integer."
                memory_logger.error(error_msg, event_type=LogEventType.MEMORY_INIT)
                raise ValueError(error_msg)

        if isinstance(version_value, int):
            return version_value

        error_msg = f"Invalid SCHEMA_VERSION type: {type(version_value).__name__}. Expected string or integer."
        memory_logger.error(error_msg, event_type=LogEventType.MEMORY_INIT)
        raise ValueError(error_msg)

    async def _has_memory_module_data(self) -> bool:
        """
        Check if the KV store contains any data from memory modules.
        
        This is used to distinguish between:
        1. A newly initialized KV store (no memory data)
        2. An old store with data that has never been migrated (has memory data but no version key)
        
        Returns:
            bool: True if any memory module data is found, False otherwise
        """
        prefixes = kv_prefix_registry.get_all_prefixes()
        for prefix in prefixes:
            data = await self.kv_store.get_by_prefix(prefix)
            if data:
                return True
        return False

    async def _update_version(self, version: int) -> None:
        """
        Update schema version in KV store.

        Args:
            version: New version number to store
        """
        try:
            await self.kv_store.set(KV_SCHEMA_VERSION, str(version))
        except Exception as e:
            memory_logger.error(f"Error updating version to {version}: {str(e)}", event_type=LogEventType.MEMORY_INIT)
            raise

    async def _execute_operation(self, operation: BaseOperation) -> None:
        """
        Execute a single migration operation.

        Args:
            operation: The operation to execute

        Raises:
            ValueError: If operation type is not supported
            Exception: If operation execution fails
        """
        if isinstance(operation, UpdateKVOperation):
            await operation.update_func(self.kv_store)
        else:
            raise ValueError(f"Unsupported operation type: {operation.__class__.__name__}")

    @staticmethod
    def _validate_operations_order(operations: List[BaseOperation]) -> bool:
        """
        Validate that operations are in ascending order by schema_version.

        Args:
            operations: List of migration operations to validate

        Returns:
            bool: True if operations are in ascending order, False otherwise
        """
        for i in range(len(operations) - 1):
            if operations[i].schema_version >= operations[i + 1].schema_version:
                return False
        return True

    async def _create_backup(self, current_version: int | None) -> str:
        """
        Create a backup of all KV data before migration.

        Args:
            current_version: Current schema version before migration

        Returns:
            str: The backup key used to identify this backup
        """
        backup_key = f"{KV_SCHEMA_VERSION}_BACKUP_{int(time.time() * 1000)}"
        backup_data = {}
        prefixes = kv_prefix_registry.get_all_prefixes()

        for prefix in prefixes:
            data = await self.kv_store.get_by_prefix(prefix)
            if data:
                backup_data.update(data)

        if current_version is not None:
            version_value = await self.kv_store.get(KV_SCHEMA_VERSION)
            if version_value is not None:
                backup_data[KV_SCHEMA_VERSION] = version_value

        if backup_data:
            backup_json = json.dumps(backup_data)
            await self.kv_store.set(backup_key, backup_json)

        memory_logger.info(f"Created backup with key: {backup_key}", event_type=LogEventType.MEMORY_INIT)
        return backup_key

    async def _restore_from_backup(self, backup_key: str) -> None:
        """
        Restore KV data from backup.

        Args:
            backup_key: The backup key to restore from
        """
        backup_json = await self.kv_store.get(backup_key)
        if backup_json is None:
            memory_logger.error(f"Backup not found: {backup_key}", event_type=LogEventType.MEMORY_INIT)
            return

        try:
            backup_data = json.loads(backup_json)
            prefixes = kv_prefix_registry.get_all_prefixes()

            for prefix in prefixes:
                await self.kv_store.delete_by_prefix(prefix)

            for key, value in backup_data.items():
                await self.kv_store.set(key, value)

            memory_logger.info(
                f"Successfully restored {len(backup_data)} keys from backup", event_type=LogEventType.MEMORY_INIT)

        except json.JSONDecodeError as e:
            memory_logger.error(f"Failed to decode backup data: {str(e)}", event_type=LogEventType.MEMORY_INIT)
            raise

    async def _cleanup_backup(self, backup_key: str) -> None:
        """
        Clean up backup data after successful migration.

        Args:
            backup_key: The backup key to clean up
        """
        try:
            await self.kv_store.delete(backup_key)
            memory_logger.info(f"Cleaned up backup: {backup_key}", event_type=LogEventType.MEMORY_INIT)
        except Exception as e:
            memory_logger.warning(
                f"Failed to cleanup backup {backup_key}: {str(e)}",
                event_type=LogEventType.MEMORY_INIT
            )
