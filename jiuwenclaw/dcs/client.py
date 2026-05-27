from __future__ import annotations

from redis.asyncio.cluster import RedisCluster

from jiuwenclaw.dcs.config import (
    DCS_SOCKET_CONNECT_TIMEOUT_SECONDS,
    DcsClusterConfig,
)


class DcsClusterClient:
    """Lazy Redis Cluster connection wrapper shared by domain DCS stores."""

    def __init__(self, config: DcsClusterConfig) -> None:
        self._config = config
        self._client: RedisCluster | None = None

    @property
    def config(self) -> DcsClusterConfig:
        return self._config

    @property
    def host(self) -> str:
        return self._config.host

    @property
    def ttl_seconds(self) -> int:
        return self._config.ttl_seconds

    def _create_redis_client(self) -> RedisCluster:
        return RedisCluster(
            host=self._config.host,
            port=self._config.port,
            password=self._config.password,
            decode_responses=True,
            require_full_coverage=False,
            socket_connect_timeout=DCS_SOCKET_CONNECT_TIMEOUT_SECONDS,
        )

    async def ensure_connected(self) -> RedisCluster:
        if self._client is None:
            self._client = self._create_redis_client()
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get(self, key: str) -> str | None:
        client = await self.ensure_connected()
        return await client.get(key)

    async def set(self, key: str, value: str) -> None:
        client = await self.ensure_connected()
        await client.set(key, value)

    async def set_with_ttl(self, key: str, value: str) -> None:
        await self.set(key, value)
        if self._config.ttl_seconds > 0:
            client = await self.ensure_connected()
            await client.expire(key, self._config.ttl_seconds)

    async def delete(self, *keys: str) -> int:
        if not keys:
            return 0
        client = await self.ensure_connected()
        return await client.delete(*keys)
