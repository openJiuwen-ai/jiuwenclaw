# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from pydantic import BaseModel, Field
from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.memory.common.base import generate_idx_name, parse_memory_hit_infos
from openjiuwen.core.memory.manage.index.base_memory_manager import BaseMemoryManager
from openjiuwen.core.memory.manage.mem_model.data_id_manager import DataIdManager
from openjiuwen.core.memory.manage.mem_model.memory_unit import BaseMemoryUnit, FragmentMemoryUnit, MemoryType
from openjiuwen.core.memory.manage.mem_model.semantic_store import SemanticStore
from openjiuwen.core.memory.manage.mem_model.user_mem_store import UserMemStore
from openjiuwen.core.memory.manage.update.mem_update_checker import MemUpdateChecker, MemoryStatus
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.common.logging.events import LogEventType


@dataclass
class RecallParams:
    """Parameters for vector recall operation"""
    query: str
    user_id: str
    scope_id: str
    semantic_store: SemanticStore
    top_k: int = 5
    mem_type: str = MemoryType.USER_PROFILE.value


@dataclass
class AddVectorParams:
    """Parameters for adding vector memory operation"""
    user_id: str
    scope_id: str
    memory_id: str
    mem: str
    semantic_store: SemanticStore
    mem_type: str = MemoryType.USER_PROFILE.value


class FragmentMemoryStoreParams(BaseModel):
    mem_type: str = Field(default=MemoryType.USER_PROFILE.value)
    user_id: Optional[str] = None
    scope_id: Optional[str] = None
    content: Optional[str] = None
    source_id: Optional[str] = None


FRAGMENT_MEMORY_TYPE = [MemoryType.USER_PROFILE.value, MemoryType.SEMANTIC_MEMORY.value,
                        MemoryType.EPISODIC_MEMORY.value]


