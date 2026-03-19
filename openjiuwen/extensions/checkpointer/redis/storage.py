# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from abc import ABC, abstractmethod
from typing import (
    Any,
    Dict,
    Optional,
    Tuple,
)

from openjiuwen.core.common.constants.constant import INTERACTIVE_INPUT
from openjiuwen.core.common.logging import logger
from openjiuwen.core.graph.store import (
    create_serializer,
    GraphState,
    Serializer,
    Store,
)
from openjiuwen.core.session import (
    BaseSession,
    InteractiveInput,
    NodeSession,
)
from openjiuwen.core.session.checkpointer import (
    build_key,
    build_key_with_namespace,
    SESSION_NAMESPACE_AGENT,
    SESSION_NAMESPACE_AGENT_GROUP,
    SESSION_NAMESPACE_WORKFLOW,
    Storage,
    WORKFLOW_NAMESPACE_GRAPH,
)
from openjiuwen.extensions.store.kv.redis_store import RedisStore

_DEFAULT_TTL = "default_ttl"
_SECONDS_PER_MINUTE = 60
_REFRESH_ON_READ = "refresh_on_read"


class BaseRedisStorage(Storage, ABC):
    """
    Base class for Redis-based storage implementations with common functionality.
    
    This class only interacts with RedisStore and does not directly use Redis client APIs.
    """

    def __init__(self, redis_store: RedisStore, ttl: Optional[dict[str, Any]] = None):
        """
        Initialize BaseRedisStorage with a RedisStore instance.

        Args:
            redis_store (RedisStore): The RedisStore instance for all Redis operations.
            ttl (Optional[dict[str, Any]]): Optional TTL configuration for stored data.
        """
        self._redis_store = redis_store
        self._serde: Serializer = create_serializer("pickle")
        self._ttl_seconds = None
        self._refresh_on_read = False
        if ttl and _DEFAULT_TTL in ttl:
            self._ttl_seconds = int(ttl.get(_DEFAULT_TTL) * _SECONDS_PER_MINUTE)
        if ttl and _REFRESH_ON_READ in ttl:
            self._refresh_on_read = True

    def _serialize_state(self, state: Any) -> Optional[Tuple[str, bytes]]:
        """Serialize state and return (dump_type, blob) tuple."""
        return self._serde.dumps_typed(state)

    def _decode_dump_type(self, dump_type: Any) -> str:
        """Decode dump_type from bytes to string if needed."""
        if isinstance(dump_type, bytes):
            return dump_type.decode("utf-8")
        return dump_type if dump_type is not None else ""

    def _deserialize_state(self, dump_type: Any, blob: Any) -> Any:
        """Deserialize state from (dump_type, blob) tuple."""
        if dump_type is None or blob is None:
            return None
        # Redis returns bytes, decode dump_type if needed
        dump_type_str = self._decode_dump_type(dump_type)
        try:
            return self._serde.loads_typed((dump_type_str, blob))
        except Exception as e:
            logger.error(f"Failed to deserialize state: {e}")
            return None

    async def _refresh_ttl(self, keys: list[str], entity_name: str, entity_id: str) -> None:
        """Refresh TTL for given keys if refresh_on_read is enabled."""
        if not (self._refresh_on_read and self._ttl_seconds) or not keys:
            return

        try:
            await self._redis_store.refresh_ttl(keys, self._ttl_seconds)
            logger.debug(f"Refreshed TTL for {entity_name} {entity_id}")
        except Exception as e:
            logger.warning(f"Failed to refresh TTL for {entity_name} {entity_id}: {e}")

    @staticmethod
    def _make_redis_key(*args):
        return ":".join(list(args))


