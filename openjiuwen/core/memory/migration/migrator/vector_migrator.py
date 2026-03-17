# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import List

from openjiuwen.core.foundation.store.base_vector_store import BaseVectorStore
from openjiuwen.core.memory.manage.mem_model.memory_unit import SupportMemoryType
from openjiuwen.core.memory.migration.operation.base_operation import BaseOperation
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error


class VectorMigrator:
    def __init__(self, vector_store: BaseVectorStore):
        self.vector_store = vector_store

    async def try_migrate(self, entity_key: str, operations: List[BaseOperation]) -> bool:
        """
        Apply the given list of schema operations to every collection
        whose name ends with the specified memory type.

        Parameters
        ----------
        entity_key : str
            Memory-type key (e.g. 'vector_summary') used to discover target collections.
        operations : List[BaseOperation]
            Schema operations to execute on each matched collection.
        """
        collection_names = await self._find_collections(entity_key)
        await self._migrate_collections(collection_names=collection_names,
                                        operations=operations)
        return True

    async def _migrate_collections(self, collection_names: List[str], operations: List[BaseOperation]):
        """
        Applies version-filtered schema migrations to a list of collections.

        This method collects all operations with schema_version greater than the current
        version of each collection and applies them in a single batch operation.
        This reduces the number of data migrations required.
        """
        for collection_name in collection_names:
            # Get collection metadata including schema_version
            metadata = await self.vector_store.get_collection_metadata(collection_name)
            current_version = metadata.get("schema_version", 0)

            # Collect all operations with version > current_version
            operations_to_apply = [
                op for op in operations
                if getattr(op, 'schema_version', 0) > current_version
            ]
            if operations_to_apply:
                # Apply all operations in a single batch
                await self.vector_store.update_schema(
                    collection_name=collection_name,
                    operations=operations_to_apply
                )
                # Update to the max version among applied operations
                max_version = max(getattr(op, 'schema_version', 0) for op in operations_to_apply)
                await self.vector_store.update_collection_metadata(
                    collection_name,
                    {"schema_version": max_version}
                )

    async def _find_collections(self, mem_type_str: str) -> List[str]:
        """
        Discover all vector collections whose names match the pattern
        {user_id}_{scope_id}_{mem_type_str}.

        Parameters
        ----------
        mem_type_str : str
            Either 'vector_summary' or 'vector_user_profile'.  The 'vector_' prefix
            is stripped internally to obtain the raw memory type.

        Returns
        -------
        List[str]
            Collection names that end with the requested memory type.
        """
        # Normalize input
        if mem_type_str.startswith("vector_"):
            mem_type_str = mem_type_str[7:]  # remove 'vector_' prefix

        # Get all supported memory type values
        supported_types = {mt.value for mt in SupportMemoryType}
        
        if mem_type_str not in supported_types:
            raise build_error(
                StatusCode.MEMORY_MIGRATE_MEMORY_EXECUTION_ERROR,
                error_msg=f"Unsupported memory type: '{mem_type_str}'. "
                f"Supported types: {sorted(supported_types)}"
            )

        all_collections = await self.vector_store.list_collection_names()
        suffix = f"_{mem_type_str}"
        matched = [name for name in all_collections if name.endswith(suffix)]
        return matched