class FragmentMemoryManager(BaseMemoryManager):
    UPDATE_CHECK_OLD_MEMORY_NUM = 5
    UPDATE_CHECK_OLD_MEMORY_RELEVANCE_THRESHOLD = 0.75

    def __init__(self,
                 user_mem_store: UserMemStore,
                 data_id_generator: DataIdManager,
                 crypto_key: bytes):
        self.mem_store = user_mem_store
        self.date_user_profile_id = data_id_generator
        self.crypto_key = crypto_key
        self.mem_type = "fragment"

    @staticmethod
    def _process_conflict_info(conflict_info: list[dict], input_memory_ids_map: dict[int, str]) -> list[dict]:
        process_conflict_info = []
        for conflict in conflict_info:
            conf_id = int(conflict['id'])
            conf_mem = conflict['text']
            conf_event = conflict['event']
            if conf_id == 0:
                process_conflict_info.append({
                    "id": '-1',
                    "text": conf_mem,
                    "event": conf_event
                })
                continue
            map_id = input_memory_ids_map[conf_id]
            process_conflict_info.append({
                "id": map_id,
                "text": conf_mem,
                "event": conf_event
            })
        return process_conflict_info

    async def add_memories(self, user_id: str, scope_id: str, memories: dict[str, list[BaseMemoryUnit]],
                      llm: Tuple[str, Model] | None = None, **kwargs):
        semantic_store = self._get_semantic_store("add", **kwargs)
        # Step 1: Prepare new memories dictionary for checker
        new_mem_content: dict[str, str] = {}
        new_mem_units: dict[str, FragmentMemoryUnit] = {}
        for mem_type, memory in memories.items():
            if mem_type not in FRAGMENT_MEMORY_TYPE:
                continue
            for mem_unit in memory:
                if not isinstance(mem_unit, FragmentMemoryUnit):
                    memory_logger.warning(
                        "mem_unit is not a FragmentMemoryUnit",
                        event_type=LogEventType.MEMORY_STORE,
                        memory_type=mem_type,
                        user_id=user_id,
                        scope_id=scope_id
                    )
                    continue
                mem_content = mem_unit.content
                mem_id = mem_unit.mem_id
                if mem_content:
                    new_mem_content[mem_id] = mem_content
                    new_mem_units[mem_id] = mem_unit

        # Step 2: Query existing memories for context using search
        old_memories: dict[str, str] = {}
        old_mem_ids = set()
        for _, new_mem in new_mem_content.items():
            # Search for similar memories
            search_results = await self.search(
                user_id=user_id,
                scope_id=scope_id,
                query=new_mem,
                top_k=self.UPDATE_CHECK_OLD_MEMORY_NUM,
                semantic_store=semantic_store
            )

            if search_results:
                for result in search_results:
                    result_id = result.get("id", "")
                    result_score = result.get("score", 0)
                    result_content = result.get("mem", "")

                    if (result_id and result_score > self.UPDATE_CHECK_OLD_MEMORY_RELEVANCE_THRESHOLD and
                        result_id not in old_mem_ids):
                        old_memories[result_id] = result_content
                        old_mem_ids.add(result_id)

        # If no existing memories found, and only has one new memory, skip check and write directly
        if not old_memories and len(new_mem_units) == 1:
            # Get the only memory unit from new_mem_units
            mem_unit = next(iter(new_mem_units.values()))
            await self._add_memory_to_store(user_id, scope_id, mem_unit, semantic_store)
            return

        # Step 3: Use MemChecker to analyze for redundancy/conflicts
        checker = MemUpdateChecker()
        action_items = await checker.check(
            new_memories=new_mem_content,
            old_memories=old_memories,
            base_chat_model=llm,
        )
        memory_logger.info(
            f"Memory check completed, got {len(action_items)} action items",
            event_type=LogEventType.MEMORY_PROCESS,
            metadata={"action_count": len(action_items)},
        )

        # Step 4: Execute add/delete operations based on action items
        for action_item in action_items:
            if action_item.status == MemoryStatus.ADD:
                # Add new memory
                mem_unit = new_mem_units.get(action_item.id)
                if mem_unit:
                    await self._add_memory_to_store(user_id, scope_id, mem_unit, semantic_store)
            elif action_item.status == MemoryStatus.DELETE:
                # Delete old memory
                await self.delete(
                    user_id=user_id,
                    scope_id=scope_id,
                    mem_id=action_item.id,
                    semantic_store=semantic_store
                )

    async def update(self, user_id: str, scope_id: str, mem_id: str, new_memory: str, **kwargs) -> bool:
        semantic_store = self._get_semantic_store("update", **kwargs)
        time = datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M:%S')
        encrypt_new_memory = BaseMemoryManager.encrypt_memory_if_needed(key=self.crypto_key, plaintext=new_memory)
        new_data = {'mem': encrypt_new_memory, 'time': time}
        old_mem = await self.mem_store.get(mem_id=mem_id, user_id=user_id, scope_id=scope_id)
        mem_type = old_mem.get("mem_type", None)
        await self.mem_store.update(mem_id=mem_id, user_id=user_id, scope_id=scope_id, data=new_data)
        table_name = generate_idx_name(user_id, scope_id, mem_type)
        await semantic_store.delete_docs([mem_id], table_name)
        # semantic memory embedding must not encrypt
        await semantic_store.add_docs([(mem_id, new_memory)], table_name, scope_id=scope_id)
        return True

    async def search(self, user_id: str, scope_id: str, query: str, top_k: int, **kwargs):
        semantic_store = self._get_semantic_store("search", **kwargs)
        mem_type = kwargs.get("mem_type", None)
        if not mem_type:
            mem_types = FRAGMENT_MEMORY_TYPE
        else:
            mem_types = [mem_type]
        mem_ids, scores = [], {}
        for mem_type in mem_types:
            mem_ids_, scores_ = await self._recall_by_vector(
                RecallParams(
                    query=query,
                    user_id=user_id,
                    scope_id=scope_id,
                    semantic_store=semantic_store,
                    top_k=top_k,
                    mem_type=mem_type
                )
            )
            mem_ids.extend(mem_ids_)
            scores.update(scores_)
            mem_ids_, scores_ = [], {}
        retrieve_res = await self.mem_store.batch_get(user_id=user_id, scope_id=scope_id, mem_ids=mem_ids)
        if retrieve_res is None:
            return None
        for item in retrieve_res:
            item["score"] = scores.get(item['id'], 0)
            item["mem"] = BaseMemoryManager.decrypt_memory_if_needed(key=self.crypto_key, ciphertext=item["mem"])
        retrieve_res.sort(key=lambda x: scores.get(x["id"], 0), reverse=True)
        return retrieve_res[:top_k]

    async def get(self, user_id: str, scope_id: str, mem_id: str) -> dict[str, Any] | None:
        retrieve_res = await self.mem_store.get(user_id=user_id, scope_id=scope_id, mem_id=mem_id)
        retrieve_res["mem"] = BaseMemoryManager.decrypt_memory_if_needed(key=self.crypto_key,
                                                                         ciphertext=retrieve_res["mem"])
        return retrieve_res

    async def delete(self, user_id: str, scope_id: str, mem_id: str, **kwargs):
        semantic_store = self._get_semantic_store("delete", **kwargs)
        data = await self.mem_store.get(user_id=user_id, scope_id=scope_id, mem_id=mem_id)
        if data is None:
            memory_logger.error(
                "Delete user_profile in store failed",
                memory_type="user_profile",
                memory_id=[mem_id],
                event_type=LogEventType.MEMORY_STORE,
                user_id=user_id,
                scope_id=scope_id
            )
            return False
        mem_type = kwargs.get("mem_type", data.get("mem_type", None))
        await self.mem_store.delete(mem_id=mem_id, user_id=user_id, scope_id=scope_id)
        if not mem_type:
            mem_types = FRAGMENT_MEMORY_TYPE
        else:
            mem_types = [mem_type]
        for mem_type in mem_types:
            await self._delete_vector_user_profile_memory(
                memory_id=[mem_id],
                user_id=user_id,
                scope_id=scope_id,
                mem_type=mem_type,
                semantic_store=semantic_store
            )
        return True

    async def delete_by_user_id(self, user_id: str, scope_id: str, **kwargs):
        semantic_store = self._get_semantic_store("delete", **kwargs)
        datas = []
        for mem_type in FRAGMENT_MEMORY_TYPE:
            data = await self.mem_store.get_all(
                user_id=user_id,
                scope_id=scope_id,
                mem_type=mem_type
            )
            if data:
                datas.extend(data)
        if not datas:
            memory_logger.error(
                "Delete user_profile in store failed, the mem of user_id is not exist.",
                memory_type="user_profile",
                event_type=LogEventType.MEMORY_STORE,
                user_id=user_id,
                scope_id=scope_id
            )
            return False
        mem_ids = [item['id'] for item in datas]
        await self.mem_store.batch_delete(user_id=user_id, scope_id=scope_id, mem_ids=mem_ids)
        for mem_type in FRAGMENT_MEMORY_TYPE:
            await self._delete_vector_store_table(user_id=user_id, scope_id=scope_id,
                                                  mem_type=mem_type, semantic_store=semantic_store)
        return True

    async def list_fragment_memories(self, user_id: str, scope_id: str,
                                mem_type: Optional[MemoryType] = None) -> list[dict[str, Any]]:
        if not mem_type:
            mem_types = FRAGMENT_MEMORY_TYPE
        else:
            if mem_type.value not in FRAGMENT_MEMORY_TYPE:
                memory_logger.error(
                    f"{mem_type.value} is not a valid memory type",
                    memory_type=mem_type,
                    event_type=LogEventType.MEMORY_STORE,
                    user_id=user_id,
                    scope_id=scope_id
                )
                return []
            mem_types = [mem_type.value]
        datas = []
        for mem_type in mem_types:
            data = await self.mem_store.get_all(user_id=user_id, scope_id=scope_id, mem_type=mem_type)
            if data:
                datas.extend(data)
        if not datas:
            memory_logger.debug(
                "End to get user profile, result is None.",
                memory_type=mem_type,
                event_type=LogEventType.MEMORY_STORE,
                user_id=user_id,
                scope_id=scope_id
            )
            return []
        for data in datas:
            data["mem"] = BaseMemoryManager.decrypt_memory_if_needed(key=self.crypto_key,
                                                                     ciphertext=data["mem"])
        datas.sort(key=lambda x: (x['mem'], x['timestamp']), reverse=True)
        return datas

    async def _add_memory_to_store(
        self,
        user_id: str,
        scope_id: str,
        memory: FragmentMemoryUnit,
        semantic_store: SemanticStore
    ):
        if not user_id:
            raise build_error(
                StatusCode.MEMORY_ADD_MEMORY_EXECUTION_ERROR,
                memory_type=memory.mem_type,
                error_msg=f"user_profile_manager add operation must pass user_id",
            )
        if not scope_id:
            raise build_error(
                StatusCode.MEMORY_ADD_MEMORY_EXECUTION_ERROR,
                memory_type=memory.mem_type,
                error_msg=f"user_profile_manager add operation must pass scope_id",
            )
        if not memory.content:
            raise build_error(
                StatusCode.MEMORY_ADD_MEMORY_EXECUTION_ERROR,
                memory_type=memory.mem_type,
                error_msg=f"user_profile_manager add operation must pass profile_mem",
            )

        memory_logger.debug(
            "Add memory",
            memory_type=memory.mem_type,
            event_type=LogEventType.MEMORY_STORE,
            user_id=user_id,
            scope_id=scope_id
        )
        user_profile_search_param = FragmentMemoryStoreParams(
            user_id=user_id,
            scope_id=scope_id,
            mem_type=memory.mem_type.value,
            content=memory.content,
            source_id=memory.message_mem_id
        )
        mem_id = await self._add_user_profile_memory(user_profile_search_param)
        vector_success = await self._add_vector_user_profile_memory(
            AddVectorParams(
                user_id=user_id,
                scope_id=scope_id,
                memory_id=mem_id,
                mem=memory.content,
                semantic_store=semantic_store,
                mem_type=memory.mem_type.value
            )
        )
        if not vector_success:
            await self.delete(user_id=user_id, scope_id=scope_id, mem_id=mem_id, semantic_store=semantic_store)
            raise build_error(
                StatusCode.MEMORY_ADD_MEMORY_EXECUTION_ERROR,
                memory_type=memory.mem_type,
                error_msg=f"user_profile_manager add vector store failed",
            )

    async def _recall_by_vector(
            self,
            params: RecallParams
    ) -> tuple[List[str], dict[str, float]]:
        table_name = generate_idx_name(params.user_id, params.scope_id, params.mem_type)
        memory_hit_info = await params.semantic_store.search(
            query=params.query,
            table_name=table_name,
            top_k=params.top_k
        )
        return parse_memory_hit_infos(memory_hit_info)

    async def _add_user_profile_memory(self, req: FragmentMemoryStoreParams) -> str:
        mem_id = str(await self.date_user_profile_id.generate_next_id(user_id=req.user_id))

        time = datetime.now(timezone.utc).astimezone()
        content = BaseMemoryManager.encrypt_memory_if_needed(
            key=self.crypto_key,
            plaintext=req.content
        )
        data = {
            'id': mem_id,
            'user_id': req.user_id or '',
            'scope_id': req.scope_id or '',
            'mem': content,
            'source_id': req.source_id,
            'mem_type': req.mem_type,
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')
        }
        await self.mem_store.write(user_id=req.user_id, scope_id=req.scope_id, mem_id=mem_id, data=data)
        return mem_id

    async def _add_vector_user_profile_memory(
            self, params: AddVectorParams) -> bool:
        if params.semantic_store:
            table_name = generate_idx_name(params.user_id, params.scope_id, params.mem_type)
            return await params.semantic_store.add_docs(
                [(params.memory_id, params.mem)],
                table_name,
                scope_id=params.scope_id
            )
        else:
            raise build_error(
                StatusCode.MEMORY_ADD_MEMORY_EXECUTION_ERROR,
                memory_type="user profile",
                error_msg=f"vector store must not be None",
            )

    async def _delete_vector_user_profile_memory(
            self,
            user_id: str,
            scope_id: str,
            memory_id: List[str],
            semantic_store: SemanticStore,
            mem_type: str = None
    ):
        if semantic_store:
            table_name = generate_idx_name(user_id, scope_id, mem_type)
            await semantic_store.delete_docs(memory_id, table_name)
        else:
            raise build_error(
                StatusCode.MEMORY_DELETE_MEMORY_EXECUTION_ERROR,
                memory_type="user profile",
                error_msg=f"vector store must not be None",
            )

    async def _delete_vector_store_table(
            self,
            user_id: str,
            scope_id: str,
            mem_type,
            semantic_store: SemanticStore
    ):
        if semantic_store:
            table_name = generate_idx_name(usr_id=user_id, scope_id=scope_id, mem_type=mem_type)
            await semantic_store.delete_table(table_name=table_name)
        else:
            raise build_error(
                StatusCode.MEMORY_DELETE_MEMORY_EXECUTION_ERROR,
                memory_type="user profile",
                error_msg=f"vector store must not be None",
            )

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
                memory_type="fragment_memory",
                error_msg="semantic_store is required",
            )
        return semantic_store
