# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Enterprise Web uplink WS tuning (server + client), overridable via environment."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return float(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return int(raw)


@dataclass(frozen=True)
class EnterpriseWebUplinkWsSettings:
    ping_interval: float
    ping_timeout: float


@dataclass(frozen=True)
class EnterpriseWebUplinkClientSettings(EnterpriseWebUplinkWsSettings):
    connect_max_attempts: int
    connect_base_delay_sec: float
    open_timeout: float
    reconnect_max_delay_sec: float
    reconnect_backoff_cap: int


def get_enterprise_web_uplink_ws_settings() -> EnterpriseWebUplinkWsSettings:
    """Shared ping settings for Web Pod WS server and Gateway uplink client."""
    return EnterpriseWebUplinkWsSettings(
        ping_interval=_env_float("ENTERPRISE_WEB_UPLINK_PING_INTERVAL", 20.0),
        ping_timeout=_env_float("ENTERPRISE_WEB_UPLINK_PING_TIMEOUT", 20.0),
    )


def get_enterprise_web_uplink_client_settings() -> EnterpriseWebUplinkClientSettings:
    """Gateway EnterpriseWebChannel connect / reconnect tuning."""
    ws = get_enterprise_web_uplink_ws_settings()
    return EnterpriseWebUplinkClientSettings(
        ping_interval=ws.ping_interval,
        ping_timeout=ws.ping_timeout,
        connect_max_attempts=_env_int("ENTERPRISE_WEB_UPLINK_CONNECT_MAX_ATTEMPTS", 12),
        connect_base_delay_sec=_env_float("ENTERPRISE_WEB_UPLINK_CONNECT_BASE_DELAY_SEC", 0.15),
        open_timeout=_env_float("ENTERPRISE_WEB_UPLINK_OPEN_TIMEOUT", 30.0),
        reconnect_max_delay_sec=_env_float("ENTERPRISE_WEB_UPLINK_RECONNECT_MAX_DELAY_SEC", 2.0),
        reconnect_backoff_cap=_env_int("ENTERPRISE_WEB_UPLINK_RECONNECT_BACKOFF_CAP", 4),
    )
