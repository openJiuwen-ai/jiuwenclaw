# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Tuple
from openjiuwen.core.memory.manage.mem_model.memory_unit import MemoryType

from openjiuwen.core.memory.manage.index.base_memory_manager import BaseMemoryManager
from openjiuwen.core.memory.manage.mem_model.memory_unit import BaseMemoryUnit
from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.memory.manage.mem_model.user_mem_store import UserMemStore
from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.common.logging.events import LogEventType


class WriteManager:
    def __init__(self, managers: dict[str, BaseMemoryManager], mem_store: UserMemStore):
        self.managers = managers
        self.mem_store = mem_store
        
    async def add_memories(self, user_id: str, scope_id: str, memories: dict[str, list[BaseMemoryUnit]],
                           llm: Model | None, semantic_store) -> None:
        if not memories:
            memory_logger.debug("No memory units to add", event_type=LogEventType.MEMORY_STORE)
            return

        for manager in set(self.managers.values()):
            try:
                await manager.add_memories(
                    user_id, scope_id, memories, llm, semantic_store=semantic_store)
            except Exception as e:
                memory_logger.error(
                    "Failed to add mem",
                    exception=str(e),
                    memory_type=manager.mem_type,
                    event_type=LogEventType.MEMORY_STORE,
                )
                raise e


    async def update_mem_by_id(self, user_id: str, scope_id: str, mem_id: str, memory: str, semantic_store):
        mem_type = await self.__get_mem_type_from_store(user_id, scope_id, mem_id)
        if mem_type is None:
            memory_logger.warning(
                "Skipping this update due to failure in getting memory type",
                memory_type=mem_type,
                memory_id=[mem_id],
                event_type=LogEventType.MEMORY_STORE,
                user_id=user_id,
                scope_id=scope_id,
            )
            return
        await self.managers[mem_type].update(user_id, scope_id, mem_id, memory, semantic_store=semantic_store)

    async def delete_mem_by_id(self, user_id: str, scope_id: str, mem_id: str, semantic_store):
        mem_type = await self.__get_mem_type_from_store(user_id, scope_id, mem_id)
        if mem_type is None:
            memory_logger.warning(
                "Skipping this deletion due to failure in getting memory type",
                memory_type=mem_type,
                memory_id=[mem_id],
                event_type=LogEventType.MEMORY_STORE,
                user_id=user_id,
                scope_id=scope_id
            )
            return
        await self.managers[mem_type].delete(user_id, scope_id, mem_id, semantic_store=semantic_store)

    async def delete_mem_by_user_id(self, user_id: str, scope_id: str, semantic_store):
        for manager in self.managers:
            await self.managers[manager].delete_by_user_id(user_id=user_id,
                                                           scope_id=scope_id,
                                                           semantic_store=semantic_store)

    async def __get_mem_type_from_store(self, user_id: str, scope_id: str, mem_id: str) -> str | None:
        data = None
        try:
            data = await self.mem_store.get(user_id=user_id, scope_id=scope_id, mem_id=mem_id)
        except Exception as e:
            memory_logger.error(
                "Failed to get memory",
                memory_id=[mem_id],
                event_type=LogEventType.MEMORY_STORE,
                user_id=user_id,
                scope_id=scope_id,
                exception=str(e)
            )
            return None
        if data is None:
            memory_logger.warning(
                "Nonexistent memory",
                memory_id=[mem_id],
                event_type=LogEventType.MEMORY_STORE,
                user_id=user_id,
                scope_id=scope_id,
            )
            return None
        if "mem_type" not in data:
            memory_logger.warning(
                "The mem_type field doesn't exist",
                memory_id=[mem_id],
                event_type=LogEventType.MEMORY_STORE,
                user_id=user_id,
                scope_id=scope_id,
            )
            return None
        mem_type = data['mem_type']
        if mem_type not in self.managers:
            memory_logger.warning(
                "Unsupported mem_type",
                memory_id=[mem_id],
                memory_type=mem_type,
                event_type=LogEventType.MEMORY_STORE,
                user_id=user_id,
                scope_id=scope_id
            )
            return None
        return mem_type