# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import copy
from datetime import datetime, timedelta, timezone
from typing import Tuple
from pydantic import BaseModel, Field

from openjiuwen.core.foundation.llm.schema.config import ModelRequestConfig, ModelClientConfig
from openjiuwen.core.memory.common.distributed_lock import DistributedLock
from openjiuwen.core.memory.config.config import MemoryEngineConfig, MemoryScopeConfig, AgentMemoryConfig
from openjiuwen.core.memory.process.extract.generation import Generator
from openjiuwen.core.memory.manage.mem_model.data_id_manager import DataIdManager
from openjiuwen.core.memory.manage.mem_model.message_manager import MessageManager, MessageAddRequest
from openjiuwen.core.memory.manage.index.fragment_memory_manager import FragmentMemoryManager
from openjiuwen.core.memory.manage.index.variable_manager import VariableManager
from openjiuwen.core.memory.manage.index.write_manager import WriteManager
from openjiuwen.core.memory.manage.index.summary_manager import SummaryManager
from openjiuwen.core.memory.manage.mem_model.memory_unit import MemoryType
from openjiuwen.core.memory.manage.index.base_memory_manager import BaseMemoryManager
from openjiuwen.core.memory.manage.search.search_manager import SearchManager, SearchParams
from openjiuwen.core.foundation.store.base_db_store import BaseDbStore
from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore
from openjiuwen.core.memory.manage.mem_model.semantic_store import SemanticStore
from openjiuwen.core.memory.manage.mem_model.db_model import create_tables
from openjiuwen.core.memory.manage.mem_model.sql_db_store import SqlDbStore
from openjiuwen.core.memory.manage.mem_model.user_mem_store import UserMemStore
from openjiuwen.core.foundation.llm import UserMessage, BaseMessage, Model
from openjiuwen.core.common.utils.singleton import Singleton
from openjiuwen.core.retrieval.embedding.base import Embedding
from openjiuwen.core.retrieval.embedding.api_embedding import APIEmbedding
from openjiuwen.core.foundation.store.base_vector_store import BaseVectorStore
from openjiuwen.core.memory.manage.mem_model.scope_user_mapping_manager import ScopeUserMappingManager
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.common.logging.events import LogEventType
from openjiuwen.core.memory.migration.run_migrations import run_kv_migrations, run_vector_migrations, run_sql_migrations
from openjiuwen.core.runner.callback import trigger, lazy_callback_framework as _fw
from openjiuwen.core.runner.callback.events import MemoryEvents


class MemInfo(BaseModel):
    mem_id: str = Field(default="", description="memory id")
    content: str = Field(default="", description="memory content")
    type: MemoryType = Field(default=MemoryType.USER_PROFILE, description="memory type")


class MemResult(BaseModel):
    mem_info: MemInfo = Field(default=None, description="memory information")
    score: float = Field(default=0.0, description="memory score of relevance")


