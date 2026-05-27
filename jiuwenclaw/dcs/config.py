from __future__ import annotations

import os
from dataclasses import dataclass

DCS_SOCKET_CONNECT_TIMEOUT_SECONDS = 1.0
DCS_DEFAULT_TTL_SECONDS = 86400
DCS_DEFAULT_PORT = 2881

# Canonical env names (same as legacy Sandbox routing); VibeSkill session reuses them.
SANDBOX_DCS_HOST_ENV = "SANDBOX_DCS_HOST"
SANDBOX_DCS_PORT_ENV = "SANDBOX_DCS_PORT"
SANDBOX_DCS_PASSWORD_ENV = "SANDBOX_DCS_PASSWORD"
SANDBOX_DCS_TTL_SECONDS_ENV = "SANDBOX_DCS_TTL_SECONDS"


def env_int(name: str, *, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class DcsClusterConfig:
    host: str
    port: int
    password: str | None = None
    ttl_seconds: int = DCS_DEFAULT_TTL_SECONDS


def load_config_from_env(
    *,
    required: bool = True,
    missing_host_error: str | None = None,
) -> DcsClusterConfig | None:
    """Load ``DcsClusterConfig`` from ``SANDBOX_DCS_*`` environment variables."""
    host = os.environ.get(SANDBOX_DCS_HOST_ENV, "").strip()
    if not host:
        if required:
            raise RuntimeError(
                missing_host_error
                or f"{SANDBOX_DCS_HOST_ENV} environment variable is required"
            )
        return None
    password = os.environ.get(SANDBOX_DCS_PASSWORD_ENV, "").strip() or None
    return DcsClusterConfig(
        host=host,
        port=env_int(SANDBOX_DCS_PORT_ENV, default=DCS_DEFAULT_PORT),
        password=password,
        ttl_seconds=env_int(SANDBOX_DCS_TTL_SECONDS_ENV, default=DCS_DEFAULT_TTL_SECONDS),
    )
