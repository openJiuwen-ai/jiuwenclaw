from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as redis

from jiuwenclaw.utils import logger


@dataclass(frozen=True)
class SandboxDcsConfig:
    enabled: bool = False
    url: str = "redis://127.0.0.1:6379/0"
    key_prefix: str = "claw:sandbox:"
    ttl_seconds: int = 0
    socket_timeout_seconds: float = 5.0
    socket_connect_timeout_seconds: float = 5.0

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> SandboxDcsConfig:
        cfg = raw if isinstance(raw, dict) else {}
        ttl_raw = cfg.get("ttl_seconds")
        ttl_seconds = int(ttl_raw) if ttl_raw is not None and str(ttl_raw).strip() else 0
        return cls(
            enabled=_cfg_bool(cfg.get("enabled"), False),
            url=str(cfg.get("url") or cfg.get("redis_url") or "redis://127.0.0.1:6379/0").strip(),
            key_prefix=str(cfg.get("key_prefix") or "claw:sandbox:"),
            ttl_seconds=max(0, ttl_seconds),
            socket_timeout_seconds=float(cfg.get("socket_timeout_seconds") or 5.0),
            socket_connect_timeout_seconds=float(cfg.get("socket_connect_timeout_seconds") or 5.0),
        )


@dataclass(frozen=True)
class SandboxDcsRecord:
    sandbox_id: str
    api_key: str
    created_at: str


class SandboxDcsStore:
    """Persist sandbox metadata to Huawei DCS (Redis-compatible) via redis-py."""

    def __init__(self, config: SandboxDcsConfig) -> None:
        self._config = config
        self._client: redis.Redis | None = None

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    async def ensure_connected(self) -> redis.Redis:
        if self._client is None:
            self._client = redis.from_url(
                self._config.url,
                decode_responses=True,
                socket_timeout=self._config.socket_timeout_seconds,
                socket_connect_timeout=self._config.socket_connect_timeout_seconds,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _record_key(self, sandbox_id: str) -> str:
        prefix = self._config.key_prefix
        if not prefix.endswith(":"):
            prefix = f"{prefix}:"
        return f"{prefix}{sandbox_id}"

    async def save_sandbox(
        self,
        sandbox_id: str,
        *,
        api_key: str,
        created_at: float | None = None,
    ) -> SandboxDcsRecord:
        if not self._config.enabled:
            raise RuntimeError("sandbox_dcs is disabled")
        client = await self.ensure_connected()
        created_ts = created_at if created_at is not None else time.time()
        created_at_text = datetime.fromtimestamp(created_ts, tz=timezone.utc).isoformat()
        record = SandboxDcsRecord(
            sandbox_id=sandbox_id,
            api_key=api_key,
            created_at=created_at_text,
        )
        key = self._record_key(sandbox_id)
        await client.hset(
            key,
            mapping={
                "sandbox_id": record.sandbox_id,
                "api_key": record.api_key,
                "created_at": record.created_at,
            },
        )
        if self._config.ttl_seconds > 0:
            await client.expire(key, self._config.ttl_seconds)
        logger.info(
            "Saved sandbox record to DCS: sandbox_id=%s created_at=%s",
            sandbox_id,
            created_at_text,
        )
        return record

    async def delete_sandbox(self, sandbox_id: str) -> None:
        if not self._config.enabled:
            return
        client = await self.ensure_connected()
        await client.delete(self._record_key(sandbox_id))

    async def get_sandbox(self, sandbox_id: str) -> SandboxDcsRecord | None:
        if not self._config.enabled:
            return None
        client = await self.ensure_connected()
        data = await client.hgetall(self._record_key(sandbox_id))
        if not data:
            return None
        sandbox_value = str(data.get("sandbox_id") or sandbox_id).strip()
        api_key = str(data.get("api_key") or "").strip()
        created_at = str(data.get("created_at") or "").strip()
        if not api_key or not created_at:
            return None
        return SandboxDcsRecord(
            sandbox_id=sandbox_value,
            api_key=api_key,
            created_at=created_at,
        )


def _cfg_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