class BaseSingleStateStorage(BaseRedisStorage, ABC):
    _KEY_NUMS = 2

    @property
    @abstractmethod
    def _namespace(self) -> str:
        ...

    @property
    @abstractmethod
    def _entity_name(self) -> str:
        ...

    @property
    @abstractmethod
    def _state_blobs_key(self) -> str:
        ...

    @property
    @abstractmethod
    def _state_dump_type_key(self) -> str:
        ...

    @abstractmethod
    def _get_entity_id(self, session: BaseSession) -> str:
        ...

    @abstractmethod
    def _get_state_to_save(self, session: BaseSession) -> Any:
        ...

    @abstractmethod
    def _restore_state(self, session: BaseSession, state: Any) -> None:
        ...

    def _build_state_keys(self, session_id: str, entity_id: str) -> tuple[str, str]:
        dump_type_key = build_key_with_namespace(
            session_id, self._namespace, entity_id, self._state_dump_type_key
        )
        blob_key = build_key_with_namespace(
            session_id, self._namespace, entity_id, self._state_blobs_key
        )
        return dump_type_key, blob_key

    async def save(self, session: BaseSession):
        state = self._get_state_to_save(session)
        session_id = session.session_id()
        entity_id = self._get_entity_id(session)

        state_blob = self._serialize_state(state)
        if not state_blob:
            logger.warning(f"Failed to serialize state for {self._entity_name} {entity_id}, session {session_id}")
            return

        try:
            dump_type, blob = state_blob
            pipeline = self._redis_store.pipeline()
            dump_type_key, blob_key = self._build_state_keys(session_id, entity_id)
            await (pipeline
                   .set(dump_type_key, dump_type, self._ttl_seconds)
                   .set(blob_key, blob, self._ttl_seconds)
                   .execute())
            logger.debug(f"Saved state for {self._entity_name} {entity_id}, session {session_id}")
        except Exception as e:
            logger.error(f"Failed to save state for {self._entity_name} {entity_id}, session {session_id}: {e}")
            raise

    async def recover(self, session: BaseSession, inputs: InteractiveInput = None):
        session_id = session.session_id()
        entity_id = self._get_entity_id(session)

        pipeline = self._redis_store.pipeline()
        dump_type_key, blob_key = self._build_state_keys(session_id, entity_id)
        results = await (pipeline
                         .get(dump_type_key)
                         .get(blob_key)
                         .execute())

        if len(results) != self._KEY_NUMS:
            logger.debug(
                f"Expected {self._KEY_NUMS} keys but got {len(results)} results "
                f"for {self._entity_name} {entity_id}, session {session_id}")
            return

        dump_type, blob = results[0], results[1]
        state = self._deserialize_state(dump_type, blob)
        if state is None:
            logger.debug(f"No state found for {self._entity_name} {entity_id}, session {session_id}")
            return

        try:
            self._restore_state(session, state)
            logger.debug(f"Recovered state for {self._entity_name} {entity_id}, session {session_id}")
        except Exception as e:
            logger.error(f"Failed to set state for {self._entity_name} {entity_id}, session {session_id}: {e}")
            raise
        finally:
            # Always try to refresh TTL if enabled, even if set_state failed
            await self._refresh_ttl([dump_type_key, blob_key], self._entity_name, entity_id)

    async def clear(self, entity_id: str, session_id: str):
        dump_type_key, blob_key = self._build_state_keys(session_id, entity_id)
        deleted = await self._redis_store.batch_delete([dump_type_key, blob_key])
        logger.debug(f"Cleared {deleted} keys for {self._entity_name} {entity_id}, session {session_id}")

    async def exists(self, session: BaseSession) -> bool:
        session_id = session.session_id()
        entity_id = self._get_entity_id(session)

        pipeline = self._redis_store.pipeline()
        dump_type_key, blob_key = self._build_state_keys(session_id, entity_id)
        results = await (pipeline
                         .exists(dump_type_key)
                         .exists(blob_key)
                         .execute())

        if len(results) != self._KEY_NUMS:
            return False

        # Both keys must exist for the state to be considered existing
        return results[0] == 1 and results[1] == 1


