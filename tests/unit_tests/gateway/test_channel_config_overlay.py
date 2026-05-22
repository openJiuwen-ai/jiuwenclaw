# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from jiuwenclaw.gateway.channel_config_overlay import (
    ChannelConfigChange,
    _gateway_deployment_mode,
    apply_channel_change_to_runtime,
    build_channels_from_db_rows,
    channel_config_overlay_enabled,
    fetch_active_channel_config_rows,
    merge_channels_with_db,
    register_channel_config_reload,
    trigger_channel_config_reload,
)


def test_overlay_enabled_standalone():
    with patch(
        "jiuwenclaw.gateway.channel_config_overlay._gateway_deployment_mode",
        return_value="standalone",
    ):
        assert channel_config_overlay_enabled() is False


def test_deployment_mode_env_fallback_when_config_raises(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DEPLOYMENT_MODE", "distributed")

    def _boom() -> dict:
        raise RuntimeError("config unavailable")

    with patch("jiuwenclaw.config.get_config", side_effect=_boom):
        assert _gateway_deployment_mode() == "distributed"


def test_deployment_mode_invalid_value_defaults_standalone():
    with patch(
        "jiuwenclaw.config.get_config",
        return_value={"gateway": {"deployment_mode": "k8s"}},
    ):
        assert _gateway_deployment_mode() == "standalone"


def test_overlay_enabled_distributed():
    with patch(
        "jiuwenclaw.gateway.channel_config_overlay._gateway_deployment_mode",
        return_value="distributed",
    ):
        assert channel_config_overlay_enabled() is True


def test_build_single_feishu_bot_top_level():
    rows = [
        {
            "channel_id": "feishu",
            "channel_type": "feishu",
            "bot_id": "feishu",
            "config": {"app_id": "db-id", "app_secret": "db-secret"},
            "status": "active",
        }
    ]
    channels = build_channels_from_db_rows(rows)
    assert channels["feishu"]["app_id"] == "db-id"
    assert channels["feishu"]["enabled"] is True
    assert "dingtalk" not in channels


def test_build_multi_feishu_bots():
    rows = [
        {
            "channel_id": "feishu-a",
            "channel_type": "feishu",
            "bot_id": "bot_a",
            "config": {"app_id": "a", "app_secret": "sa"},
            "status": "active",
        },
        {
            "channel_id": "feishu-b",
            "channel_type": "feishu",
            "bot_id": "bot_b",
            "config": {"app_id": "b", "app_secret": "sb"},
            "status": "active",
        },
    ]
    channels = build_channels_from_db_rows(rows)
    assert channels["feishu"]["bot_a"]["app_id"] == "a"
    assert channels["feishu"]["bot_b"]["app_secret"] == "sb"


def test_incremental_upsert_single_bot():
    current: dict = {}
    row = {
        "channel_id": "feishu",
        "channel_type": "feishu",
        "bot_id": "feishu",
        "config": {"app_id": "new"},
    }
    out = apply_channel_change_to_runtime(
        current, ChannelConfigChange.upsert(row)
    )
    assert out["feishu"]["app_id"] == "new"


def test_incremental_upsert_second_bot_converts_to_multi():
    current = {
        "feishu": {"app_id": "old", "app_secret": "s", "enabled": True},
    }
    row = {
        "channel_id": "bot-b",
        "channel_type": "feishu",
        "bot_id": "bot_b",
        "config": {"app_id": "b"},
    }
    out = apply_channel_change_to_runtime(
        current, ChannelConfigChange.upsert(row)
    )
    assert out["feishu"]["feishu"]["app_id"] == "old"
    assert out["feishu"]["bot_b"]["app_id"] == "b"


def test_incremental_remove_multi_bot():
    current = {
        "feishu": {
            "bot_a": {"app_id": "a", "enabled": True},
            "bot_b": {"app_id": "b", "enabled": True},
        }
    }
    row = {
        "channel_id": "bot-b",
        "channel_type": "feishu",
        "bot_id": "bot_b",
    }
    out = apply_channel_change_to_runtime(
        current, ChannelConfigChange.remove(row)
    )
    assert "bot_b" not in out["feishu"]
    assert out["feishu"]["bot_a"]["app_id"] == "a"


def test_incremental_remove_last_clears_type():
    current = {"feishu": {"app_id": "x", "app_secret": "y", "enabled": True}}
    row = {"channel_id": "feishu", "channel_type": "feishu", "bot_id": "feishu"}
    out = apply_channel_change_to_runtime(
        current, ChannelConfigChange.remove(row)
    )
    assert "feishu" not in out


@pytest.mark.asyncio
async def test_trigger_reload_serializes_concurrent_calls():
    active = 0
    peak = 0

    async def slow_reload(_change: ChannelConfigChange | None) -> None:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1

    with patch(
        "jiuwenclaw.gateway.channel_config_overlay.channel_config_overlay_enabled",
        return_value=True,
    ):
        await register_channel_config_reload(slow_reload)
        await asyncio.gather(
            trigger_channel_config_reload(),
            trigger_channel_config_reload(),
        )
        await register_channel_config_reload(None)

    assert peak == 1


@pytest.mark.asyncio
async def test_fetch_active_rows_delegates_to_db_loader():
    active_rows = [
        {
            "channel_id": "feishu-1",
            "channel_name": "Feishu",
            "channel_type": "feishu",
            "bot_id": "feishu",
            "config": {"app_id": "x"},
            "status": "active",
        }
    ]
    with patch(
        "jiuwenclaw.gateway.channel_config_overlay.load_active_channel_config_rows",
        new=AsyncMock(return_value=active_rows),
    ) as loader:
        rows = await fetch_active_channel_config_rows()
    loader.assert_awaited_once()
    assert rows == active_rows


@pytest.mark.asyncio
async def test_fetch_active_rows_empty_when_loader_fails():
    with patch(
        "jiuwenclaw.gateway.channel_config_overlay.load_active_channel_config_rows",
        new=AsyncMock(return_value=[]),
    ):
        assert await fetch_active_channel_config_rows() == []


def test_build_ignores_yaml_base():
    yaml_base = {"dingtalk": {"client_id": "dt-yaml"}}
    rows = [
        {
            "channel_id": "feishu-1",
            "channel_type": "feishu",
            "bot_id": "main",
            "config": {"app_id": "x"},
            "status": "active",
        }
    ]
    merged = merge_channels_with_db(yaml_base, rows)
    assert "dingtalk" not in merged
