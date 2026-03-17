# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Any, List, Optional, Tuple

from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.memory.manage.mem_model.memory_unit import MemoryType, BaseMemoryUnit, VariableUnit
from openjiuwen.core.memory.manage.index.base_memory_manager import BaseMemoryManager
from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore
from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.common.logging.events import LogEventType
from openjiuwen.core.memory.common.kv_prefix_registry import kv_prefix_registry


class VariableManager(BaseMemoryManager):
    SEPARATOR = "/"
    USER_VAR_PREFIX = "user_var"
    SESSION_VAR_PREFIX = "session_var"
    LEGACY_PREFIXES: List[str] = []

    def __init__(self,
                 kv_store: BaseKVStore,
                 crypto_key: bytes):
        self.kv_store = kv_store
        self.crypto_key = crypto_key
        self.mem_type = MemoryType.VARIABLE.value
        kv_prefix_registry.register_current(self.USER_VAR_PREFIX)
        kv_prefix_registry.register_current(self.SESSION_VAR_PREFIX)
        for legacy_prefix in self.LEGACY_PREFIXES:
            kv_prefix_registry.register_legacy(legacy_prefix)

    async def add_memories(self, user_id: str, scope_id: str, memories: dict[str, list[BaseMemoryUnit]],
                           llm: Tuple[str, Model] | None = None, **kwargs):
        """add Variable memories in batch"""
        for mem_type, memory in memories.items():
            if mem_type != self.mem_type:
                continue
            for unit in memory:
                if not isinstance(unit, VariableUnit):
                    memory_logger.warning(
                        "mem_unit is not a VariableUnit",
                        event_type=LogEventType.MEMORY_STORE,
                        memory_type=self.mem_type,
                        user_id=user_id,
                        scope_id=scope_id
                    )
                    continue
                if self.kv_store is None:
                    memory_logger.error(
                        "kv_store cannot be None",
                        event_type=LogEventType.MEMORY_STORE,
                        memory_type=self.mem_type,
                        user_id=user_id,
                        scope_id=scope_id
                    )
                    return
                key, value = self._make_variable_pairs(
                    user_id,
                    False,
                    scope_id,
                    unit.variable_name,
                    None,
                    unit.variable_mem,
                    None
                )
                await self.kv_store.set(key, value)

    async def update(self, user_id: str, scope_id: str, mem_id: str, new_memory: str, **kwargs):
        memory_logger.warning(
            "Not implemented method update",
            event_type=LogEventType.MEMORY_STORE,
            memory_type=self.mem_type,
            memory_id=[mem_id],
            user_id=user_id,
            scope_id=scope_id
        )
        pass

    async def update_user_variable(self, user_id: str, scope_id: str, var_name: str, var_mem: str):
        if self.kv_store is None:
            memory_logger.error(
                "KV_store cannot be None",
                event_type=LogEventType.MEMORY_STORE,
                memory_type=self.mem_type,
                user_id=user_id,
                scope_id=scope_id
            )
            return
        existing_variable = await self.query_variable(user_id=user_id, scope_id=scope_id, name=var_name)
        if not VariableManager._check_exist(existing_variable, var_name):
            return
        key, value = self._make_variable_pairs(usr_id=user_id, for_deletion=False,
                                               scope_id=scope_id, var_name=var_name, user_var_value=var_mem)
        await self.kv_store.set(key, value)

    async def delete(self, user_id: str, scope_id: str, mem_id: str, **kwargs):
        memory_logger.error(
            "Not implemented method delete",
            event_type=LogEventType.MEMORY_STORE,
            memory_id=[mem_id],
            memory_type=self.mem_type,
            user_id=user_id,
            scope_id=scope_id
        )
        pass

    async def delete_by_user_id(self, user_id: str, scope_id: str, **kwargs):
        if self.kv_store is None:
            memory_logger.error(
                "kv_store cannot be None",
                event_type=LogEventType.MEMORY_STORE,
                memory_type=self.mem_type,
                user_id=user_id,
                scope_id=scope_id
            )
            return
        user_prefix = f"{self.USER_VAR_PREFIX}{self.SEPARATOR}{user_id}{self.SEPARATOR}{scope_id}{self.SEPARATOR}"
        session_prefix = f"{self.SESSION_VAR_PREFIX}{self.SEPARATOR}{user_id}{self.SEPARATOR}{scope_id}{self.SEPARATOR}"
        await self.kv_store.delete_by_prefix(user_prefix)
        await self.kv_store.delete_by_prefix(session_prefix)

    async def delete_user_variable(self, user_id: str, scope_id: str, var_name: str):
        if self.kv_store is None:
            memory_logger.error(
                "kv_store cannot be None",
                event_type=LogEventType.MEMORY_STORE,
                memory_type=self.mem_type,
                user_id=user_id,
                scope_id=scope_id
            )
            return
        key, _ = self._make_variable_pairs(usr_id=user_id, for_deletion=False, scope_id=scope_id, var_name=var_name)
        await self.kv_store.delete(key)

    async def get(self, user_id: str, scope_id: str, mem_id: str) -> dict[str, Any] | None:
        memory_logger.warning(
            "Not implemented method get",
            memory_id=[mem_id],
            event_type=LogEventType.MEMORY_STORE,
            memory_type=self.mem_type,
            user_id=user_id,
            scope_id=scope_id
        )
        pass

    async def search(self, user_id: str, scope_id: str, query: str, top_k: int, **kwargs):
        memory_logger.warning(
            "Not implemented method search",
            event_type=LogEventType.MEMORY_STORE,
            memory_type=self.mem_type,
            query=query,
            user_id=user_id,
            scope_id=scope_id
        )
        pass

    async def query_variable(self, user_id: str, scope_id: str, name: Optional[str] = None,
                             session_id: Optional[str] = None) -> dict[str, Any]:
        """query variable by user_id, scope_id, variable_name return variable mem."""
        self._check_user_and_scope_id(user_id, scope_id, "Search")
        if not name or not name.strip():
            prefix_str = f"{self.USER_VAR_PREFIX}{self.SEPARATOR}{user_id}{self.SEPARATOR}{scope_id}{self.SEPARATOR}"
            kv_ret = await self.kv_store.get_by_prefix(prefix_str)
            result = {}
            for k, v in kv_ret.items():
                v = BaseMemoryManager.decrypt_memory_if_needed(key=self.crypto_key, ciphertext=v)
                result[k.split(f"{self.SEPARATOR}")[-1]] = v
            return result
        if session_id:
            key = (f"{self.SESSION_VAR_PREFIX}{self.SEPARATOR}{user_id}{self.SEPARATOR}{scope_id}"
                   f"{self.SEPARATOR}{session_id}{self.SEPARATOR}{name}")
        else:
            key = f"{self.USER_VAR_PREFIX}{self.SEPARATOR}{user_id}{self.SEPARATOR}{scope_id}{self.SEPARATOR}{name}"
        kv_ret = await self.kv_store.get(key)
        kv_ret = BaseMemoryManager.decrypt_memory_if_needed(key=self.crypto_key, ciphertext=kv_ret)
        return {name: kv_ret}

    def _make_variable_pairs(
            self,
            usr_id: str,
            for_deletion: bool,
            scope_id: str,
            var_name: Optional[str] = None,
            session_id: Optional[str] = None,
            user_var_value: Optional[str] = None,
            session_var_value: Optional[str] = None
    ) -> Tuple[str, str]:
        key, value = "", ""
        user_var_value = BaseMemoryManager.encrypt_memory_if_needed(key=self.crypto_key,
                                                                    plaintext=user_var_value)
        session_var_value = BaseMemoryManager.encrypt_memory_if_needed(key=self.crypto_key,
                                                                       plaintext=session_var_value)
        if var_name is not None:
            # 1) user_var
            if session_id is None:
                key = (
                    f"{self.USER_VAR_PREFIX}{VariableManager.SEPARATOR}{usr_id}"
                    f"{VariableManager.SEPARATOR}{scope_id}"
                    f"{VariableManager.SEPARATOR}{var_name}"
                )
                value = None if for_deletion else user_var_value
            # 2) session_var
            else:
                key = (
                    f"{self.SESSION_VAR_PREFIX}{VariableManager.SEPARATOR}{usr_id}"
                    f"{VariableManager.SEPARATOR}{scope_id}"
                    f"{VariableManager.SEPARATOR}{session_id}"
                    f"{VariableManager.SEPARATOR}{var_name}"
                )
                value = None if for_deletion else session_var_value
        return key, value

    @staticmethod
    def _check_user_and_scope_id(user_id, scope_id, context="Operation"):
        if not user_id or not user_id.strip():
            memory_logger.error(
                "Check user and scope id operation failed, user ID is empty",
                event_type=LogEventType.MEMORY_RETRIEVE,
                memory_type="variable",
                user_id=user_id,
                scope_id=scope_id,
                metadata={"context": context}
            )
        if not scope_id or not scope_id.strip():
            memory_logger.error(
                "Check user and scope id operation failed, scope ID is empty",
                event_type=LogEventType.MEMORY_RETRIEVE,
                memory_type="variable",
                user_id=user_id,
                scope_id=scope_id,
                metadata={"context": context},
            )

    @staticmethod
    def _check_exist(variable_dict: dict[str, Any], variable_name: str) -> bool:
        if not variable_dict:
            return False

        if variable_name not in variable_dict.keys():
            return False

        if not variable_dict[variable_name]:
            return False

        return True