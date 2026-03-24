# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import (
    Any,
    Optional,
    Union,
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from redis.asyncio.client import Redis
from redis.asyncio.cluster import RedisCluster

from openjiuwen.core.common.constants.constant import INTERACTIVE_INPUT
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.graph.pregel import TASK_STATUS_INTERRUPT
from openjiuwen.core.session import (
    BaseSession,
    Checkpointer,
    InteractiveInput,
)
from openjiuwen.core.session.checkpointer import (
    CheckpointerFactory,
    CheckpointerProvider,
)
from openjiuwen.core.session.constants import FORCE_DEL_WORKFLOW_STATE_KEY
from openjiuwen.extensions.checkpointer.redis.storage import (
    AgentStorage,
    AgentGroupStorage,
    GraphStore,
    WorkflowStorage,
)
from openjiuwen.extensions.store.kv import RedisStore


class RedisTTLConfig(BaseModel):
    """
    TTL (Time To Live) configuration for Redis stored data.
    
    Attributes:
        default_ttl: Default TTL in minutes. If set, all stored data will have
                    this expiration time.
        refresh_on_read: If True, TTL will be refreshed when data is read.
                        This extends the lifetime of frequently accessed data.
    """
    default_ttl: Optional[float] = Field(
        default=None,
        description="Default TTL in minutes for stored data"
    )
    refresh_on_read: bool = Field(
        default=False,
        description="Whether to refresh TTL when data is read"
    )


