# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import pytest

from jiuwenclaw.config import _sandbox_yaml_to_env_overlay


class TestSandboxYamlToEnvOverlay:
    @staticmethod
    def test_maps_url_type_enabled_true():
        out = _sandbox_yaml_to_env_overlay(
            {"url": "http://sb/v1", "type": "jiuwenbox", "enabled": True}
        )
        assert out == {
            "JIUWENCLAW_SANDBOX_URL": "http://sb/v1",
            "JIUWENCLAW_SANDBOX_TYPE": "jiuwenbox",
            "JIUWENCLAW_SANDBOX_ENABLED": "true",
        }

    @staticmethod
    def test_maps_enabled_false_string():
        out = _sandbox_yaml_to_env_overlay({"enabled": "false"})
        assert out == {"JIUWENCLAW_SANDBOX_ENABLED": "false"}

    @staticmethod
    def test_skips_missing_or_none_fields():
        out = _sandbox_yaml_to_env_overlay({"url": "u", "type": None})
        assert out == {"JIUWENCLAW_SANDBOX_URL": "u"}

    @staticmethod
    def test_skips_empty_string_url_type():
        out = _sandbox_yaml_to_env_overlay({"url": "  ", "type": ""})
        assert out == {}

    @staticmethod
    def test_strips_whitespace_around_url_type():
        out = _sandbox_yaml_to_env_overlay({"url": "  u  ", "type": "\tt\n"})
        assert out == {
            "JIUWENCLAW_SANDBOX_URL": "u",
            "JIUWENCLAW_SANDBOX_TYPE": "t",
        }

    @staticmethod
    def test_enabled_accepts_yes_on_off():
        assert _sandbox_yaml_to_env_overlay({"enabled": "yes"}) == {
            "JIUWENCLAW_SANDBOX_ENABLED": "true"
        }
        assert _sandbox_yaml_to_env_overlay({"enabled": "off"}) == {
            "JIUWENCLAW_SANDBOX_ENABLED": "false"
        }

    @staticmethod
    def test_enabled_invalid_raises_value_error():
        with pytest.raises(ValueError, match="sandbox.enabled"):
            _sandbox_yaml_to_env_overlay({"enabled": "maybe"})

    @staticmethod
    def test_non_dict_input_returns_empty():
        assert _sandbox_yaml_to_env_overlay("not-a-dict") == {}
        assert _sandbox_yaml_to_env_overlay(None) == {}

    @staticmethod
    def test_empty_dict_returns_empty():
        assert _sandbox_yaml_to_env_overlay({}) == {}

    @staticmethod
    def test_maps_startup_mode_and_policy_file():
        out = _sandbox_yaml_to_env_overlay(
            {
                "startup_mode": "internal",
                "policy_file": "code-agent-policy.yaml",
            }
        )
        assert out == {
            "JIUWENCLAW_SANDBOX_STARTUP_MODE": "internal",
            "JIUWENCLAW_SANDBOX_POLICY_FILE": "code-agent-policy.yaml",
        }

    @staticmethod
    def test_invalid_startup_mode_raises():
        with pytest.raises(ValueError, match="STARTUP_MODE"):
            _sandbox_yaml_to_env_overlay({"startup_mode": "sidecar"})
