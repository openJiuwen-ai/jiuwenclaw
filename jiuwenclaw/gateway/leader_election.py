# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Distributed Leader Election using Redis SETNX + TTL for PRIMARY/STANDBY failover."""

from __future__ import annotations

import asyncio
import logging
import uuid
from enum import Enum
from typing import Callable, Awaitable

from jiuwenclaw.config import get_config

logger = logging.getLogger(__name__)


class Role(Enum):
    """Leader election role states."""
    PRIMARY = "primary"
    STANDBY = "standby"


class LeaderElection:
    """Distributed leader election using Redis SETNX + TTL.

    Redis lock algorithm:
    - Lock acquisition: SET {instance_id}:gateway:leader <instance_id:uuid> NX EX 30
    - Lock renewal: EXPIRE {instance_id}:gateway:leader 30 (PRIMARY runs every 10s)
    - Lock release: DEL {instance_id}:gateway:leader (only if value matches)
    - Lock competition: STANDBY tries SET {instance_id}:gateway:leader <instance_id:uuid> NX EX 30 every 5s

    A single leader loop task handles both PRIMARY renewal and STANDBY competition,
    promoting/demoting as needed without creating new tasks.
    """

    DEFAULT_LOCK_KEY = "gateway:leader"
    DEFAULT_LOCK_TTL_SECONDS = 30
    DEFAULT_RENEWAL_INTERVAL_SECONDS = 10
    DEFAULT_COMPETITION_INTERVAL_SECONDS = 5

    _instance: "LeaderElection | None" = None

    @staticmethod
    def _with_instance_prefix(instance_id: str, lock_key: str) -> str:
        iid = str(instance_id or "").strip()
        if not iid:
            return str(lock_key or "").strip()
        key = str(lock_key or "").strip()
        if key.startswith(f"{iid}:"):
            return key
        return f"{iid}:{key}"

    def __init__(self) -> None:
        """Initialize LeaderElection."""
        self._instance_id = self._get_default_instance_id()
        self._lock_key = self._with_instance_prefix(self._instance_id, self.DEFAULT_LOCK_KEY)
        self._lock_ttl = self.DEFAULT_LOCK_TTL_SECONDS
        self._renewal_interval = self.DEFAULT_RENEWAL_INTERVAL_SECONDS
        self._competition_interval = self.DEFAULT_COMPETITION_INTERVAL_SECONDS

        self._role: Role = Role.STANDBY
        self._lock_value: str = f"{self._instance_id}:{uuid.uuid4()}"
        self._callbacks: list[Callable[[Role], Awaitable[None]]] = []

        self._redis_store = None
        self._leader_task: asyncio.Task | None = None
        self._running = False

        self._load_config()

    @staticmethod
    def _get_default_instance_id() -> str:
        """Get default instance_id from config.yaml gateway.instance_id, fallback to hostname."""
        config = get_config()
        gateway_config = config.get("gateway", {}) if isinstance(config, dict) else {}
        instance_id = gateway_config.get("instance_id", "")
        if instance_id:
            return str(instance_id)
        import socket
        try:
            return socket.gethostname()
        except Exception:
            return "unknown"

    @classmethod
    def get_instance(cls) -> "LeaderElection":
        """Get singleton instance. Creates from config on first call."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _load_config(self) -> None:
        """Load config from config.yaml and inject into instance attributes."""
        config = get_config()
        le_config = config.get("leader_election", {}) if isinstance(config, dict) else {}

        configured_lock_key = le_config.get("lock_key", self.DEFAULT_LOCK_KEY)
        self._lock_key = self._with_instance_prefix(self._instance_id, configured_lock_key)
        self._lock_ttl = le_config.get("lock_ttl_seconds", self.DEFAULT_LOCK_TTL_SECONDS)
        renew_key = "renewal_interval_seconds"
        self._renewal_interval = le_config.get(renew_key, self.DEFAULT_RENEWAL_INTERVAL_SECONDS)
        comp_key = "competition_interval_seconds"
        self._competition_interval = le_config.get(comp_key, self.DEFAULT_COMPETITION_INTERVAL_SECONDS)

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (for testing only)."""
        cls._instance = None

    @property
    def role(self) -> Role:
        """Current role (PRIMARY or STANDBY)."""
        return self._role

    @property
    def is_primary(self) -> bool:
        """Whether current role is PRIMARY."""
        return self._role == Role.PRIMARY

    @property
    def instance_id(self) -> str:
        """This Gateway instance's unique identifier."""
        return self._instance_id

    async def _get_redis_store(self):
        """Lazily initialize RedisStore from config.yaml redis section."""
        if self._redis_store is None:
            from openjiuwen.extensions.store.kv.redis_store import RedisStore

            config = get_config()
            redis_cfg = config.get("redis", {}) if isinstance(config, dict) else {}
            password = redis_cfg.get("password") or None
            mode = str(redis_cfg.get("mode") or "standalone").strip().lower()

            if mode == "cluster":
                from redis.asyncio.cluster import RedisCluster  # noqa: PLC0415
                from redis.cluster import ClusterNode  # noqa: PLC0415
                # LeaderElection 直接读 raw redis 配置(不走 RedisConfig.from_mapping),
                # 需自行解析逗号分隔的 startup_nodes 字符串
                raw_nodes = str(redis_cfg.get("startup_nodes") or "")
                nodes = []
                for n in raw_nodes.split(","):
                    n = n.strip()
                    if not n:
                        continue
                    host, _, port = n.partition(":")
                    nodes.append(ClusterNode(host.strip(), int(port.strip() or "6379")))
                if not nodes:
                    nodes = [ClusterNode(redis_cfg.get("host", "localhost"), int(redis_cfg.get("port", 6379)))]
                redis_client = RedisCluster(startup_nodes=nodes, password=password, decode_responses=False)
                logger.info("[LeaderElection] Redis store initialized (cluster, %d nodes)", len(nodes))
            else:
                from redis.asyncio import Redis  # noqa: PLC0415
                redis_client = Redis(
                    host=redis_cfg.get("host", "localhost"),
                    port=redis_cfg.get("port", 6379),
                    db=redis_cfg.get("db", 0),
                    password=password,
                    decode_responses=False,
                )
                logger.info(
                    "[LeaderElection] Redis store initialized: %s:%s/%s",
                    redis_cfg.get("host", "localhost"),
                    redis_cfg.get("port", 6379),
                    redis_cfg.get("db", 0),
                )
            self._redis_store = RedisStore(redis_client)
        return self._redis_store

    async def _acquire_lock(self) -> bool:
        """Try to acquire lock using SET NX EX.

        Returns:
            True: Lock acquired, current role is PRIMARY
            False: Lock held by another instance, current role is STANDBY
        """
        try:
            store = await self._get_redis_store()

            acquired = await store.exclusive_set(
                self._lock_key,
                self._lock_value,
                expiry=self._lock_ttl,
            )

            if acquired:
                logger.info("[LeaderElection] Lock acquired, role=PRIMARY")
            return acquired
        except Exception:
            logger.exception("[LeaderElection] Failed to acquire lock")
            return False

    async def _release_lock(self) -> None:
        """Release lock only if value matches (prevents releasing another instance's lock)."""
        try:
            store = await self._get_redis_store()

            current_value = await store.get(self._lock_key)
            if isinstance(current_value, bytes):
                current_value = current_value.decode("utf-8")
            if current_value is not None and current_value == self._lock_value:
                await store.delete(self._lock_key)
                logger.info("[LeaderElection] Lock released")
            else:
                logger.debug("[LeaderElection] Lock already lost or value mismatch, skip release")
        except Exception:
            logger.exception("[LeaderElection] Failed to release lock")

    async def _renew_lock(self) -> None:
        """Renew lock TTL using EXPIRE command."""
        try:
            store = await self._get_redis_store()

            current_value = await store.get(self._lock_key)
            if isinstance(current_value, bytes):
                current_value = current_value.decode("utf-8")
            if current_value is not None and current_value == self._lock_value:
                pipeline = store.pipeline()
                await pipeline.expire(self._lock_key, self._lock_ttl)
                await pipeline.execute()
                logger.debug("[LeaderElection] Lock TTL renewed: %ds", self._lock_ttl)
            else:
                logger.warning("[LeaderElection] Lock lost during renewal, demoting to STANDBY")
                await self._demote_to_standby()
        except Exception:
            logger.exception("[LeaderElection] Failed to renew lock")

    async def _on_role_change(self, new_role: Role) -> None:
        """Handle role change: set new role and notify all callbacks."""
        self._role = new_role
        logger.info("[LeaderElection] Role changed to: %s", new_role.value)

        for callback in self._callbacks:
            try:
                await callback(new_role)
            except Exception:
                logger.exception("[LeaderElection] Callback error")

    async def _promote_to_primary(self) -> None:
        """Promote to PRIMARY."""
        await self._on_role_change(Role.PRIMARY)

    async def _demote_to_standby(self) -> None:
        """Demote to STANDBY."""
        await self._on_role_change(Role.STANDBY)

    async def _leader_loop(self) -> None:
        """Unified leader loop handling both PRIMARY renewal and STANDBY competition.

        Same task handles both roles - PRIMARY renews lock, STANDBY competes for lock.
        No new task is created on role promotion; this task simply changes behavior.
        """
        while self._running:
            try:
                if self._role == Role.PRIMARY:
                    await asyncio.sleep(self._renewal_interval)
                    if not self._running:
                        break
                    await self._renew_lock()
                else:
                    await asyncio.sleep(self._competition_interval)
                    if not self._running:
                        break
                    if await self._acquire_lock():
                        await self._promote_to_primary()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("[LeaderElection] Leader loop error")

    def register_callback(self, callback: Callable[[Role], Awaitable[None]]) -> None:
        """Register a role change callback."""
        self._callbacks.append(callback)
        logger.debug("[LeaderElection] Callback registered, total: %d", len(self._callbacks))

    def unregister_callback(self, callback: Callable[[Role], Awaitable[None]]) -> None:
        """Unregister a callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)
            logger.debug("[LeaderElection] Callback unregistered, total: %d", len(self._callbacks))

    async def start(self) -> None:
        """Start LeaderElection.

        Immediately tries to acquire lock:
        - Success -> PRIMARY -> Start leader loop (handles renewal)
        - Failure -> STANDBY -> Start leader loop (handles competition)
        """
        if self._running:
            logger.warning("[LeaderElection] Already running")
            return

        self._running = True
        logger.info(
            "[LeaderElection] Starting: instance_id=%s, lock_key=%s, ttl=%ds, renewal=%ds, competition=%ds",
            self._instance_id, self._lock_key, self._lock_ttl, self._renewal_interval, self._competition_interval,
        )

        try:
            if await self._acquire_lock():
                await self._promote_to_primary()
            else:
                await self._demote_to_standby()
        except Exception:
            logger.exception("[LeaderElection] Failed to acquire initial lock")
            self._running = False
            return

        self._leader_task = asyncio.create_task(self._leader_loop())

    async def stop(self) -> None:
        """Stop LeaderElection: clear running flag, cancel task, release lock, demote to STANDBY."""
        if not self._running:
            return

        logger.info("[LeaderElection] Stopping")
        self._running = False

        if self._leader_task is not None:
            self._leader_task.cancel()
            try:
                await self._leader_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("[LeaderElection] Error awaiting leader task")
            self._leader_task = None

        if self._role == Role.PRIMARY:
            try:
                await self._release_lock()
            except Exception:
                logger.exception("[LeaderElection] Error releasing lock")
            await self._demote_to_standby()

        logger.info("[LeaderElection] Stopped")
