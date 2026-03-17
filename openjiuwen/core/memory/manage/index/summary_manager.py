# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from datetime import datetime, timezone
from typing import Any, List, Tuple

from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.memory.manage.mem_model.semantic_store import SemanticStore
from openjiuwen.core.memory.common.base import generate_idx_name, parse_memory_hit_infos
from openjiuwen.core.memory.manage.index.base_memory_manager import BaseMemoryManager
from openjiuwen.core.memory.manage.mem_model.memory_unit import SummaryUnit, BaseMemoryUnit, MemoryType
from openjiuwen.core.memory.manage.mem_model.user_mem_store import UserMemStore
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.common.logging.events import LogEventType


class SummaryManager(BaseMemoryManager):
    """Manages summary memory CRUD with encryption and vector storage"""

    def __init__(self,
                 user_mem_store: UserMemStore,
                 crypto_key: bytes):
        self.mem_store = user_mem_store
        self.crypto_key = crypto_key
        self.mem_type = MemoryType.SUMMARY.value

    async def add_memories(self, user_id: str, scope_id: str, memories: dict[str, list[BaseMemoryUnit]],
                           llm: Tuple[str, Model] | None = None, **kwargs):
        """add memories in batch."""
        semantic_store = self._get_semantic_store("add", **kwargs)
        for mem_type, memory in memories.items():
            if mem_type != self.mem_type:
                continue
            for unit in memory:
                if not isinstance(unit, SummaryUnit):
                    memory_logger.warning(
                        "mem_unit is not a SummaryUnit",
                        event_type=LogEventType.MEMORY_STORE,
                        memory_type=self.mem_type,
                        user_id=user_id,
                        scope_id=scope_id
                    )
                    continue
                vector_success = await self._add_summary_memory_to_vector(
                    summary_unit=unit,
                    user_id=user_id,
                    scope_id=scope_id,
                    semantic_store=semantic_store
                )
                if not vector_success:
                    raise build_error(
                        StatusCode.MEMORY_ADD_MEMORY_EXECUTION_ERROR,
                        memory_type=self.mem_type,
                        error_msg="summary add to vector store failed",
                    )
                await self._add_summary_memory_to_mem_store(user_id, scope_id, unit)

    async def update(self, user_id: str, scope_id: str, mem_id: str, new_memory: str, **kwargs):
        """update memory by its id."""
        semantic_store = self._get_semantic_store("update", **kwargs)
        time = datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M:%S')
        encrypt_new_memory = BaseMemoryManager.encrypt_memory_if_needed(key=self.crypto_key, plaintext=new_memory)
        new_data = {'mem': encrypt_new_memory, 'time': time}
        await self.mem_store.update(mem_id=mem_id, user_id=user_id, scope_id=scope_id, data=new_data)
        table_name = generate_idx_name(usr_id=user_id, scope_id=scope_id, mem_type=MemoryType.SUMMARY.value)
        await semantic_store.delete_docs([mem_id], table_name)
        # semantic memory embedding must not encrypt
        await semantic_store.add_docs([(mem_id, new_memory)], table_name)
        return True

    async def delete(self, user_id: str, scope_id: str, mem_id: str, **kwargs):
        """delete memory by its id."""
        semantic_store = self._get_semantic_store("delete", **kwargs)
        data = await self.mem_store.get(user_id=user_id, scope_id=scope_id, mem_id=mem_id)
        if data is None:
            memory_logger.error(
                "Delete summary in store failed, the mem of mem_id is not exist.",
                event_type=LogEventType.MEMORY_STORE,
                memory_id=mem_id,
                user_id=user_id,
                scope_id=scope_id
            )
            return False
        await self.mem_store.delete(mem_id=mem_id, user_id=user_id, scope_id=scope_id)
        await self._delete_vector_summary_memory(memory_id=[mem_id],
                                                 user_id=user_id,
                                                 scope_id=scope_id,
                                                 semantic_store=semantic_store)
        return True

    async def delete_by_user_id(self, user_id: str, scope_id: str, **kwargs):
        """delete memory by user id and app id."""
        semantic_store = self._get_semantic_store("delete", **kwargs)
        data = await self.mem_store.get_all(user_id=user_id, scope_id=scope_id, mem_type=MemoryType.SUMMARY.value)
        if data is None:
            memory_logger.error(
                "Delete summary in store failed, the mem of user_id is not exist.",
                event_type=LogEventType.MEMORY_STORE,
                user_id=user_id,
                scope_id=scope_id
            )
            return False
        mem_ids = [item['id'] for item in data]
        await self.mem_store.batch_delete(user_id=user_id, scope_id=scope_id, mem_ids=mem_ids)
        await self._delete_vector_store_table(user_id=user_id,
                                              scope_id=scope_id,
                                              semantic_store=semantic_store)
        return True

    async def get(self, user_id: str, scope_id: str, mem_id: str) -> dict[str, Any] | None:
        """get memory by its id."""
        retrieve_res = await self.mem_store.get(user_id=user_id, scope_id=scope_id, mem_id=mem_id)
        retrieve_res["mem"] = BaseMemoryManager.decrypt_memory_if_needed(key=self.crypto_key,
                                                                         ciphertext=retrieve_res["mem"])
        return retrieve_res

    async def search(self, user_id: str, scope_id: str, query: str, top_k: int, **kwargs):
        """query memory, return top k results"""
        semantic_store = self._get_semantic_store("search", **kwargs)
        mem_ids, scores = await self._recall_by_vector(query, user_id, scope_id, semantic_store, top_k)
        retrieve_res = await self.mem_store.batch_get(user_id=user_id, scope_id=scope_id, mem_ids=mem_ids)
        if retrieve_res is None:
            return None
        for item in retrieve_res:
            item["score"] = scores.get(item['id'], 0)
            item["mem"] = BaseMemoryManager.decrypt_memory_if_needed(key=self.crypto_key, ciphertext=item["mem"])

        retrieve_res.sort(key=lambda x: x["score"], reverse=True)
        return retrieve_res

    async def _add_summary_memory_to_mem_store(self, user_id: str, scope_id: str, summary_unit: SummaryUnit):
        """Encrypt and write summary memory to persistent storage."""
        mem = BaseMemoryManager.encrypt_memory_if_needed(key=self.crypto_key,
                                                         plaintext=summary_unit.summary)
        data = {
            'id': summary_unit.mem_id,
            'user_id': user_id or '',
            'scope_id': scope_id or '',
            'mem': mem,
            'source_id': summary_unit.message_mem_id,
            'mem_type': self.mem_type,
            'timestamp': summary_unit.timestamp
        }
        await self.mem_store.write(user_id=user_id,
                                   scope_id=scope_id,
                                   mem_id=summary_unit.mem_id,
                                   data=data)

    async def _add_summary_memory_to_vector(
        self,
        summary_unit: SummaryUnit,
        user_id: str,
        scope_id: str,
        semantic_store: SemanticStore
    ) -> bool:
        """Add plaintext summary to vector store for semantic recall."""
        if not semantic_store:
            raise build_error(
                StatusCode.MEMORY_ADD_MEMORY_EXECUTION_ERROR,
                memory_type=self.mem_type,
                error_msg="vector store must not be None",
            )
        table_name = generate_idx_name(usr_id=user_id,
                                       scope_id=scope_id,
                                       mem_type=self.mem_type)
        return await semantic_store.add_docs(
            docs=[(summary_unit.mem_id, summary_unit.summary)],
            table_name=table_name,
            scope_id=scope_id
        )

    async def _delete_vector_summary_memory(
            self,
            user_id: str,
            scope_id: str,
            memory_id: List[str],
            semantic_store: SemanticStore
    ):
        """Delete summary memory from vector store by IDs."""
        if not semantic_store:
            raise build_error(
                StatusCode.MEMORY_DELETE_MEMORY_EXECUTION_ERROR,
                memory_type=self.mem_type,
                error_msg="vector store must not be None",
            )
        table_name = generate_idx_name(usr_id=user_id, scope_id=scope_id, mem_type=self.mem_type)
        await semantic_store.delete_docs(memory_id, table_name)

    async def _recall_by_vector(
            self,
            query: str,
            user_id: str,
            scope_id: str,
            semantic_store: SemanticStore,
            top_k: int = 5,
    ) -> tuple[List[str], dict[str, float]]:
        """Semantic recall summary memory IDs and similarity scores."""
        table_name = generate_idx_name(usr_id=user_id, scope_id=scope_id, mem_type=MemoryType.SUMMARY.value)
        memory_hit_info = await semantic_store.search(query=query, table_name=table_name,
                                                      scope_id=scope_id, top_k=top_k)
        return parse_memory_hit_infos(memory_hit_info)

    async def _delete_vector_store_table(
            self,
            user_id: str,
            scope_id: str,
            semantic_store: SemanticStore
    ):
        """Delete entire vector table for user + scope summary memory."""
        if not semantic_store:
            raise build_error(
                StatusCode.MEMORY_DELETE_MEMORY_EXECUTION_ERROR,
                memory_type=self.mem_type,
                error_msg="vector store must not be None",
            )
        table_name = generate_idx_name(usr_id=user_id, scope_id=scope_id, mem_type=self.mem_type)
        await semantic_store.delete_table(table_name=table_name)

    def _get_semantic_store(self, operation_type: str, **kwargs) -> SemanticStore:
        """
        Get semantic store from kwargs or raise appropriate error based on operation type.

        Args:
            operation_type: Type of operation being performed ("add", "update", "delete", "search")
            **kwargs: Keyword arguments containing semantic_store

        Returns:
            SemanticStore: The semantic store instance

        Raises:
            Error: If semantic_store is not provided
        """
        semantic_store = kwargs.get('semantic_store')
        if not semantic_store:
            error_codes = {
                "add": StatusCode.MEMORY_ADD_MEMORY_EXECUTION_ERROR,
                "update": StatusCode.MEMORY_UPDATE_MEMORY_EXECUTION_ERROR,
                "delete": StatusCode.MEMORY_DELETE_MEMORY_EXECUTION_ERROR,
                "search": StatusCode.MEMORY_GET_MEMORY_EXECUTION_ERROR,
            }
            error_code = error_codes.get(operation_type, StatusCode.MEMORY_ADD_MEMORY_EXECUTION_ERROR)
            raise build_error(
                error_code,
                memory_type=self.mem_type,
                error_msg="semantic_store is required",
            )
        return semantic_store
