# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Gateway 分布式模式：Redis 异步客户端封装（设计文档 §3.3.3）。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from redis.asyncio import ConnectionPool, Redis

logger = logging.getLogger(__name__)


def _require_redis_async() -> Any:
    """延迟导入：默认安装不包含 redis 包时仍可 import 本模块。"""
    try:
        import redis.asyncio as redis_async  # noqa: PLC0415
    except ImportError as e:
        raise ImportError(
            "分布式网关需要 Redis 异步客户端。请安装可选依赖：pip install 'jiuwenclaw[redis]' "
            "或 pip install 'redis>=5.0.0'"
        ) from e
    return redis_async


def _coerce_int(val: Any, default: int) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _coerce_float(val: Any, default: float) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


@dataclass
class RedisConfig:
    """与 config 中 ``redis:`` 段及 §3.2.1 对齐。"""

    mode: str = "standalone"                                  # standalone | cluster
    startup_nodes: list[dict] = field(default_factory=list)   # [{host, port}, ...];cluster 启动节点
    host: str = "localhost"
    port: int = 6379
    password: str | None = None
    db: int = 0
    key_prefix: str = "jiuwenclaw:"
    pool_size: int = 10
    connect_timeout: float = 5.0
    operation_timeout: float = 10.0
    health_check_interval: int = 30

    @staticmethod
    def _normalize_password(value: Any) -> str | None:
        """处理 YAML 解析后的密码值（false/0 等应视为无密码）。"""
        if value is None or value is False:
            return None
        if isinstance(value, str) and value.strip() == "":
            return None
        return str(value)

    @staticmethod
    def _parse_startup_nodes(value: Any) -> list[dict]:
        """解析 cluster 启动节点,接受逗号分隔的 'host:port' 字符串或已有列表。"""
        nodes: list[dict] = []
        if isinstance(value, (list, tuple)):
            items = list(value)
        else:
            items = str(value).split(",") if value else []
        for item in items:
            host, _, port = str(item).partition(":")
            host = host.strip()
            if not host:
                continue
            nodes.append({"host": host, "port": _coerce_int(port.strip() or "6379", 6379)})
        return nodes

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> RedisConfig:
        m = data or {}
        kp = str(m.get("key_prefix") if m.get("key_prefix") is not None else "jiuwenclaw:")
        if kp and not kp.endswith(":"):
            kp = f"{kp}:"
        pw = cls._normalize_password(m.get("password"))
        mode = str(m.get("mode") or "standalone").strip().lower()
        if mode not in ("standalone", "cluster"):
            mode = "standalone"
        return cls(
            mode=mode,
            startup_nodes=cls._parse_startup_nodes(m.get("startup_nodes")),
            host=str(m.get("host") if m.get("host") is not None else "localhost"),
            port=_coerce_int(m.get("port"), 6379),
            password=pw,
            db=_coerce_int(m.get("db"), 0),
            key_prefix=kp,
            pool_size=max(1, _coerce_int(m.get("pool_size"), 10)),
            connect_timeout=_coerce_float(m.get("connect_timeout"), 5.0),
            operation_timeout=_coerce_float(m.get("operation_timeout"), 10.0),
            health_check_interval=max(1, _coerce_int(m.get("health_check_interval"), 30)),
        )

    def effective_key(self, key: str) -> str:
        """为 key / 频道名添加 ``key_prefix``（已含前缀则不再添加）。"""
        k = key or ""
        p = self.key_prefix
        if not p:
            return k
        if k.startswith(p):
            return k
        return f"{p}{k}"


