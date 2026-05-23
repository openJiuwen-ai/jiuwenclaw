from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass

import redis.asyncio as redis

from jiuwenclaw.sandbox.open_ability import OpenAbilityEndpoint
from jiuwenclaw.utils import logger

_DCS_SOCKET_TIMEOUT_SECONDS = 5.0
_DCS_TTL_SECONDS = 0
_DCS_DEFAULT_PORT = 2881
_DCS_DB = 0


def _env_int(name: str, *, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return int(raw)


@dataclass(frozen=True)
class SandboxDcsConfig:
    host: str
    port: int
    username: str | None = None
    password: str | None = None

    @classmethod
    def from_env(cls) -> SandboxDcsConfig:
        host = os.environ.get("SANDBOX_DCS_HOST", "").strip()
        if not host:
            raise RuntimeError(
                "SANDBOX_DCS_HOST environment variable is required when sandbox routing is enabled"
            )
        username = os.environ.get("SANDBOX_DCS_USERNAME", "").strip() or None
        password = os.environ.get("SANDBOX_DCS_PASSWORD", "").strip() or None
        return cls(
            host=host,
            port=_env_int("SANDBOX_DCS_PORT", default=_DCS_DEFAULT_PORT),
            username=username,
            password=password,
        )


@dataclass(frozen=True)
class SandboxDcsRecord:
    sandbox_id: str
    api_key_sha256: str


class SandboxDcsStore:
    """Persist sandbox metadata to Huawei DCS (Redis-compatible) via redis-py."""

    def __init__(self, config: SandboxDcsConfig) -> None:
        self._config = config
        self._client: redis.Redis | None = None

    @classmethod
    def from_env(cls) -> SandboxDcsStore:
        return cls(SandboxDcsConfig.from_env())

    def _create_redis_client(self) -> redis.Redis:
        return redis.Redis(
            host=self._config.host,
            port=self._config.port,
            db=_DCS_DB,
            username=self._config.username,
            password=self._config.password,
            ssl=False,
            decode_responses=True,
            socket_timeout=_DCS_SOCKET_TIMEOUT_SECONDS,
            socket_connect_timeout=_DCS_SOCKET_TIMEOUT_SECONDS,
        )

    async def ensure_connected(self) -> redis.Redis:
        if self._client is None:
            self._client = self._create_redis_client()
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def _api_key_key(sandbox_id: str) -> str:
        return f"jiuwen:sandboxApiKey:{sandbox_id}"

    @staticmethod
    def _sandbox_to_oa_key(sandbox_id: str) -> str:
        return f"jiuwen:sandboxToOA:{sandbox_id}"

    @staticmethod
    def _hash_api_key(api_key: str) -> str:
        return hashlib.sha256(api_key.encode("utf-8")).hexdigest()

    @staticmethod
    def _decode_api_key_value(raw: str) -> str | None:
        value = str(raw or "").strip()
        if not value:
            return None
        if len(value) == 64 and all(ch in "0123456789abcdef" for ch in value.lower()):
            return value.lower()
        return None

    @staticmethod
    def _parse_sandbox_to_oa_value(raw: str) -> OpenAbilityEndpoint | None:
        text = str(raw or "").strip()
        if not text or ":" not in text:
            return None
        host, port_raw = text.rsplit(":", 1)
        host = host.strip()
        port_raw = port_raw.strip()
        if not host or not port_raw:
            return None
        try:
            port = int(port_raw)
        except ValueError:
            return None
        if port <= 0 or port > 65535:
            return None
        return OpenAbilityEndpoint(host=host, port=port)

    async def save_sandbox(
        self,
        sandbox_id: str,
        *,
        api_key: str,
    ) -> SandboxDcsRecord:
        client = await self.ensure_connected()
        api_key_sha256 = self._hash_api_key(api_key)
        record = SandboxDcsRecord(
            sandbox_id=sandbox_id,
            api_key_sha256=api_key_sha256,
        )
        key = self._api_key_key(sandbox_id)
        await client.set(key, api_key_sha256)
        if _DCS_TTL_SECONDS > 0:
            await client.expire(key, _DCS_TTL_SECONDS)
        logger.info("Saved sandbox API key hash to DCS: key=%s", key)
        return record

    async def delete_sandbox(self, sandbox_id: str) -> None:
        client = await self.ensure_connected()
        await client.delete(
            self._api_key_key(sandbox_id),
            self._sandbox_to_oa_key(sandbox_id),
        )

    async def get_sandbox(self, sandbox_id: str) -> SandboxDcsRecord | None:
        client = await self.ensure_connected()
        raw = await client.get(self._api_key_key(sandbox_id))
        if raw is None:
            return None
        api_key_sha256 = self._decode_api_key_value(raw)
        if api_key_sha256 is None:
            return None
        return SandboxDcsRecord(
            sandbox_id=sandbox_id,
            api_key_sha256=api_key_sha256,
        )

    async def get_openability_endpoint(self, sandbox_id: str) -> OpenAbilityEndpoint | None:
        client = await self.ensure_connected()
        raw = await client.get(self._sandbox_to_oa_key(sandbox_id))
        if raw is None:
            return None
        return self._parse_sandbox_to_oa_value(raw)