class AgentStorage(BaseSingleStateStorage):
    _namespace = SESSION_NAMESPACE_AGENT
    _entity_name = "agent"
    _state_blobs_key = "agent_state_blobs"
    _state_dump_type_key = "agent_state_blobs_dump_type"

    def _get_entity_id(self, session: BaseSession) -> str:
        return session.agent_id()

    def _get_state_to_save(self, session: BaseSession) -> Any:
        return session.state().get_state()

    def _restore_state(self, session: BaseSession, state: Any) -> None:
        session.state().set_state(state)


class AgentGroupStorage(BaseSingleStateStorage):
    _namespace = SESSION_NAMESPACE_AGENT_GROUP
    _entity_name = "agent_group"
    _state_blobs_key = "agent_group_state_blobs"
    _state_dump_type_key = "agent_group_state_blobs_dump_type"

    def _get_entity_id(self, session: BaseSession) -> str:
        return session.group_id()

    def _get_state_to_save(self, session: BaseSession) -> Any:
        return session.state().get_global(None)

    def _restore_state(self, session: BaseSession, state: Any) -> None:
        session.state().global_state.set_state(state)


class WorkflowStorage(BaseRedisStorage):
    _STATE_BLOBS = "workflow_state_blobs"
    _STATE_BLOBS_DUMP_TYPE = "workflow_state_blobs_dump_type"

    _UPDATE_BLOBS = "workflow_update_blobs"
    _UPDATE_BLOBS_DUMP_TYPE = "workflow_update_blobs_dump_type"

    _KEY_NUMS = 4

    def _process_interactive_inputs(self, session: BaseSession, inputs: InteractiveInput) -> None:
        """Process interactive inputs and update workflow state."""
        if inputs.raw_inputs is not None:
            session.state().update_and_commit_workflow_state({INTERACTIVE_INPUT: inputs.raw_inputs})
            return

        if not (hasattr(inputs, 'user_inputs') and inputs.user_inputs):
            return

        for node_id, value in inputs.user_inputs.items():
            node_session = NodeSession(session, node_id)
            interactive_input = node_session.state().get(INTERACTIVE_INPUT)
            if isinstance(interactive_input, list):
                interactive_input.append(value)
                node_session.state().update({INTERACTIVE_INPUT: interactive_input})
            else:
                node_session.state().update({INTERACTIVE_INPUT: [value]})
        session.state().commit()

    async def save(self, session: BaseSession):
        state = session.state().get_state()
        workflow_id = session.workflow_id()
        session_id = session.session_id()

        pipeline = self._redis_store.pipeline()
        has_operations = False

        state_blob = self._serialize_state(state)
        if state_blob:
            dump_type, blob = state_blob
            dump_type_key = build_key_with_namespace(
                session_id, SESSION_NAMESPACE_WORKFLOW, workflow_id, self._STATE_BLOBS_DUMP_TYPE
            )
            blob_key = build_key_with_namespace(
                session_id, SESSION_NAMESPACE_WORKFLOW, workflow_id, self._STATE_BLOBS
            )
            (pipeline
             .set(dump_type_key, dump_type, self._ttl_seconds)
             .set(blob_key, blob, self._ttl_seconds))
            has_operations = True
        else:
            logger.warning(f"Failed to serialize state for workflow {workflow_id}, session {session_id}")

        updates = session.state().get_updates()
        updates_blob = self._serialize_state(updates)
        if updates_blob:
            dump_type, blob = updates_blob
            dump_type_key = build_key_with_namespace(
                session_id, SESSION_NAMESPACE_WORKFLOW, workflow_id, self._UPDATE_BLOBS_DUMP_TYPE
            )
            blob_key = build_key_with_namespace(
                session_id, SESSION_NAMESPACE_WORKFLOW, workflow_id, self._UPDATE_BLOBS
            )
            (pipeline
             .set(dump_type_key, dump_type, self._ttl_seconds)
             .set(blob_key, blob, self._ttl_seconds))
            has_operations = True

        if has_operations:
            try:
                await pipeline.execute()
                logger.debug(f"Saved state for workflow {workflow_id}, session {session_id}")
            except Exception as e:
                logger.error(f"Failed to save state for workflow {workflow_id}, session {session_id}: {e}")
                raise

    async def recover(self, session: BaseSession, inputs: InteractiveInput = None):
        workflow_id = session.workflow_id()
        session_id = session.session_id()

        pipeline = self._redis_store.pipeline()
        state_dump_type_key = build_key_with_namespace(
            session_id, SESSION_NAMESPACE_WORKFLOW, workflow_id, self._STATE_BLOBS_DUMP_TYPE
        )
        state_blob_key = build_key_with_namespace(
            session_id, SESSION_NAMESPACE_WORKFLOW, workflow_id, self._STATE_BLOBS
        )
        updates_dump_type_key = build_key_with_namespace(
            session_id, SESSION_NAMESPACE_WORKFLOW, workflow_id, self._UPDATE_BLOBS_DUMP_TYPE
        )
        updates_blob_key = build_key_with_namespace(
            session_id, SESSION_NAMESPACE_WORKFLOW, workflow_id, self._UPDATE_BLOBS
        )
        results = await (pipeline
                         .get(state_dump_type_key)
                         .get(state_blob_key)
                         .get(updates_dump_type_key)
                         .get(updates_blob_key)
                         .execute())

        if len(results) != self._KEY_NUMS:
            logger.warning(
                f"Expected {self._KEY_NUMS} keys but got {len(results)} results "
                f"for workflow {workflow_id}, session {session_id}")
            return

        # Recover state
        state_dump_type, state_blob = results[0], results[1]
        state_dump_type_str = self._decode_dump_type(state_dump_type)

        if state_blob and state_dump_type_str and state_dump_type_str != "empty":
            try:
                state = self._deserialize_state(state_dump_type_str, state_blob)
                if state is not None:
                    session.state().set_state(state)
            except Exception as e:
                logger.error(f"Failed to deserialize state for workflow {workflow_id}, session {session_id}: {e}")
                # Continue execution even if state deserialization fails
            finally:
                # Always refresh TTL if data was read, even if deserialization failed
                await self._refresh_ttl([state_dump_type_key, state_blob_key], "workflow", workflow_id)

        # Process interactive inputs
        if inputs is not None:
            self._process_interactive_inputs(session, inputs)

        # Recover updates
        updates_dump_type, updates_blob = results[2], results[3]
        updates_dump_type_str = self._decode_dump_type(updates_dump_type)

        if updates_blob and updates_dump_type_str and updates_dump_type_str != "empty":
            try:
                state_updates = self._deserialize_state(updates_dump_type_str, updates_blob)
                if state_updates is not None:
                    session.state().set_updates(state_updates)
            except Exception as e:
                logger.error(f"Failed to deserialize updates for workflow {workflow_id}, session {session_id}: {e}")
                # Continue execution even if updates deserialization fails
            finally:
                # Always refresh TTL for updates keys if data was read, even if deserialization failed
                await self._refresh_ttl([updates_dump_type_key, updates_blob_key], "workflow updates", workflow_id)

    async def clear(self, workflow_id: str, session_id: str):
        state_dump_type_key = build_key_with_namespace(
            session_id, SESSION_NAMESPACE_WORKFLOW, workflow_id, self._STATE_BLOBS_DUMP_TYPE
        )
        state_blob_key = build_key_with_namespace(
            session_id, SESSION_NAMESPACE_WORKFLOW, workflow_id, self._STATE_BLOBS
        )
        state_updates_dump_type_key = build_key_with_namespace(
            session_id, SESSION_NAMESPACE_WORKFLOW, workflow_id, self._UPDATE_BLOBS_DUMP_TYPE
        )
        state_updates_blob_key = build_key_with_namespace(
            session_id, SESSION_NAMESPACE_WORKFLOW, workflow_id, self._UPDATE_BLOBS
        )
        # Use batch_delete for multiple keys
        deleted = await self._redis_store.batch_delete([
            state_dump_type_key, state_blob_key,
            state_updates_dump_type_key, state_updates_blob_key
        ])
        logger.debug(f"Cleared {deleted} keys for workflow {workflow_id}, session {session_id}")

    async def exists(self, session: BaseSession) -> bool:
        workflow_id = session.workflow_id()
        session_id = session.session_id()

        pipeline = self._redis_store.pipeline()
        # Check state keys
        state_dump_type_key = build_key_with_namespace(
            session_id, SESSION_NAMESPACE_WORKFLOW, workflow_id, self._STATE_BLOBS_DUMP_TYPE
        )
        state_blob_key = build_key_with_namespace(
            session_id, SESSION_NAMESPACE_WORKFLOW, workflow_id, self._STATE_BLOBS
        )
        # Check updates keys (optional)
        state_updates_dump_type_key = build_key_with_namespace(
            session_id, SESSION_NAMESPACE_WORKFLOW, workflow_id, self._UPDATE_BLOBS_DUMP_TYPE
        )
        state_updates_blob_key = build_key_with_namespace(
            session_id, SESSION_NAMESPACE_WORKFLOW, workflow_id, self._UPDATE_BLOBS
        )
        results = await (pipeline
                         .exists(state_dump_type_key)
                         .exists(state_blob_key)
                         .exists(state_updates_dump_type_key)
                         .exists(state_updates_blob_key)
                         .execute())

        if len(results) != self._KEY_NUMS:
            return False

        # At least state keys must exist for the workflow state to be considered existing
        # Updates are optional, so we only require state keys to exist
        return results[0] == 1 and results[1] == 1


