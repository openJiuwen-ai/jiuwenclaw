from __future__ import annotations

import os
from dataclasses import dataclass

_OA_USE_TLS = False
_OA_CONNECT_TIMEOUT_SECONDS = 10.0
_OA_READINESS_POLL_INTERVAL_SECONDS = 0.5
_OA_READINESS_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True)
class OpenAbilityEndpoint:
    host: str
    port: int


@dataclass(frozen=True)
class OpenAbilityConfig:
    ws_path: str
    use_tls: bool = _OA_USE_TLS
    connect_timeout_seconds: float = _OA_CONNECT_TIMEOUT_SECONDS
    readiness_poll_interval_seconds: float = _OA_READINESS_POLL_INTERVAL_SECONDS
    readiness_timeout_seconds: float = _OA_READINESS_TIMEOUT_SECONDS

    @classmethod
    def from_env(cls) -> OpenAbilityConfig:
        ws_path = os.environ.get("GATEWAY_TO_OA_WS_PATH", "").strip()
        if not ws_path:
            raise RuntimeError(
                "GATEWAY_TO_OA_WS_PATH environment variable is required when sandbox routing is enabled"
            )
        return cls(ws_path=ws_path)


def build_openability_ws_uri(endpoint: OpenAbilityEndpoint, *, ws_path: str) -> str:
    scheme = "wss" if _OA_USE_TLS else "ws"
    path = ws_path if ws_path.startswith("/") else f"/{ws_path}"
    return f"{scheme}://{endpoint.host}:{endpoint.port}{path}"
