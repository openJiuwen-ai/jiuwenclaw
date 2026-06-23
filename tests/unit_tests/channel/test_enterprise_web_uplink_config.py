# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import os

import pytest

from jiuwenclaw.channel.enterprise_web_uplink_config import (
    get_enterprise_web_uplink_client_settings,
    get_enterprise_web_uplink_ws_settings,
)


def test_uplink_ws_settings_defaults() -> None:
    for key in (
        "ENTERPRISE_WEB_UPLINK_PING_INTERVAL",
        "ENTERPRISE_WEB_UPLINK_PING_TIMEOUT",
        "ENTERPRISE_WEB_UPLINK_CONNECT_MAX_ATTEMPTS",
        "ENTERPRISE_WEB_UPLINK_CONNECT_BASE_DELAY_SEC",
        "ENTERPRISE_WEB_UPLINK_OPEN_TIMEOUT",
        "ENTERPRISE_WEB_UPLINK_RECONNECT_MAX_DELAY_SEC",
        "ENTERPRISE_WEB_UPLINK_RECONNECT_BACKOFF_CAP",
    ):
        os.environ.pop(key, None)

    ws = get_enterprise_web_uplink_ws_settings()
    assert ws.ping_interval == 20.0
    assert ws.ping_timeout == 20.0

    client = get_enterprise_web_uplink_client_settings()
    assert client.connect_max_attempts == 12
    assert client.connect_base_delay_sec == 0.15
    assert client.open_timeout == 30.0
    assert client.reconnect_max_delay_sec == 2.0
    assert client.reconnect_backoff_cap == 4


def test_uplink_settings_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENTERPRISE_WEB_UPLINK_PING_INTERVAL", "25")
    monkeypatch.setenv("ENTERPRISE_WEB_UPLINK_PING_TIMEOUT", "30")
    monkeypatch.setenv("ENTERPRISE_WEB_UPLINK_CONNECT_MAX_ATTEMPTS", "5")
    monkeypatch.setenv("ENTERPRISE_WEB_UPLINK_CONNECT_BASE_DELAY_SEC", "0.5")
    monkeypatch.setenv("ENTERPRISE_WEB_UPLINK_OPEN_TIMEOUT", "45")
    monkeypatch.setenv("ENTERPRISE_WEB_UPLINK_RECONNECT_MAX_DELAY_SEC", "3")
    monkeypatch.setenv("ENTERPRISE_WEB_UPLINK_RECONNECT_BACKOFF_CAP", "6")

    client = get_enterprise_web_uplink_client_settings()
    assert client.ping_interval == 25.0
    assert client.ping_timeout == 30.0
    assert client.connect_max_attempts == 5
    assert client.connect_base_delay_sec == 0.5
    assert client.open_timeout == 45.0
    assert client.reconnect_max_delay_sec == 3.0
    assert client.reconnect_backoff_cap == 6
