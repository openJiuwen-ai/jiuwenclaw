"""Slack cron target validation."""

from jiuwenswarm.gateway.cron.models import (
    CronTargetChannel,
    is_valid_target_channel_id,
    normalize_target_channel_id,
)


def test_slack_is_a_supported_cron_target() -> None:
    assert CronTargetChannel.SLACK.value == "slack"
    assert is_valid_target_channel_id("slack")
    assert is_valid_target_channel_id("SLACK")
    assert normalize_target_channel_id("SLACK") == "slack"
