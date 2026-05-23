from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as redis

from jiuwenclaw.sandbox.open_ability import OpenAbilityConfig, OpenAbilityEndpoint
from jiuwenclaw.utils import logger


@dataclass(frozen=True)
class SandboxDcsConfig:
    enabled: bool = False
    url: str = "redis://127.0.0.1:6379/0"
    key_prefix: str = ""
    open_ability_key_suffix: str = ":openability"
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
            key_prefix=str(cfg.get("key_prefix") or ""),
            open_ability_key_suffix=str(cfg.get("open_ability_key_suffix") or ":openability"),
            ttl_seconds=max(0, ttl_seconds),
            socket_timeout_seconds=float(cfg.get("socket_timeout_seconds") or 5.0),
            socket_connect_timeout_seconds=float(cfg.get("socket_connect_timeout_seconds") or 5.0),
        )


@dataclass(frozen=True)
class SandboxDcsRecord:
    sandbox_id: str
    api_key_sha256: str
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

    def _sandbox_key(self, sandbox_id: str) -> str:
        prefix = self._config.key_prefix
        if prefix and not prefix.endswith(":"):
            prefix = f"{prefix}:"
        return f"{prefix}{sandbox_id}"

    def _openability_key(self, sandbox_id: str) -> str:
        return f"{self._sandbox_key(sandbox_id)}{self._config.open_ability_key_suffix}"

    @staticmethod
    def _hash_api_key(api_key: str) -> str:
        return hashlib.sha256(api_key.encode("utf-8")).hexdigest()

    @staticmethod
    def _encode_record_value(api_key_sha256: str, created_at: str) -> str:
        return json.dumps(
            {"api_key_sha256": api_key_sha256, "created_at": created_at},
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def _decode_record_value(raw: str) -> dict[str, str] | None:
        text = str(raw or "").strip()
        if not text:
            return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        api_key_sha256 = str(data.get("api_key_sha256") or "").strip()
        created_at = str(data.get("created_at") or "").strip()
        if not api_key_sha256 or not created_at:
            return None
        return {"api_key_sha256": api_key_sha256, "created_at": created_at}

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
        api_key_sha256 = self._hash_api_key(api_key)
        record = SandboxDcsRecord(
            sandbox_id=sandbox_id,
            api_key_sha256=api_key_sha256,
            created_at=created_at_text,
        )
        key = self._sandbox_key(sandbox_id)
        await client.set(key, self._encode_record_value(api_key_sha256, created_at_text))
        if self._config.ttl_seconds > 0:
            await client.expire(key, self._config.ttl_seconds)
        logger.info(
            "Saved sandbox record to DCS: key=%s created_at=%s",
            key,
            created_at_text,
        )
        return record

    async def delete_sandbox(self, sandbox_id: str) -> None:
        if not self._config.enabled:
            return
        client = await self.ensure_connected()
        await client.delete(self._sandbox_key(sandbox_id), self._openability_key(sandbox_id))

    async def get_sandbox(self, sandbox_id: str) -> SandboxDcsRecord | None:
        if not self._config.enabled:
            return None
        client = await self.ensure_connected()
        raw = await client.get(self._sandbox_key(sandbox_id))
        if raw is None:
            return None
        parsed = self._decode_record_value(raw)
        if parsed is None:
            return None
        return SandboxDcsRecord(
            sandbox_id=sandbox_id,
            api_key_sha256=parsed["api_key_sha256"],
            created_at=parsed["created_at"],
        )

    async def get_openability_endpoint(
        self,
        sandbox_id: str,
        *,
        open_ability_config: OpenAbilityConfig | None = None,
    ) -> OpenAbilityEndpoint | None:
        if not self._config.enabled:
            return None
        client = await self.ensure_connected()
        data = await client.hgetall(self._openability_key(sandbox_id))
        if not data:
            return None
        fields = {str(key): str(value) for key, value in data.items()}
        cfg = open_ability_config or OpenAbilityConfig()
        host = _first_non_empty(fields, cfg.host_fields)
        port_raw = _first_non_empty(fields, cfg.port_fields)
        if not host or not port_raw:
            return None
        try:
            port = int(str(port_raw).strip())
        except ValueError:
            return None
        if port <= 0 or port > 65535:
            return None
        return OpenAbilityEndpoint(host=host, port=port)


def _first_non_empty(data: dict[str, str], field_names: tuple[str, ...]) -> str:
    for name in field_names:
        value = str(data.get(name) or "").strip()
        if value:
            return value
    return ""


def _cfg_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