class RedisConnectionConfig(BaseModel):
    """
    Redis connection configuration.
    
    This class provides a structured way to configure Redis connections
    with validation and type safety.
    
    Attributes:
        redis_client: Pre-configured Redis or RedisCluster client instance.
                     If provided, other connection parameters are ignored.
        url: Redis connection URL. Can be a standalone URL (redis://) or
             cluster URL (redis+cluster:// or rediss+cluster://).
        cluster_mode: Explicitly enable/disable cluster mode.
                     If None, auto-detected from URL scheme.
        connection_args: Additional connection arguments passed to Redis client.
    
    Examples:
        Standalone Redis:
        >>> config = RedisConnectionConfig(url="redis://localhost:6379")
        
        Cluster mode with explicit flag:
        >>> config = RedisConnectionConfig(
        ...     url="redis://localhost:7000",
        ...     cluster_mode=True
        ... )
        
        Cluster mode with URL scheme:
        >>> config = RedisConnectionConfig(url="redis+cluster://localhost:7000")
        
        With pre-configured client:
        >>> redis_client = Redis.from_url("redis://localhost:6379")
        >>> config = RedisConnectionConfig(redis_client=redis_client)
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)

    redis_client: Optional[Union[Redis, RedisCluster]] = Field(
        default=None,
        description="Pre-configured Redis or RedisCluster client instance"
    )
    url: Optional[str] = Field(
        default=None,
        description="Redis connection URL (redis:// or redis+cluster://)"
    )
    cluster_mode: Optional[bool] = Field(
        default=None,
        description="Explicitly enable/disable cluster mode (auto-detected if None)"
    )
    connection_args: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional connection arguments for Redis client"
    )

    @field_validator('url')
    @classmethod
    def validate_url(cls, v: Optional[str]) -> Optional[str]:
        """Validate URL format."""
        if v is None:
            return v
        # Basic URL validation
        if not (v.startswith("redis://") or
                v.startswith("rediss://") or
                v.startswith("redis+cluster://") or
                v.startswith("rediss+cluster://")):
            raise ValueError(
                f"Invalid Redis URL format: {v}. "
                "URL must start with redis://, rediss://, "
                "redis+cluster://, or rediss+cluster://"
            )
        return v

    @model_validator(mode='after')
    def validate_config(self) -> 'RedisConnectionConfig':
        """Validate that at least one connection method is provided."""
        if self.redis_client is None and self.url is None:
            raise ValueError(
                "Either 'redis_client' or 'url' must be provided in RedisConnectionConfig"
            )
        return self

    def is_cluster_mode(self) -> bool:
        """
        Determine if cluster mode should be used.
        
        Returns:
            bool: True if cluster mode should be used, False otherwise.
        """
        if self.redis_client is not None:
            return isinstance(self.redis_client, RedisCluster)

        if self.cluster_mode is not None:
            return self.cluster_mode

        if self.url is not None:
            return (self.url.startswith("redis+cluster://") or
                    self.url.startswith("rediss+cluster://"))

        return False

    def get_connection_url(self) -> Optional[str]:
        """
        Get the connection URL, normalizing cluster URLs if needed.
        
        Returns:
            Optional[str]: Normalized connection URL.
        """
        if self.url is None:
            return None

        # Remove +cluster suffix for RedisCluster.from_url
        if self.url.startswith("redis+cluster://"):
            return self.url.replace("redis+cluster://", "redis://")
        elif self.url.startswith("rediss+cluster://"):
            return self.url.replace("rediss+cluster://", "rediss://")

        return self.url


class RedisCheckpointerConfig(BaseModel):
    """
    Complete configuration for Redis checkpointer.
    
    This class provides a structured, type-safe configuration for Redis checkpointer
    with automatic validation and sensible defaults.
    
    Attributes:
        connection: Redis connection configuration.
        ttl: TTL configuration for stored data.
    
    Examples:
        Minimal configuration (standalone Redis):
        >>> config = RedisCheckpointerConfig(
        ...     connection=RedisConnectionConfig(url="redis://localhost:6379")
        ... )
        
        With TTL configuration:
        >>> config = RedisCheckpointerConfig(
        ...     connection=RedisConnectionConfig(url="redis://localhost:6379"),
        ...     ttl=RedisTTLConfig(default_ttl=5, refresh_on_read=True)
        ... )
        
        Cluster mode:
        >>> config = RedisCheckpointerConfig(
        ...     connection=RedisConnectionConfig(
        ...         url="redis://localhost:7000",
        ...         cluster_mode=True
        ...     )
        ... )
        
        Using pre-configured client:
        >>> redis_client = Redis.from_url("redis://localhost:6379")
        >>> config = RedisCheckpointerConfig(
        ...     connection=RedisConnectionConfig(redis_client=redis_client),
        ...     ttl=RedisTTLConfig(default_ttl=10)
        ... )
    """
    connection: RedisConnectionConfig = Field(
        ...,
        description="Redis connection configuration"
    )
    ttl: Optional[RedisTTLConfig] = Field(
        default=None,
        description="TTL configuration for stored data"
    )


@CheckpointerFactory.register("redis")
class RedisCheckpointerProvider(CheckpointerProvider):
    """
    Provider for creating Redis-based checkpointers.
    
    Supports both standalone Redis and Redis Cluster modes.
    Uses structured configuration with automatic validation.
    """

    async def create(self, conf: dict) -> Checkpointer:
        """
        Create a RedisCheckpointer instance.
        
        Configuration format:
            {
                "connection": {
                    "redis_client": Redis(...),  # Optional: Pre-configured client
                    "url": "redis://...",  # Required if redis_client not provided
                    "cluster_mode": True,  # Optional: Auto-detected from URL if None
                    "connection_args": {...}  # Optional: Additional connection args
                },
                "ttl": {  # Optional
                    "default_ttl": 5,  # Optional: TTL in minutes
                    "refresh_on_read": True  # Optional: Refresh TTL on read
                }
            }
        
        Args:
            conf (dict): Configuration dictionary with 'connection' and optional 'ttl' keys.
                        The 'connection' dict must contain either 'redis_client' or 'url'.
        
        Returns:
            Checkpointer: A RedisCheckpointer instance.
        
        Raises:
            ValueError: If configuration is invalid or missing required fields.
        
        Examples:
            Standalone Redis:
            >>> conf = {
            ...     "connection": {"url": "redis://localhost:6379"}
            ... }
            
            Cluster mode:
            >>> conf = {
            ...     "connection": {
            ...         "url": "redis://localhost:7000",
            ...         "cluster_mode": True
            ...     }
            ... }
            
            With TTL:
            >>> conf = {
            ...     "connection": {"url": "redis://localhost:6379"},
            ...     "ttl": {"default_ttl": 5, "refresh_on_read": True}
            ... }
        """
        # Parse and validate configuration
        try:
            config = RedisCheckpointerConfig.model_validate(conf)
        except Exception as e:
            raise ValueError(
                f"Invalid Redis checkpointer configuration: {e}. "
                "Configuration must have a 'connection' key with either 'redis_client' or 'url'. "
                "Optional 'ttl' key for TTL configuration."
            ) from e

        connection = config.connection

        # If redis_client is provided, use it directly
        if connection.redis_client is not None:
            redis_store = RedisStore(connection.redis_client)
            ttl_dict = config.ttl.model_dump() if config.ttl else None
            return RedisCheckpointer(redis_store, ttl_dict)

        # Get connection URL
        connection_url = connection.get_connection_url()
        if connection_url is None:
            raise ValueError(
                "Either 'redis_client' or 'url' must be provided in connection configuration"
            )

        # Determine cluster mode
        is_cluster = connection.is_cluster_mode()

        # Create appropriate Redis client
        try:
            if is_cluster:
                # Create Redis Cluster client
                redis = RedisCluster.from_url(
                    connection_url,
                    **connection.connection_args
                )
            else:
                # Create standalone Redis client
                redis = Redis.from_url(
                    connection_url,
                    **connection.connection_args
                )
        except Exception as e:
            raise ValueError(
                f"Failed to create Redis client: {e}. "
                f"URL: {connection_url}, Cluster mode: {is_cluster}"
            ) from e

        # Create RedisStore instance
        redis_store = RedisStore(redis)
        ttl_dict = config.ttl.model_dump() if config.ttl else None
        return RedisCheckpointer(redis_store, ttl_dict)


class RedisCheckpointer(Checkpointer):
    """
    Redis-based checkpointer implementation.
    
    This checkpointer only interacts with RedisStore and does not directly use
    Redis client APIs. All Redis operations are performed through RedisStore.
    """

    def __init__(self,
                 redis_store: RedisStore,
                 ttl: Optional[dict[str, Any]] = None):
        """
        Initialize RedisCheckpointer with a RedisStore instance.

        Args:
            redis_store (RedisStore): The RedisStore instance for all Redis operations.
            ttl (Optional[dict[str, Any]]): Optional TTL configuration for stored data.
        """
        self._redis_store = redis_store
        # All storage classes now use RedisStore instead of direct Redis client
        self._agent_storage = AgentStorage(redis_store, ttl)
        self._agent_group_storage = AgentGroupStorage(redis_store, ttl)
        self._workflow_storage = WorkflowStorage(redis_store, ttl)
        self._graph_state = GraphStore(redis_store, ttl)

    async def pre_agent_execute(self, session: BaseSession, inputs):
        agent_id = session.agent_id()  # type: ignore[attr-defined]
        logger.info(
            f"agent: {agent_id} create or restore checkpoint from session: {session.session_id()}")
        await self._agent_storage.recover(session)
        if inputs is not None:
            session.state().update({INTERACTIVE_INPUT: [inputs]})

    async def pre_agent_team_execute(self, session: BaseSession, inputs):
        logger.info(
            f"agent group: {session.group_id()} create or restore checkpoint from session: {session.session_id()}"
        )
        await self._agent_group_storage.recover(session)
        if inputs is not None:
            session.state().update_global({INTERACTIVE_INPUT: [inputs]})

    async def interrupt_agent_execute(self, session: BaseSession):
        logger.info(f"interaction required, save checkpoint for "
                    f"agent: {session.agent_id()} in session: {session.session_id()}")  # type: ignore[attr-defined]
        await self._agent_storage.save(session)

    async def post_agent_execute(self, session: BaseSession):
        logger.info(f"agent finished, save checkpoint for "
                    f"agent: {session.agent_id()} in session: {session.session_id()}")  # type: ignore[attr-defined]
        await self._agent_storage.save(session)

    async def post_agent_team_execute(self, session: BaseSession):
        logger.info(
            f"agent group finished, save checkpoint for group: {session.group_id()} "
            f"in session: {session.session_id()}"
        )
        await self._agent_group_storage.save(session)

    async def pre_workflow_execute(self, session: BaseSession, inputs: InteractiveInput):
        """
        Prepare workflow execution by recovering or clearing workflow state.
        
        If inputs is an InteractiveInput, recover the workflow state.
        If inputs is not an InteractiveInput and workflow state exists:
            - If FORCE_DEL_WORKFLOW_STATE_KEY is True, delete graph state and workflow state
            - Otherwise, raise WORKFLOW_STATE_INVALID exception
        
        Args:
            session (BaseSession): The session for the workflow.
            inputs (InteractiveInput): The input for the workflow execution.
        """
        workflow_id = session.workflow_id()  # type: ignore[attr-defined]
        logger.info(f"workflow: {workflow_id} create or restore checkpoint from "
                    f"session: {session.session_id()}")
        if isinstance(inputs, InteractiveInput):
            await self._workflow_storage.recover(session, inputs)
        else:
            # Check if workflow state exists
            if not await self._workflow_storage.exists(session):
                return

            # If FORCE_DEL_WORKFLOW_STATE_KEY is enabled, delete the state
            if session.config().get_env(FORCE_DEL_WORKFLOW_STATE_KEY, False):
                workflow_id = session.workflow_id()  # type: ignore[attr-defined]
                if workflow_id is None:
                    logger.warning(f"workflow_id is None for session: {session.session_id()}")
                    return
                session_id = session.session_id()
                await self._graph_state.delete(session_id, workflow_id)
                await self._workflow_storage.clear(workflow_id, session_id)
                logger.info(
                    f"Force deleted workflow state for workflow: {workflow_id} "
                    f"in session: {session_id}"
                )
            else:
                # Raise exception if state exists but cleanup is disabled
                raise build_error(
                    StatusCode.CHECKPOINTER_PRE_WORKFLOW_EXECUTION_ERROR,
                    workflow=workflow_id,
                    reason="workflow state exists but non-interactive input and cleanup is disabled"
                )

    async def post_workflow_execute(self, session: BaseSession, result, exception):
        workflow_id = session.workflow_id()  # type: ignore[attr-defined]
        session_id = session.session_id()

        if exception is not None:
            logger.info(f"exception in workflow, save checkpoint for "
                        f"workflow: {workflow_id} in session: {session_id}")
            await self._workflow_storage.save(session)
            raise exception

        if result.get(TASK_STATUS_INTERRUPT) is None:
            logger.info(f"clear checkpoint for workflow: {workflow_id} in session: {session_id}")
            await self._graph_state.delete(session_id, workflow_id)
            await self._workflow_storage.clear(workflow_id, session_id)
        else:
            logger.info(f"interaction required, save checkpoint for "
                        f"workflow: {workflow_id} in session: {session_id}")
            await self._workflow_storage.save(session)

    async def session_exists(self, session_id: str) -> bool:
        """
        Check if a session exists in Redis.
        
        This method checks if there are any keys associated with the given session_id
        in Redis. It uses prefix-based lookup to find any keys starting with the
        session_id pattern.
        
        Args:
            session_id (str): The session ID to check.
        
        Returns:
            bool: True if the session exists (has associated keys), False otherwise.
        """
        if self._redis_store is None:
            return False

        # Check if any keys exist with the session_id prefix
        prefix = f"{session_id}:"
        keys = await self._redis_store.get_by_prefix(prefix)
        return len(keys) > 0

    async def release(self, session_id: str, agent_id: Optional[str] = None):
        """
        Release resources for a session, optionally for a specific agent.
        
        All Redis operations are performed through RedisStore.
        
        Args:
            session_id (str): The session ID to release resources for.
            agent_id (str, optional): If provided, only release resources for this specific agent.
        """
        if self._redis_store is None:
            logger.warning("Cannot release resources: RedisStore is None")
            return

        if agent_id is not None:
            logger.info(f"clear checkpoint for agent: {agent_id} in session: {session_id}")
            await self._agent_storage.clear(agent_id, session_id)  # type: ignore[arg-type]
        else:
            logger.info(f"clear session: {session_id}")
            # Delete all keys matching the session prefix in batches using RedisStore
            prefix = f"{session_id}:"
            await self._redis_store.delete_by_prefix(prefix, batch_size=500)
            logger.debug(f"Released all resources for session {session_id}")

    def graph_store(self):
        return self._graph_state
