# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Callable, Any

from openjiuwen.core.foundation.store.base_vector_store import BaseVectorStore
from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore
from openjiuwen.core.memory.manage.mem_model.sql_db_store import SqlDbStore
from openjiuwen.core.memory.migration.migration_plan import vector_registry, kv_registry, sql_registry
from openjiuwen.core.memory.migration.migrator.sql_migrator import SQLMigrator
from openjiuwen.core.memory.migration.migrator.vector_migrator import VectorMigrator
from openjiuwen.core.memory.migration.migrator.kv_migrator import KVMigrator
from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.common.logging.events import LogEventType
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error


async def _run_migrations_with_registry(
    registry: Any,
    migrator_factory: Callable[[], Any],
    store_name: str
) -> None:
    """
    Generic migration runner that handles common logging and error handling patterns.

    Args:
        registry: The operation registry containing migration operations
        migrator_factory: Factory function to create the migrator instance
        store_name: Name of the store type for logging purposes (e.g., "kv store", "db store", "vector store")
    """
    migrator = migrator_factory()
    registry_map = registry.get_all_operations()

    if not registry_map:
        memory_logger.info(f"No {store_name} migrations registered, skipping migration process",
                           event_type=LogEventType.MEMORY_INIT)
        return

    for entity_key, operations in registry_map.items():
        try:
            success = await migrator.try_migrate(entity_key, operations)
            if not success:
                raise Exception(f"{store_name} migrations failed for entity: {entity_key}")
        except Exception as e:
            memory_logger.error(
                f"Error during {store_name} migration for entity {entity_key}: {str(e)}",
                event_type=LogEventType.MEMORY_INIT,
                exception=str(e)
            )
            raise build_error(
                StatusCode.MEMORY_MIGRATE_MEMORY_EXECUTION_ERROR,
                error_msg=f"{store_name} migrations failed for entity: {entity_key}",
                cause=e
            ) from e


async def run_vector_migrations(vector_store: BaseVectorStore) -> None:
    """
    Apply all registered vector migrations.

    For every (key, operations) pair in vector_registry:
        1. key is treated as the memory-type hint (e.g. 'vector_summary')
        2. VectorMigrator.find_collections(key) discovers all existing
           collections whose names end with that memory type
        3. Each collection is migrated by calling vector_store.update_schema
           with the collection name and the list of operations
    """
    await _run_migrations_with_registry(
        registry=vector_registry,
        migrator_factory=lambda: VectorMigrator(vector_store),
        store_name="vector store"
    )


async def run_kv_migrations(kv_store: BaseKVStore) -> None:
    """
    Apply all registered KV migrations.

    For every (key, operations) pair in kv_registry:
        1. key is treated as the entity identifier (e.g. 'kv_global')
        2. KVMigrator.try_migrate() applies all pending operations to the KV store
        3. Operations are executed in schema_version order
    """
    await _run_migrations_with_registry(
        registry=kv_registry,
        migrator_factory=lambda: KVMigrator(kv_store),
        store_name="kv store"
    )


async def run_sql_migrations(sql_db_store: SqlDbStore) -> None:
    """
    Apply all registered SQL migrations.

    For every (key, operations) pair in sql_registry:
        1. key is treated as the entity identifier (e.g. 'user_messages')
        2. SQLMigrator.try_migrate() applies all pending operations to the table
        3. Operations are executed in schema_version order
    """
    await _run_migrations_with_registry(
        registry=sql_registry,
        migrator_factory=lambda: SQLMigrator(sql_db_store),
        store_name="db store"
    )
