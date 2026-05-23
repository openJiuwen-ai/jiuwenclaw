from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit


@dataclass(frozen=True)
class OpenAbilityEndpoint:
    host: str
    port: int


@dataclass(frozen=True)
class OpenAbilityConfig:
    ws_path: str = "/ws"
    use_tls: bool = False
    connect_timeout_seconds: float = 10.0
    readiness_poll_interval_seconds: float = 0.5
    readiness_timeout_seconds: float = 60.0
    api_key_query_param: str = "api_key"
    sandbox_id_query_param: str = "sandbox_id"

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> OpenAbilityConfig:
        cfg = raw if isinstance(raw, dict) else {}
        return cls(
            ws_path=str(cfg.get("ws_path") or "/ws"),
            use_tls=_cfg_bool(cfg.get("use_tls"), False),
            connect_timeout_seconds=float(cfg.get("connect_timeout_seconds") or 10.0),
            readiness_poll_interval_seconds=float(
                cfg.get("readiness_poll_interval_seconds") or 0.5
            ),
            readiness_timeout_seconds=float(cfg.get("readiness_timeout_seconds") or 60.0),
            api_key_query_param=str(cfg.get("api_key_query_param") or "api_key"),
            sandbox_id_query_param=str(cfg.get("sandbox_id_query_param") or "sandbox_id"),
        )


def build_openability_ws_uri(
    endpoint: OpenAbilityEndpoint,
    *,
    sandbox_id: str,
    api_key: str,
    config: OpenAbilityConfig,
) -> str:
    scheme = "wss" if config.use_tls else "ws"
    path = config.ws_path if config.ws_path.startswith("/") else f"/{config.ws_path}"
    base = f"{scheme}://{endpoint.host}:{endpoint.port}{path}"
    query: dict[str, str] = {config.sandbox_id_query_param: sandbox_id}
    if api_key:
        query[config.api_key_query_param] = api_key
    return f"{base}?{urlencode(query)}"


def redact_openability_ws_uri(uri: str) -> str:
    """Hide api_key in logs."""
    parts = urlsplit(uri)
    if not parts.query:
        return uri
    pairs = []
    for item in parts.query.split("&"):
        if "=" not in item:
            pairs.append(item)
            continue
        key, value = item.split("=", 1)
        if key in {"api_key", "apiKey"}:
            pairs.append(f"{key}=***")
        else:
            pairs.append(item)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "&".join(pairs), parts.fragment))


def _cfg_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