class GraphStore(Store):
    """
    Redis-based graph state store implementation.
    
    This class only interacts with RedisStore and does not directly use Redis client APIs.
    Graph state keys are structured as: session:workflow_id:graph:workflow_id:suffix
    This separates graph state from workflow's own state which is under session namespace.
    """

    _DATA_TYPE = "checkpoint_data_type"
    _DATA_VALUE = "checkpoint_data_value"
    _KEY_NUMS = 2

    def __init__(
            self,
            redis_store: RedisStore,
            ttl: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Initialize GraphStore with a RedisStore instance.

        Args:
            redis_store (RedisStore): The RedisStore instance for all Redis operations.
            ttl (Optional[Dict[str, Any]]): Optional TTL configuration for stored data.
        """
        self._redis_store = redis_store
        self._serde: Serializer = create_serializer("pickle")
        self._ttl_seconds = None
        self._refresh_on_read = False
        if ttl and _DEFAULT_TTL in ttl:
            self._ttl_seconds = int(ttl.get(_DEFAULT_TTL) * _SECONDS_PER_MINUTE)
        if ttl and _REFRESH_ON_READ in ttl:
            self._refresh_on_read = True

    async def get(self, session_id: str, ns: str) -> Optional[GraphState]:
        pipeline = self._redis_store.pipeline()
        key_type = build_key_with_namespace(session_id, WORKFLOW_NAMESPACE_GRAPH, ns, self._DATA_TYPE)
        key_value = build_key_with_namespace(session_id, WORKFLOW_NAMESPACE_GRAPH, ns, self._DATA_VALUE)
        results = await (pipeline
                         .get(key_type)
                         .get(key_value)
                         .execute())
        if len(results) != self._KEY_NUMS:
            logger.error(f"Redis expected {self._KEY_NUMS} keys but got {len(results)} results")
            return None
        _type, _value = results
        if not _type or not _value:
            logger.debug(f"Not found in redis: {_type}, {_value}, input session_id: {session_id}, ns: {ns}")
            return None

        # Decode type if it's bytes (Redis returns bytes)
        if isinstance(_type, bytes):
            _type_str = _type.decode("utf-8")
        else:
            _type_str = _type if _type is not None else ""

        # Deserialize graph state using Serializer (consistent with AgentStorage)
        # Use try-finally to ensure TTL is refreshed even if deserialization fails
        try:
            graph_state = self._deserialize_graph_state(_type_str, _value)
            if graph_state is None:
                logger.debug(f"Failed to deserialize graph state for session {session_id}, ns {ns}")
                return None
            return graph_state

        finally:
            # Always refresh TTL if data was read, even if deserialization failed
            await self._refresh_ttl([key_type, key_value], session_id, ns)

    async def save(self, session_id: str, ns: str, state: GraphState) -> None:
        """Save graph state to Redis."""
        # Serialize graph state using Serializer (consistent with AgentStorage)
        serialized = self._serialize_graph_state(state)
        if not serialized:
            logger.warning(f"Failed to serialize graph state for session {session_id}, ns {ns}")
            return

        try:
            dump_type, blob = serialized
            key_type = build_key_with_namespace(session_id, WORKFLOW_NAMESPACE_GRAPH, ns, self._DATA_TYPE)
            pipeline = self._redis_store.pipeline()
            key_value = build_key_with_namespace(session_id, WORKFLOW_NAMESPACE_GRAPH, ns, self._DATA_VALUE)
            await (pipeline
                   .set(key_type, dump_type, self._ttl_seconds)
                   .set(key_value, blob, self._ttl_seconds)
                   .execute())
            logger.debug(f"Saved graph state for session {session_id}, ns {ns}")
        except Exception as e:
            logger.error(f"Failed to save graph state for session {session_id}, ns {ns}: {e}")
            raise

    async def delete(self, session_id: str, ns: str | None = None) -> None:
        """
        Delete graph state keys for the given session_id and namespace.
        
        Args:
            session_id: Session identifier.
            ns: Namespace identifier. If None or empty, deletes all graph state data
                for the session_id (all namespaces under this session).
        """
        if not ns:
            # Delete all graph state data for this session_id
            prefix = build_key(session_id, WORKFLOW_NAMESPACE_GRAPH)
            await self._redis_store.delete_by_prefix(prefix, batch_size=500)
            logger.debug(f"Deleted keys for session {session_id} (all namespaces)")
        else:
            # Delete specific namespace
            prefix = build_key_with_namespace(session_id, WORKFLOW_NAMESPACE_GRAPH, ns)
            await self._redis_store.delete_by_prefix(prefix, batch_size=500)
            logger.debug(f"Deleted keys for session {session_id}, ns {ns}")

    async def _refresh_ttl(self, keys: list[str], session_id: str, ns: str) -> None:
        """Refresh TTL for given keys if refresh_on_read is enabled."""
        if not (self._refresh_on_read and self._ttl_seconds) or not keys:
            return

        try:
            await self._redis_store.refresh_ttl(keys, self._ttl_seconds)
            logger.debug(f"Refreshed TTL for session {session_id}, ns {ns}")
        except Exception as e:
            logger.warning(f"Failed to refresh TTL for session {session_id}, ns {ns}: {e}")

    def _serialize_graph_state(self, graph_state: GraphState) -> Optional[Tuple[str, bytes]]:
        """Serialize graph state and return (dump_type, blob) tuple."""
        return self._serde.dumps_typed(graph_state)

    def _deserialize_graph_state(self, dump_type: str, blob: Any) -> Optional[GraphState]:
        """Deserialize graph state from (dump_type, blob) tuple."""
        if not dump_type or blob is None:
            return None
        try:
            return self._serde.loads_typed((dump_type, blob))
        except Exception as e:
            logger.error(f"Failed to deserialize graph state: {e}")
            return None