class LongTermMemory(metaclass=Singleton):
    """
        Abstract base class for memory engine.

        Defines the core interface for memory storage and retrieval operations.
        Provides unified memory management functionality including conversation memory,
        user variables, semantic search, and persistence.

        Concrete implementations should handle memory operations across multiple storage
        backends (KV store, semantic store, database store).
    """
    DEFAULT_VALUE: str = "__default__"
    SCOPE_CONFIG_KEY: str = "memory_scope_config"

    def __init__(self):
        """
        Initialize the memory engine
        """
        # config
        self._sys_mem_config: MemoryEngineConfig | None = None
        self._scope_config: dict[str, MemoryScopeConfig] = {}
        # store
        self.kv_store: BaseKVStore | None = None
        self.vector_store: BaseVectorStore | None = None
        self.db_store: BaseDbStore | None = None
        # managers
        self.scope_user_mapping_manager = None
        self.message_manager: MessageManager | None = None
        self.fragment_memory_manager = None
        self.variable_manager = None
        self.write_manager = None
        self.summary_manager = None
        self.search_manager = None
        self.generator = None
        self.fragment_type = None
        # llm
        self._base_llm: Model | None = None
        # embedding
        self._base_embed: Embedding | None = None
        # embedding model cache
        self._scope_embedding: dict[str, Embedding] = {}

    async def register_store(self, kv_store: BaseKVStore,
                             vector_store: BaseVectorStore | None = None,
                             db_store: BaseDbStore | None = None,
                             embedding_model: Embedding | None = None):
        """
        Register store instance.

        Args:
            kv_store: Key-value store for fast structured data access
            vector_store: Vector storage for vector-based similarity search
            db_store: Database store for persistent data storage
            embedding_model: Embedding model for semantic search
        """
        if kv_store is None:
            raise build_error(
                StatusCode.MEMORY_REGISTER_STORE_EXECUTION_ERROR,
                store_type="kv store",
                error_msg="kv store is required, cannot be None",
            )

        if vector_store is not None and not isinstance(vector_store, BaseVectorStore):
            raise build_error(
                StatusCode.MEMORY_REGISTER_STORE_EXECUTION_ERROR,
                store_type="vector store",
                error_msg="vector store must be instance of BaseVectorStore",
            )

        if db_store is not None and not isinstance(db_store, BaseDbStore):
            raise build_error(
                StatusCode.MEMORY_REGISTER_STORE_EXECUTION_ERROR,
                store_type="db store",
                error_msg="db store must be instance of BaseDbStore",
            )

        self.kv_store = kv_store
        self.vector_store = vector_store
        self.db_store = db_store
        self._base_embed = embedding_model

        if self.db_store:
            await create_tables(self.db_store)

        self.set_config(MemoryEngineConfig())

        await self._run_migration(
            migrate_func=run_kv_migrations,
            store=self.kv_store,
            store_type="kv store"
        )

        if self.vector_store:
            await self._run_migration(
                migrate_func=run_vector_migrations,
                store=self.vector_store,
                store_type="vector store"
            )

        if self.db_store:
            sql_db_store = SqlDbStore(self.db_store)
            await self._run_migration(
                migrate_func=run_sql_migrations,
                store=sql_db_store,
                store_type="db store"
            )

    def set_config(self, config: MemoryEngineConfig):
        """
        Set configuration.

        Args:
            config: memory engine configuration parameters
        """
        if not self.kv_store or not self.db_store or not self.vector_store:
            raise build_error(
                StatusCode.MEMORY_SET_CONFIG_EXECUTION_ERROR,
                config_type="system",
                error_msg="stores must be registered before setting config",
            )
        self._sys_mem_config = config
        data_id_generator = DataIdManager()
        user_mem_store = UserMemStore(self.kv_store)

        if self.db_store:
            sql_db_store = SqlDbStore(self.db_store)
            self.scope_user_mapping_manager = ScopeUserMappingManager(sql_db_store)
            self.message_manager = MessageManager(
                sql_db_store,
                data_id_generator,
                config.crypto_key
            )
        self.fragment_memory_manager = FragmentMemoryManager(
            user_mem_store=user_mem_store,
            data_id_generator=data_id_generator,
            crypto_key=config.crypto_key
        )
        self.variable_manager = VariableManager(
            self.kv_store,
            config.crypto_key
        )
        self.summary_manager = SummaryManager(
            user_mem_store=user_mem_store,
            crypto_key=self._sys_mem_config.crypto_key
        )
        managers = {
            MemoryType.USER_PROFILE.value: self.fragment_memory_manager,
            MemoryType.EPISODIC_MEMORY.value: self.fragment_memory_manager,
            MemoryType.SEMANTIC_MEMORY.value: self.fragment_memory_manager,
            MemoryType.VARIABLE.value: self.variable_manager,
            MemoryType.SUMMARY.value: self.summary_manager
        }
        self.fragment_type = [MemoryType.USER_PROFILE.value, MemoryType.EPISODIC_MEMORY.value,
                              MemoryType.SEMANTIC_MEMORY.value]
        self.write_manager = WriteManager(managers, user_mem_store)
        self.search_manager = SearchManager(
            managers,
            user_mem_store,
            config.crypto_key
        )
        self.generator = Generator(data_id_generator=data_id_generator)
        # set init llm
        if config.default_model_cfg and config.default_model_client_cfg:
            llm = LongTermMemory._get_llm_from_config(model_config=config.default_model_cfg,
                                                    model_client_config=config.default_model_client_cfg)
            self._base_llm = llm

    async def set_scope_config(self, scope_id: str, memory_scope_config: MemoryScopeConfig) -> bool:
        """
        Set the scope-specific memory configuration and store it in kv_store.

        Args:
            scope_id: The scope identifier.
            memory_scope_config: The scope-specific memory configuration.


        Returns:
            True if the configuration was set successfully, False otherwise.
        """
        if not self._validate_id(event_type=LogEventType.MEMORY_STORE, scope_id=scope_id):
            memory_logger.error(
                "Invalid scope_id format.",
                event_type=LogEventType.MEMORY_STORE,
                scope_id=scope_id
            )
            return False
        # Create a deep copy of the config to avoid modifying the original
        encrypted_config = copy.deepcopy(memory_scope_config)

        # Encrypt API keys if they exist
        if encrypted_config.model_client_cfg and encrypted_config.model_client_cfg.api_key:
            encrypted_config.model_client_cfg.api_key = BaseMemoryManager.encrypt_memory_if_needed(
                key=self._sys_mem_config.crypto_key,
                plaintext=encrypted_config.model_client_cfg.api_key
            )

        if encrypted_config.embedding_cfg and encrypted_config.embedding_cfg.api_key:
            encrypted_config.embedding_cfg.api_key = BaseMemoryManager.encrypt_memory_if_needed(
                key=self._sys_mem_config.crypto_key,
                plaintext=encrypted_config.embedding_cfg.api_key
            )

        self._scope_config[scope_id] = encrypted_config

        config_key = f"{self.SCOPE_CONFIG_KEY}/{scope_id}"
        config_json = encrypted_config.model_dump_json(by_alias=True)
        await self.kv_store.set(config_key, config_json)

        # Clear cached embedding model for this scope since configuration changed
        if scope_id in self._scope_embedding:
            del self._scope_embedding[scope_id]

        return True

    async def get_scope_config(self, scope_id: str) -> MemoryScopeConfig | None:
        """
        Get the scope-specific memory configuration from kv_store.

        Args:
            scope_id: Unique identifier for the scope

        Returns:
            MemoryScopeConfig: The decrypted memory configuration for the scope, or None if not found
        """
        if not self._validate_id(event_type=LogEventType.MEMORY_RETRIEVE, scope_id=scope_id):
            memory_logger.error(
                "Invalid scope_id format.",
                event_type=LogEventType.MEMORY_RETRIEVE,
                scope_id=scope_id
            )
            return None
        config_key = f"{self.SCOPE_CONFIG_KEY}/{scope_id}"
        config_json = await self.kv_store.get(config_key)

        if not config_json:
            return None

        # Parse the JSON into MemoryScopeConfig
        encrypted_config = MemoryScopeConfig.model_validate_json(config_json)

        # Decrypt API keys if they exist
        if encrypted_config.model_client_cfg and encrypted_config.model_client_cfg.api_key:
            encrypted_config.model_client_cfg.api_key = BaseMemoryManager.decrypt_memory_if_needed(
                key=self._sys_mem_config.crypto_key,
                ciphertext=encrypted_config.model_client_cfg.api_key
            )

        if encrypted_config.embedding_cfg and encrypted_config.embedding_cfg.api_key:
            encrypted_config.embedding_cfg.api_key = BaseMemoryManager.decrypt_memory_if_needed(
                key=self._sys_mem_config.crypto_key,
                ciphertext=encrypted_config.embedding_cfg.api_key
            )

        return encrypted_config

    async def delete_scope_config(self, scope_id: str) -> bool:
        """
        Delete the scope-specific memory configuration from kv_store.

        Args:
            scope_id: The scope identifier whose configuration should be deleted.

        Returns:
            True if the configuration was deleted successfully, False otherwise.
        """
        if not self._validate_id(event_type=LogEventType.MEMORY_DELETE, scope_id=scope_id):
            memory_logger.error(
                "Invalid scope_id format.",
                event_type=LogEventType.MEMORY_DELETE,
                scope_id=scope_id
            )
            return False
        try:
            config_key = f"{self.SCOPE_CONFIG_KEY}/{scope_id}"
            await self.kv_store.delete(config_key)

            if scope_id in self._scope_config:
                del self._scope_config[scope_id]

            if scope_id in self._scope_embedding:
                del self._scope_embedding[scope_id]

            memory_logger.debug(
                "Successfully deleted configuration.",
                event_type=LogEventType.MEMORY_DELETE,
                scope_id=scope_id
            )
            return True
        except Exception as e:
            memory_logger.error(
                "Failed to delete configuration.",
                event_type=LogEventType.MEMORY_DELETE,
                exception=str(e),
                scope_id=scope_id
            )
            return False

    async def delete_mem_by_scope(self, scope_id: str) -> bool:
        """
        Delete all memories associated with a specific scope.

        Args:
            scope_id: The scope identifier whose memories should be deleted.

        Returns:
            True if all memories were deleted successfully, False otherwise.
        """
        if not self._validate_id(event_type=LogEventType.MEMORY_DELETE, scope_id=scope_id):
            memory_logger.error(
                "Invalid scope_id format.",
                event_type=LogEventType.MEMORY_DELETE,
                scope_id=scope_id
            )
            return False
        scope_user_data = await self.scope_user_mapping_manager.get_by_scope_id(scope_id=scope_id)
        user_ids = [scope_user["user_id"] for scope_user in scope_user_data]
        # Use write_manager to delete all memories associated with the scope
        semantic_store = await self._create_semantic_store_with_embedding(scope_id)
        if self.write_manager:
            for user_id in user_ids:
                lock = DistributedLock(self.kv_store, f"user/{user_id}")
                async with lock:
                    await self.write_manager.delete_mem_by_user_id(
                        scope_id=scope_id,
                        user_id=user_id,
                        semantic_store=semantic_store
                    )
        await self.scope_user_mapping_manager.delete_by_scope_id(scope_id=scope_id)
        memory_logger.debug(
            "Successfully deleted memories.",
            event_type=LogEventType.MEMORY_DELETE,
            scope_id=scope_id
        )
        return True

    @_fw.emit_before(MemoryEvents.MEMORY_ADDED)
    async def add_messages(
            self,
            messages: list[BaseMessage],
            agent_config: AgentMemoryConfig,
            *,
            user_id: str = DEFAULT_VALUE,
            scope_id: str = DEFAULT_VALUE,
            session_id: str = DEFAULT_VALUE,
            timestamp: datetime | None = None,
            gen_mem: bool = True,
            gen_mem_with_history_msg_num: int = 2
    ):
        if not self._validate_id(event_type=LogEventType.MEMORY_STORE, scope_id=scope_id):
            memory_logger.error(
                "Invalid scope_id format.",
                event_type=LogEventType.MEMORY_STORE,
                scope_id=scope_id,
                user_id=user_id
            )
            return

        msg_id = "-1"
        llm = await self._get_scope_llm(scope_id)
        semantic_store = await self._create_semantic_store_with_embedding(scope_id)
        # user level distributed lock
        lock = DistributedLock(self.kv_store, f"user/{user_id}")
        async with lock:
            if not llm:
                memory_logger.error(
                    "LLM is not initialized.",
                    event_type=LogEventType.MEMORY_STORE,
                    user_id=user_id,
                    scope_id=scope_id
                )
                return
            history_messages = await self._get_history_messages(
                user_id=user_id,
                scope_id=scope_id,
                session_id=session_id,
                history_window_size=gen_mem_with_history_msg_num)
            # add meta data
            await self.scope_user_mapping_manager.add(user_id=user_id, scope_id=scope_id)
            # if timestamp is None, take the current time
            if not timestamp:
                timestamp = datetime.now(timezone.utc).astimezone()
            timestamp_str = timestamp.strftime('%Y-%m-%d %H:%M:%S')
            # when multi messages, use last msg_id
            for i, msg in enumerate(messages):
                msg_timestamp = timestamp + timedelta(milliseconds=i)
                add_req = MessageAddRequest(
                    user_id=user_id,
                    scope_id=scope_id,
                    role=msg.role,
                    content=msg.content,
                    session_id=session_id,
                    timestamp=msg_timestamp
                )
                msg_id = await self.message_manager.add(add_req)

            if not gen_mem:
                return

            check_res, messages = self._check_messages(messages=messages)
            if not check_res:
                memory_logger.debug(
                    "Memory engine no need to process messages.",
                    event_type=LogEventType.MEMORY_STORE,
                    memory_type="message",
                    memory_count=len(messages),
                    user_id=user_id,
                    scope_id=scope_id
                )
                return

            all_memory = await self.generator.gen_all_memory(
                scope_id=scope_id,
                user_id=user_id,
                messages=messages,
                history_messages=history_messages,
                session_id=session_id,
                config=agent_config,
                base_chat_model=llm,
                message_mem_id=msg_id,
                timestamp=timestamp_str,
                summary_max_token=self._sys_mem_config.single_turn_history_summary_max_token
            )
            try:
                await self.write_manager.add_memories(
                    user_id=user_id,
                    scope_id=scope_id,
                    memories=all_memory,
                    llm=llm,
                    semantic_store=semantic_store
                )
                memory_logger.debug(
                    "Successfully added memory units.",
                    event_type=LogEventType.MEMORY_STORE,
                    memory_count=len(all_memory),
                    memory_type="all type",
                    user_id=user_id,
                    scope_id=scope_id
                )
            except ValueError as e:
                memory_logger.error(
                    "Failed to add mem.",
                    memory_type="unknown",
                    event_type=LogEventType.MEMORY_STORE,
                    exception=str(e),
                    user_id=user_id,
                    scope_id=scope_id
                )
                raise build_error(
                    StatusCode.MEMORY_ADD_MEMORY_EXECUTION_ERROR,
                    memory_type="unknown",
                    error_msg=f"{str(e)}",
                    cause=e
                ) from e
        return

    async def get_recent_messages(
            self,
            user_id: str = DEFAULT_VALUE,
            scope_id: str = DEFAULT_VALUE,
            session_id: str = DEFAULT_VALUE,
            num: int = 10
    ) -> list[BaseMessage]:
        """
        Get recent messages.

        Args:
            user_id: Unique identifier for the user
            scope_id: Unique identifier for the scope
            session_id: Optional session identifier for scoping related messages
            num: message num

        Returns:
            Message list in order of writing.
        """
        if not self._validate_id(event_type=LogEventType.MEMORY_RETRIEVE, scope_id=scope_id):
            memory_logger.error(
                "Invalid scope_id format.",
                event_type=LogEventType.MEMORY_RETRIEVE,
                user_id=user_id,
                scope_id=scope_id,
                memory_type="message"
            )
            return []
        recent_messages_tuple = await self.message_manager.get(
            user_id=user_id,
            scope_id=scope_id,
            session_id=session_id,
            message_len=num
        )
        recent_messages = [msg for msg, _ in recent_messages_tuple]
        return recent_messages

    async def get_message_by_id(self, msg_id: str) -> Tuple[BaseMessage, datetime] | None:
        """
        Retrieve a specific message by its unique identifier.

        Args:
            msg_id: Unique identifier of the message to retrieve

        Returns:
            Tuple of (message object, creation timestamp)
        """
        if not self.message_manager:
            memory_logger.warning(
                "Message manager is not initialized.",
                event_type=LogEventType.MEMORY_RETRIEVE,
                memory_type="message",
                memory_id=[msg_id]
            )
            return None
        return await self.message_manager.get_by_id(msg_id)

    async def delete_messages_by_user_and_scope(
        self,
        user_id: str = DEFAULT_VALUE,
        scope_id: str = DEFAULT_VALUE,
    ):
        if not self._validate_id(event_type=LogEventType.MEMORY_RETRIEVE, scope_id=scope_id):
            memory_logger.error(
                "Invalid scope_id format.",
                event_type=LogEventType.MEMORY_RETRIEVE,
                user_id=user_id,
                scope_id=scope_id,
                memory_type="message"
            )
            return
        await self.message_manager.delete_by_user_and_scope(
            user_id=user_id,
            scope_id=scope_id
        )

    @_fw.emit_before(MemoryEvents.MEMORY_DELETED)
    async def delete_mem_by_id(self,
                               mem_id: str,
                               user_id: str = DEFAULT_VALUE,
                               scope_id: str = DEFAULT_VALUE):
        """
        Delete a specific memory by ID.

        Args:
            user_id: Unique identifier for the user
            scope_id: Unique identifier for the scope
            mem_id: Unique identifier of the memory to delete
        """
        if not self._validate_id(event_type=LogEventType.MEMORY_DELETE, scope_id=scope_id):
            memory_logger.error(
                "Invalid scope_id format.",
                event_type=LogEventType.MEMORY_DELETE,
                user_id=user_id,
                scope_id=scope_id,
                memory_id=[mem_id]
            )
            return
        semantic_store = await self._create_semantic_store_with_embedding(scope_id)
        lock = DistributedLock(self.kv_store, f"user/{user_id}")
        async with lock:
            if not self.write_manager:
                raise build_error(
                    StatusCode.MEMORY_DELETE_MEMORY_EXECUTION_ERROR,
                    memory_type="all",
                    error_msg=f"write manager is not initialized",
                )
            await self.write_manager.delete_mem_by_id(
                user_id=user_id,
                scope_id=scope_id,
                mem_id=mem_id,
                semantic_store=semantic_store
            )

    @_fw.emit_before(MemoryEvents.MEMORY_DELETED)
    async def delete_mem_by_user_id(self,
                                    user_id: str = DEFAULT_VALUE,
                                    scope_id: str = DEFAULT_VALUE):
        """
        Delete all type memories for a user with scope id.

        Useful for implementing "forget me" functionality or cleaning up user data.

        Args:
            user_id: User identifier whose memories should be deleted
            scope_id: Unique identifier for the scope
        """
        if not self._validate_id(event_type=LogEventType.MEMORY_DELETE, scope_id=scope_id):
            memory_logger.error(
                "Invalid scope_id format.",
                event_type=LogEventType.MEMORY_DELETE,
                user_id=user_id,
                scope_id=scope_id
            )
            return
        semantic_store = await self._create_semantic_store_with_embedding(scope_id)
        lock = DistributedLock(self.kv_store, f"user/{user_id}")
        async with lock:
            if not self.write_manager:
                raise build_error(
                    StatusCode.MEMORY_DELETE_MEMORY_EXECUTION_ERROR,
                    memory_type="all",
                    error_msg=f"write manager is not initialized",
                )
            await self.write_manager.delete_mem_by_user_id(
                user_id=user_id,
                scope_id=scope_id,
                semantic_store=semantic_store
            )

    @_fw.emit_before(MemoryEvents.MEMORY_UPDATED)
    async def update_mem_by_id(self,
                               mem_id: str,
                               memory: str,
                               user_id: str = DEFAULT_VALUE,
                               scope_id: str = DEFAULT_VALUE):
        """
        Update the content of an existing memory entry.

        Args:
            mem_id: Unique identifier of the memory to update
            memory: New content for the memory
            user_id: Unique identifier for the user
            scope_id: Unique identifier for the scope
        """
        if not self._validate_id(event_type=LogEventType.MEMORY_UPDATE, scope_id=scope_id):
            memory_logger.error(
                "Invalid scope_id format",
                event_type=LogEventType.MEMORY_UPDATE,
                user_id=user_id,
                scope_id=scope_id,
                memory_id=[mem_id]
            )
            return
        semantic_store = await self._create_semantic_store_with_embedding(scope_id)
        lock = DistributedLock(self.kv_store, f"user/{user_id}")
        async with lock:
            if not self.write_manager:
                raise build_error(
                    StatusCode.MEMORY_UPDATE_MEMORY_EXECUTION_ERROR,
                    memory_type="all",
                    error_msg=f"write manager is not initialized",
                )
            await self.write_manager.update_mem_by_id(user_id=user_id, scope_id=scope_id,
                                                      mem_id=mem_id, memory=memory, semantic_store=semantic_store)

    async def get_variables(self,
                            names: list[str] | str | None = None,
                            user_id: str = DEFAULT_VALUE,
                            scope_id: str = DEFAULT_VALUE) -> dict[str, str]:
        """
            Get user variable(s)

            Args:
                names: Name of the variable(s) to get.
                       - None: return all variables
                       - str: return one variable
                       - list[str]: return multiple variables
                user_id: user identifier
                scope_id: scope identifier

            Returns:
                dict[str, str]: variable name -> value
        """
        if not self._validate_id(event_type=LogEventType.MEMORY_RETRIEVE, scope_id=scope_id):
            memory_logger.error(
                "Invalid scope_id format",
                event_type=LogEventType.MEMORY_RETRIEVE,
                user_id=user_id,
                scope_id=scope_id,
                memory_type=MemoryType.VARIABLE.value,
            )
            return {}
        if not self.search_manager:
            raise build_error(
                StatusCode.MEMORY_GET_MEMORY_EXECUTION_ERROR,
                memory_type="all",
                error_msg=f"search manager is not initialized",
            )
        ret: dict[str, str] = {}
        if names is None:
            return await self.search_manager.get_all_user_variable(user_id=user_id, scope_id=scope_id)
        if isinstance(names, str):
            value = await self.search_manager.get_user_variable(user_id, scope_id, names)
            ret[names] = value
            return ret
        if isinstance(names, list):
            for name in names:
                value = await self.search_manager.get_user_variable(user_id, scope_id, name)
                ret[name] = value
            return ret
        raise build_error(
            StatusCode.MEMORY_GET_MEMORY_EXECUTION_ERROR,
            memory_type="all",
            error_msg=f"names must be str | list[str] | None",
        )

    @_fw.emit_before(MemoryEvents.MEMORY_SEARCH_STARTED)
    async def search_user_mem(self,
                              query: str,
                              num: int,
                              user_id: str = DEFAULT_VALUE,
                              scope_id: str = DEFAULT_VALUE,
                              threshold: float = 0.3
                              ) -> list[MemResult]:
        if not self._validate_id(event_type=LogEventType.MEMORY_RETRIEVE, scope_id=scope_id):
            memory_logger.error(
                "Invalid scope_id format.",
                event_type=LogEventType.MEMORY_RETRIEVE,
                query=query,
                user_id=user_id,
                scope_id=scope_id
            )
            return []
        semantic_store = await self._create_semantic_store_with_embedding(scope_id)
        if not self.search_manager:
            raise build_error(
                StatusCode.MEMORY_GET_MEMORY_EXECUTION_ERROR,
                memory_type="all",
                error_msg=f"search manager is not initialized",
            )
        params = SearchParams(
            query=query,
            scope_id=scope_id,
            top_k=num,
            user_id=user_id,
            threshold=threshold,
        )
        try:
            search_data = []
            for mem_type in self.fragment_type:
                params.search_type = mem_type
                res = await self.search_manager.search(params, semantic_store)
                search_data.extend(res)
            search_data = sorted(search_data, key=lambda x: x.get("score", 0.0), reverse=True)[:num]
            mem_results: list[MemResult] = [
                MemResult(
                    mem_info=MemInfo(
                        mem_id=item["id"],
                        content=item["mem"],
                        type=item.get("mem_type", None)
                    ),
                    score=item.get("score", 0.0)
                )
                for item in search_data
            ]
            await trigger(MemoryEvents.MEMORY_SEARCH_FINISHED,
                       scope_id=scope_id, user_id=user_id, query=query,
                       result_count=len(mem_results), search_type="user_mem")
            return mem_results
        except AttributeError as e:
            memory_logger.debug(
                "Search user mem has attribute exception.",
                event_type=LogEventType.MEMORY_RETRIEVE,
                exception=str(e),
                user_id=user_id,
                scope_id=scope_id,
                query=query,
            )
            return []
        except ValueError as e:
            memory_logger.warning(
                "Search user mem has value exception.",
                event_type=LogEventType.MEMORY_RETRIEVE,
                user_id=user_id,
                scope_id=scope_id,
                exception=str(e),
                query=query
            )
            return []
        except Exception as e:
            memory_logger.warning(
                "Search user mem has exception.",
                event_type=LogEventType.MEMORY_RETRIEVE,
                user_id=user_id,
                scope_id=scope_id,
                exception=str(e),
                query=query
            )
            return []

    @_fw.emit_before(MemoryEvents.MEMORY_SEARCH_STARTED)
    async def search_user_history_summary(
            self,
            query: str,
            num: int,
            user_id: str = DEFAULT_VALUE,
            scope_id: str = DEFAULT_VALUE,
            threshold: float = 0.3
    ) -> list[MemResult]:
        """
        Search user summary.

        Args:
            query: Search query string
            num: Number of results to return
            user_id: user identifier
            scope_id: scope identifier
            threshold: Minimum similarity threshold for results

        Returns:
            List of memory information
        """
        if not self._validate_id(event_type=LogEventType.MEMORY_RETRIEVE, scope_id=scope_id):
            memory_logger.error(
                "Invalid scope_id format.",
                event_type=LogEventType.MEMORY_RETRIEVE,
                query=query,
                memory_type=MemoryType.SUMMARY.value,
                user_id=user_id,
                scope_id=scope_id
            )
            return []
        semantic_store = await self._create_semantic_store_with_embedding(scope_id)
        if not self.search_manager:
            raise build_error(
                StatusCode.MEMORY_GET_MEMORY_EXECUTION_ERROR,
                memory_type="all",
                error_msg=f"search manager is not initialized",
            )
        params = SearchParams(
            query=query,
            scope_id=scope_id,
            top_k=num,
            user_id=user_id,
            threshold=threshold,
            search_type=MemoryType.SUMMARY.value
        )
        try:
            search_data = await self.search_manager.search(params, semantic_store)
            mem_results: list[MemResult] = [
                MemResult(
                    mem_info=MemInfo(
                        mem_id=item["id"],
                        content=item["mem"],
                        type=item.get("mem_type", MemoryType.SUMMARY)
                    ),
                    score=item.get("score", 0.0)
                )
                for item in search_data
            ]
            await trigger(MemoryEvents.MEMORY_SEARCH_FINISHED,
                       scope_id=scope_id, user_id=user_id, query=query,
                       result_count=len(mem_results), search_type="history_summary")
            return mem_results
        except AttributeError as e:
            memory_logger.debug(
                "Search user history summary has attribute exception.",
                event_type=LogEventType.MEMORY_RETRIEVE,
                exception=str(e),
                user_id=user_id,
                scope_id=scope_id,
                query=query,
                memory_type=MemoryType.SUMMARY.value
            )
            return []
        except ValueError as e:
            memory_logger.warning(
                "Search user history summary has value exception.",
                event_type=LogEventType.MEMORY_RETRIEVE,
                user_id=user_id,
                scope_id=scope_id,
                exception=str(e),
                memory_type=MemoryType.SUMMARY.value,
                query=query
            )
            return []
        except Exception as e:
            memory_logger.warning(
                "Search user history summary has exception.",
                event_type=LogEventType.MEMORY_RETRIEVE,
                user_id=user_id,
                scope_id=scope_id,
                exception=str(e),
                memory_type=MemoryType.SUMMARY.value,
                query=query
            )
            return []

    async def user_mem_total_num(self,
                                 user_id: str = DEFAULT_VALUE,
                                 scope_id: str = DEFAULT_VALUE) -> int:
        """
        return total number of user memory
        """
        if not self._validate_id(event_type=LogEventType.MEMORY_RETRIEVE, scope_id=scope_id):
            memory_logger.error(
                "Invalid scope_id format",
                event_type=LogEventType.MEMORY_RETRIEVE,
                user_id=user_id,
                scope_id=scope_id
            )
            return 0
        # Get all user profiles by using get_in_range with a large range
        search_data = await self.search_manager.list_user_profile(user_id=user_id,
                                                                  scope_id=scope_id)
        return len(search_data)

    async def get_user_mem_by_page(self,
                                   user_id: str = DEFAULT_VALUE,
                                   scope_id: str = DEFAULT_VALUE,
                                   page_size: int = 10,
                                   page_idx: int = 0,
                                   memory_type: MemoryType = MemoryType.UNKNOWN) -> list[MemInfo]:
        """
        List user memories with pagination support.

        Retrieves memories in chronological order, suitable for displaying
        conversation history or memory browsing interfaces.

        Args:
            user_id: User identifier to search within
            scope_id: Unique identifier for the scope
            page_size: Number of memories per page
            page_idx: Page index (0-based)
            memory_type: Memory type to filter. If UNKNOWN, no filtering is applied.

        Returns:
            List of memory information
        """
        if not self._validate_id(event_type=LogEventType.MEMORY_RETRIEVE, scope_id=scope_id):
            memory_logger.error(
                "Invalid scope_id format.",
                event_type=LogEventType.MEMORY_RETRIEVE,
                user_id=user_id,
                scope_id=scope_id,
                
            )
            return []
        if not self.search_manager:
            raise build_error(
                StatusCode.MEMORY_GET_MEMORY_EXECUTION_ERROR,
                memory_type="all",
                error_msg=f"search manager is not initialized",
            )

        if memory_type == MemoryType.UNKNOWN:
            search_memory_type = None
        else:
            search_memory_type = memory_type.value
        search_data = await self.search_manager.list_user_mem(user_id=user_id, scope_id=scope_id,
                                                              nums=page_size, pages=page_idx,
                                                              mem_type=search_memory_type)

        if not search_data:
            return []

        mem_results: list[MemInfo] = []
        for item in search_data:
            mem_type = item.get("mem_type", MemoryType.UNKNOWN.value)
            mem_results.append(
                MemInfo(
                    mem_id=item["id"],
                    content=item["mem"],
                    type=mem_type
                )
            )
        return mem_results

    async def update_variables(self,
                                   variables: dict[str, str],
                                   user_id: str = DEFAULT_VALUE,
                                   scope_id: str = DEFAULT_VALUE
                                   ):
        """
        Update user variables.

        Args:
            variables: variable name to value pairs
            user_id: User identifier to search within
            scope_id: Unique identifier for the scope
        """
        if not self._validate_id(event_type=LogEventType.MEMORY_UPDATE, scope_id=scope_id):
            memory_logger.error(
                "Invalid scope_id format.",
                event_type=LogEventType.MEMORY_UPDATE,
                user_id=user_id,
                scope_id=scope_id,
                memory_type=MemoryType.VARIABLE.value
            )
            return
        lock = DistributedLock(self.kv_store, f"user/{user_id}")
        async with lock:
            if not self.variable_manager:
                raise build_error(
                    StatusCode.MEMORY_UPDATE_MEMORY_EXECUTION_ERROR,
                    memory_type="variable",
                    error_msg=f"variable manager is not initialized",
                )
            for name, value in variables.items():
                await self.variable_manager.update_user_variable(
                    user_id=user_id,
                    scope_id=scope_id,
                    var_name=name,
                    var_mem=value
                )

    async def delete_variables(self,
                                   names: list[str],
                                   user_id: str = DEFAULT_VALUE,
                                   scope_id: str = DEFAULT_VALUE):
        """
        Delete user variables.

        Args:
            names: Name of the variables to delete
            user_id: User identifier to search within
            scope_id: Unique identifier for the scope
        """
        if not self._validate_id(event_type=LogEventType.MEMORY_DELETE, scope_id=scope_id):
            memory_logger.error(
                "Invalid scope_id format.",
                event_type=LogEventType.MEMORY_DELETE,
                user_id=user_id,
                scope_id=scope_id,
                memory_type=MemoryType.VARIABLE.value
            )
            return False
        lock = DistributedLock(self.kv_store, f"user/{user_id}")
        async with lock:
            if not self.variable_manager:
                raise build_error(
                    StatusCode.MEMORY_DELETE_MEMORY_EXECUTION_ERROR,
                    memory_type="variable",
                    error_msg=f"variable manager is not initialized",
                )
            for name in names:
                await self.variable_manager.delete_user_variable(user_id=user_id, scope_id=scope_id, var_name=name)
            return True

    @staticmethod
    def _get_llm_from_config(model_config: ModelRequestConfig,
                             model_client_config: ModelClientConfig):
        return Model(model_config=model_config, model_client_config=model_client_config)

    async def _get_scope_config(self, scope_id: str) -> MemoryScopeConfig | None:
        """
        Get the scope-specific configuration from memory cache first, then from kv_store if not found.

        Args:
            scope_id: Unique identifier for the scope

        Returns:
            MemoryScopeConfig: scope-specific configuration or None if not found
        """
        # First check if config is in memory cache
        if scope_id in self._scope_config:
            config = self._scope_config[scope_id]

            # Create a copy to avoid modifying the encrypted config in memory
            decrypted_config = copy.deepcopy(config)

            # Decrypt API keys if they exist
            if decrypted_config.model_client_cfg and decrypted_config.model_client_cfg.api_key:
                decrypted_config.model_client_cfg.api_key = BaseMemoryManager.decrypt_memory_if_needed(
                    key=self._sys_mem_config.crypto_key,
                    ciphertext=decrypted_config.model_client_cfg.api_key
                )

            if decrypted_config.embedding_cfg and decrypted_config.embedding_cfg.api_key:
                decrypted_config.embedding_cfg.api_key = BaseMemoryManager.decrypt_memory_if_needed(
                    key=self._sys_mem_config.crypto_key,
                    ciphertext=decrypted_config.embedding_cfg.api_key
                )

            return decrypted_config

        # If not in memory, get from kv_store
        return await self.get_scope_config(scope_id)

    async def _get_scope_embedding_model(self, scope_id: str) -> Embedding | None:
        """
        Get the embedding model for the scope from cache first, then from config if not found.

        Args:
            scope_id: scope/scope identifier

        Returns:
            APIEmbedModel: Embedding model for the scope, or None if no model is available
        """
        # Check if embedding model is already in cache
        if scope_id in self._scope_embedding:
            return self._scope_embedding[scope_id]

        try:
            config = await self._get_scope_config(scope_id)
            if config and config.embedding_cfg:
                # Use APIEmbedding to instantiate the embedding model
                embedding_model = APIEmbedding(config=config.embedding_cfg)
                # Cache the embedding model
                self._scope_embedding[scope_id] = embedding_model
                return embedding_model
        except Exception as e:
            memory_logger.error(
                "Failed to get or instantiate embedding model.",
                event_type=LogEventType.MEMORY_RETRIEVE,
                scope_id=scope_id,
                exception=str(e)
            )

        memory_logger.error(
            "No embedding model available.",
            event_type=LogEventType.MEMORY_RETRIEVE,
            scope_id=scope_id
        )
        return None

    async def _get_scope_llm(self, scope_id: str) -> Model:
        """
        Get LLM for the scope.

        Args:
            scope_id: scope/scope identifier

        Returns:
            Model: LLM instance
        """
        try:
            config = await self._get_scope_config(scope_id)

            if config and config.model_cfg and config.model_client_cfg:
                return LongTermMemory._get_llm_from_config(config.model_cfg, config.model_client_cfg)

            # If the LLM fails to be obtained, try to use the system default configuration.
            elif not self._sys_mem_config:
                pass
            elif not self._sys_mem_config.default_model_client_cfg:
                memory_logger.debug(
                    "Default model client config is missing, cannot instantiate LLM.",
                    event_type=LogEventType.MEMORY_RETRIEVE,
                    scope_id=scope_id
                )
            elif not self._sys_mem_config.default_model_cfg:
                memory_logger.debug(
                    "Default model config is missing, cannot instantiate LLM.",
                    event_type=LogEventType.MEMORY_RETRIEVE,
                    scope_id=scope_id
                )
            else:
                return LongTermMemory._get_llm_from_config(self._sys_mem_config.default_model_cfg,
                                                         self._sys_mem_config.default_model_client_cfg)
            return self._base_llm

        except Exception as e:
            memory_logger.error(
                "Failed to get scope LLM.",
                event_type=LogEventType.MEMORY_RETRIEVE,
                scope_id=scope_id,
                exception=str(e)
            )
            # If the LLM fails to be obtained, try to use the system default configuration.
            return self._base_llm

    async def _create_semantic_store_with_embedding(self, scope_id: str) -> SemanticStore:
        """
        Create a new semantic store instance and initialize it with the appropriate embedding model.

        Args:
            scope_id: Scope identifier

        Returns:
            SemanticStore: New semantic store instance with embedding model initialized
        """
        semantic_store = SemanticStore(vector_store=self.vector_store)
        embedding_model = await self._get_scope_embedding_model(scope_id)
        if embedding_model:
            semantic_store.initialize_embedding_model(embedding_model)
        elif self._base_embed:
            semantic_store.initialize_embedding_model(self._base_embed)
        else:
            pass
        return semantic_store

    def _check_messages(self, messages: list[BaseMessage]) -> Tuple[bool, list[BaseMessage]]:
        out_messages = []
        has_human_msg = False
        human_message: UserMessage = UserMessage()
        for msg in messages:
            if msg.role == human_message.role:
                out_messages.append(msg)
                has_human_msg = True
                continue
            msg.content = msg.content[:self._sys_mem_config.input_msg_max_len]
            out_messages.append(msg)

        return has_human_msg, out_messages

    async def _get_history_messages(self,
                                    user_id: str,
                                    scope_id: str,
                                    session_id: str,
                                    history_window_size: int
                                    ) -> list[BaseMessage]:
        threshold = history_window_size
        if not self.message_manager:
            return []
        history_messages_tuple = await self.message_manager.get(
            user_id=user_id,
            scope_id=scope_id,
            session_id=session_id,
            message_len=threshold
        )
        history_messages = []
        human_message: UserMessage = UserMessage()
        for msg, _ in history_messages_tuple:
            if msg.role == human_message.role:
                history_messages.append(msg)
                continue
            msg.content = msg.content[:self._sys_mem_config.input_msg_max_len]
            history_messages.append(msg)
        return history_messages

    @staticmethod
    def _validate_id(event_type: LogEventType, scope_id: str = "") -> bool:
        """
        Validate the scope_id format.

        Args:
            scope_id: Scope identifier

        Returns:
            True if the scope_id is valid, False otherwise.
        """
        if not scope_id:
            memory_logger.error(
                "Scope_id is invalid.",
                event_type=event_type,
                scope_id=scope_id
            )
            return False
        if "/" in scope_id:
            memory_logger.error(
                "Scope_id cannot contain separator '/'.",
                event_type=event_type,
                scope_id=scope_id
            )
            return False
        if len(scope_id) > 128:
            memory_logger.error(
                "Scope_id length exceeds limit (128).",
                event_type=event_type,
                scope_id=scope_id
            )
            return False
        return True

    async def _run_migration(self, migrate_func, store, store_type: str):
        """
        Execute a migration with unified logging and error handling.

        Args:
            migrate_func: The migration function to execute
            store: The store instance to pass to the migration function
            store_type: Type of store for error messages and logging (e.g., "kv store")
        """
        try:
            memory_logger.info(f"Starting {store_type} migration", event_type=LogEventType.MEMORY_INIT)
            await migrate_func(store)
            memory_logger.info(f"{store_type} migration completed successfully", event_type=LogEventType.MEMORY_INIT)
        except Exception as e:
            memory_logger.error(f"{store_type} migration failed", event_type=LogEventType.MEMORY_INIT, exception=str(e))
            raise build_error(
                StatusCode.MEMORY_REGISTER_STORE_EXECUTION_ERROR,
                store_type=store_type,
                error_msg=f"{store_type} migration failed: {str(e)}",
                cause=e
            ) from e