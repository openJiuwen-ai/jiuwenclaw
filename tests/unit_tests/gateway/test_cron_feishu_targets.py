"""Cron targets：飞书合并 enterprise 后的多 bot 推断。"""

from unittest.mock import patch

from jiuwenclaw.gateway.cron.models import (
    feishu_multi_bot_app_ids_from_config,
    upgrade_bare_feishu_target_for_multi_bot_config,
)


def test_feishu_multi_bot_app_ids_from_nested_only() -> None:
    conf = {
        "app_id": "cli_flat",
        "app_secret": "s",
        "bot_a": {"app_id": "cli_a", "app_secret": "x"},
    }
    assert feishu_multi_bot_app_ids_from_config(conf) == ["cli_a"]


def test_upgrade_bare_feishu_when_single_multi_bot_child() -> None:
    with patch("jiuwenclaw.config.get_config_raw") as m:
        m.return_value = {
            "channels": {
                "feishu": {
                    "prod": {"app_id": "cli_one", "app_secret": "x"},
                }
            }
        }
        assert upgrade_bare_feishu_target_for_multi_bot_config("feishu") == "feishu:cli_one"


def test_upgrade_bare_feishu_untouched_when_two_multi_bots() -> None:
    with patch("jiuwenclaw.config.get_config_raw") as m:
        m.return_value = {
            "channels": {
                "feishu": {
                    "a": {"app_id": "cli_a", "app_secret": "x"},
                    "b": {"app_id": "cli_b", "app_secret": "y"},
                }
            }
        }
        assert upgrade_bare_feishu_target_for_multi_bot_config("feishu") == "feishu"


def test_upgrade_preserves_explicit_feishu_app_id() -> None:
    with patch("jiuwenclaw.config.get_config_raw") as m:
        m.return_value = {"channels": {"feishu": {"a": {"app_id": "cli_a", "app_secret": "x"}}}}
        assert (
            upgrade_bare_feishu_target_for_multi_bot_config("feishu:cli_a") == "feishu:cli_a"
        )