class RedisClient:
    """§3.3.3 接口：连接池 + KV/Hash + Pub/Sub + ping + close。"""

    def __init__(self, cfg: RedisConfig) -> None:
        self._cfg = cfg
        self._pool: ConnectionPool | None = None
        self._redis: Redis | None = None

    @property
    def config(self) -> RedisConfig:
        return self._cfg

    async def open(self) -> None:
        if self._redis is not None:
            return
        redis = _require_redis_async()
        if self._cfg.mode == "cluster":
            from redis.asyncio.cluster import RedisCluster
            from redis.cluster import ClusterNode
            if not self._cfg.startup_nodes:
                logger.warning(
                    "[RedisClient] cluster 模式未配置 startup_nodes,回退单节点 %s:%s"
                    "(若该节点非集群成员将连接失败)",
                    self._cfg.host, self._cfg.port,
                )
            nodes = [ClusterNode(n["host"], int(n["port"])) for n in self._cfg.startup_nodes] \
                or [ClusterNode(self._cfg.host, self._cfg.port)]
            self._redis = RedisCluster(
                startup_nodes=nodes,
                password=self._cfg.password,
                decode_responses=True,
                socket_connect_timeout=self._cfg.connect_timeout,
                socket_timeout=self._cfg.operation_timeout,
                health_check_interval=self._cfg.health_check_interval,
                max_connections=self._cfg.pool_size,
            )
            return
        self._pool = redis.ConnectionPool(
            host=self._cfg.host,
            port=self._cfg.port,
            username=None,
            password=self._cfg.password,
            db=self._cfg.db,
            decode_responses=True,
            max_connections=self._cfg.pool_size,
            socket_connect_timeout=self._cfg.connect_timeout,
            socket_timeout=self._cfg.operation_timeout,
        )
        self._redis = redis.Redis(connection_pool=self._pool)

    async def close(self) -> None:
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception as exc:  # noqa: BLE001
                logger.debug("[RedisClient] aclose: %s", exc)
            self._redis = None
        if self._pool is not None:
            try:
                await self._pool.disconnect(inuse_connections=True)
            except Exception as exc:  # noqa: BLE001
                logger.debug("[RedisClient] pool disconnect: %s", exc)
            self._pool = None

    def _connection(self) -> Redis:
        if self._redis is None:
            raise RuntimeError("RedisClient is not open")
        return self._redis

    async def ping(self) -> bool:
        if self._redis is None:
            return False
        try:
            return bool(await self._redis.ping())
        except Exception as exc:  # noqa: BLE001
            logger.warning("[RedisClient] ping failed: %s", exc)
            return False

    async def get(self, key: str) -> str | None:
        r = self._connection()
        return await r.get(self._cfg.effective_key(key))

    async def mget(self, keys: list[str]) -> list[str | None]:
        r = self._connection()
        if not keys:
            return []
        effective_keys = [self._cfg.effective_key(key) for key in keys]
        try:
            values = await r.mget(effective_keys)
            return list(values) if values else []
        except Exception as exc:
            # Cluster 跨 slot 抛 CROSSSLOT → 回退逐个 get
            logger.warning("[RedisClient] mget fallback to per-key get: %s", exc)
            return await asyncio.gather(*(r.get(k) for k in effective_keys))

    async def scan_keys(self, pattern: str) -> list[str]:
        r = self._connection()
        effective_pattern = self._cfg.effective_key(pattern)
        key_prefix = self._cfg.key_prefix or ""
        keys: list[str] = []
        async for raw_key in r.scan_iter(match=effective_pattern):
            key = raw_key.decode("utf-8") if isinstance(raw_key, bytes) else str(raw_key)
            if key_prefix and key.startswith(key_prefix):
                key = key[len(key_prefix):]
            keys.append(key)
        return keys

    async def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> bool:
        r = self._connection()
        v = value if isinstance(value, str) else str(value)
        if ttl_seconds is not None and ttl_seconds > 0:
            return bool(await r.set(self._cfg.effective_key(key), v, ex=int(ttl_seconds)))
        return bool(await r.set(self._cfg.effective_key(key), v))

    async def delete(self, key: str) -> bool:
        r = self._connection()
        n = int(await r.delete(self._cfg.effective_key(key)))
        return n > 0

    async def hget(self, key: str, field: str) -> str | None:
        r = self._connection()
        return await r.hget(self._cfg.effective_key(key), field)

    async def hset(self, key: str, field: str, value: Any) -> bool:
        r = self._connection()
        v = value if isinstance(value, str) else str(value)
        await r.hset(self._cfg.effective_key(key), field, v)
        return True

    async def hgetall(self, key: str) -> dict[str, Any]:
        r = self._connection()
        raw = await r.hgetall(self._cfg.effective_key(key))
        return dict(raw) if raw else {}

    async def hdel(self, key: str, field: str) -> bool:
        r = self._connection()
        n = int(await r.hdel(self._cfg.effective_key(key), field))
        return n > 0

    async def publish(self, channel: str, message: str) -> int:
        r = self._connection()
        return int(await r.publish(self._cfg.effective_key(channel), message))

    async def subscribe(self, channel: str) -> AsyncIterator[str]:
        """订阅频道，仅 yield ``type==message`` 的字符串载荷。"""
        r = self._connection()
        pubsub = r.pubsub()
        ch = self._cfg.effective_key(channel)
        await pubsub.subscribe(ch)
        try:
            async for msg in pubsub.listen():
                if not isinstance(msg, dict):
                    continue
                if msg.get("type") != "message":
                    continue
                data = msg.get("data")
                yield data if isinstance(data, str) else str(data)
        finally:
            try:
                await pubsub.unsubscribe(ch)
            except Exception as exc:  # noqa: BLE001
                logger.debug("[RedisClient] unsubscribe: %s", exc)
            try:
                await pubsub.aclose()
            except Exception as exc:  # noqa: BLE001
                logger.debug("[RedisClient] pubsub aclose: %s", exc)
