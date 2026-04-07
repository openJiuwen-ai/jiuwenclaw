# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for ChannelBootstrap."""

from jiuwenclaw.gateway.channel_bootstrap import (
    ChannelInstances,
    is_channel_enabled,
    should_restart_channel,
)


class TestChannelBootstrap:
    """Test ChannelBootstrap functionality."""

    @staticmethod
    def test_is_channel_enabled_none_config():
        """Test channel enabled check with None config."""
        enabled, reason = is_channel_enabled(None, ["app_id"])
        assert enabled is False
        assert reason == "未配置或格式错误"

    @staticmethod
    def test_is_channel_enabled_explicit_disabled():
        """Test channel enabled check with enabled=false."""
        enabled, reason = is_channel_enabled({"enabled": False}, [])
        assert enabled is False
        assert reason == "enabled = false"

    @staticmethod
    def test_is_channel_enabled_explicit_enabled():
        """Test channel enabled check with enabled=true."""
        enabled, reason = is_channel_enabled({"enabled": True}, [])
        assert enabled is True
        assert reason == ""

    @staticmethod
    def test_is_channel_enabled_missing_required_fields():
        """Test channel enabled check when missing required fields."""
        enabled, reason = is_channel_enabled({}, ["app_id", "app_secret"])
        assert enabled is False
        assert "缺少" in reason
        assert "app_id,app_secret" in reason

    @staticmethod
    def test_is_channel_enabled_has_required_fields():
        """Test channel enabled check when has required fields."""
        enabled, reason = is_channel_enabled(
            {"app_id": "test_id", "app_secret": "test_secret"}, ["app_id", "app_secret"]
        )
        assert enabled is True
        assert reason == ""

    @staticmethod
    def test_should_restart_channel_none_to_config():
        """Test restart check when channel appears."""
        old_conf = {}
        new_conf = {"feishu": {"app_id": "test"}}
        result = should_restart_channel("feishu", old_conf, new_conf)
        assert result is True

    @staticmethod
    def test_should_restart_channel_config_to_none():
        """Test restart check when channel disappears."""
        old_conf = {"feishu": {"app_id": "test"}}
        new_conf = {}
        result = should_restart_channel("feishu", old_conf, new_conf)
        assert result is True

    @staticmethod
    def test_should_restart_channel_config_changed():
        """Test restart check when config changes."""
        old_conf = {"feishu": {"app_id": "old"}}
        new_conf = {"feishu": {"app_id": "new"}}
        result = should_restart_channel("feishu", old_conf, new_conf)
        assert result is True

    @staticmethod
    def test_should_restart_channel_config_unchanged():
        """Test restart check when config unchanged."""
        old_conf = {"feishu": {"app_id": "same"}}
        new_conf = {"feishu": {"app_id": "same"}}
        result = should_restart_channel("feishu", old_conf, new_conf)
        assert result is False

    @staticmethod
    def test_should_restart_channel_both_none():
        """Test restart check when both None."""
        old_conf = {}
        new_conf = {}
        result = should_restart_channel("feishu", old_conf, new_conf)
        assert result is False

    @staticmethod
    def test_channel_instances_initialization():
        """Test ChannelInstances dataclass initialization."""
        instances = ChannelInstances()
        assert instances.feishu_channel is None
        assert instances.feishu_task is None
        assert instances.feishu_enterprise_channels == {}
        assert instances.feishu_enterprise_tasks == {}
        assert instances.xiaoyi_channel is None
        assert instances.xiaoyi_task is None
