# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for config module."""

import math
import os
from pathlib import Path

import pytest
import yaml

from jiuwenclaw.config import (
    get_config_raw,
    get_merged_config_dict,
    merge_template_with_override,
    resolve_env_vars,
    resolve_template_config_path,
)
from jiuwenclaw.local_env_config import ENV_CONFIG_DICT, clear_staged_env, reset_local_env_state_for_tests


@pytest.fixture(autouse=True)
def _reset_env_state():
    saved_environ = dict(os.environ)
    reset_local_env_state_for_tests()
    ENV_CONFIG_DICT.clear()
    clear_staged_env()
    yield
    reset_local_env_state_for_tests()
    ENV_CONFIG_DICT.clear()
    clear_staged_env()
    os.environ.clear()
    os.environ.update(saved_environ)


class TestResolveEnvVars:
    """Test environment variable resolution in config."""

    @staticmethod
    def test_resolve_string_with_env_var():
        """Test resolving string with environment variable."""
        ENV_CONFIG_DICT["TEST_VAR"] = "test_value"
        result = resolve_env_vars("${TEST_VAR}")
        assert result == "test_value"

    @staticmethod
    def test_resolve_string_with_default():
        """Test resolving string with default value."""
        result = resolve_env_vars("${TEST_VAR:-default_value}")
        assert result == "default_value"

    @staticmethod
    def test_resolve_string_with_env_and_default():
        """Test resolving string when env var exists with default."""
        ENV_CONFIG_DICT["TEST_VAR"] = "actual_value"
        result = resolve_env_vars("${TEST_VAR:-default_value}")
        assert result == "actual_value"

    @staticmethod
    def test_resolve_empty_string():
        """Test resolving empty string."""
        result = resolve_env_vars("")
        assert result == ""

    @staticmethod
    def test_resolve_string_without_env_var():
        """Test resolving string without environment variable syntax."""
        result = resolve_env_vars("plain_string")
        assert result == "plain_string"

    @staticmethod
    def test_resolve_dict_with_env_vars():
        """Test resolving dictionary with environment variables."""
        ENV_CONFIG_DICT["API_KEY"] = "secret_key"
        ENV_CONFIG_DICT["PORT"] = "8080"
        input_dict = {
            "api_key": "${API_KEY}",
            "port": "${PORT:-3000}",
            "name": "test",
        }
        result = resolve_env_vars(input_dict)
        assert result == {
            "api_key": "secret_key",
            "port": "8080",
            "name": "test",
        }

    @staticmethod
    def test_resolve_list_with_env_vars():
        """Test resolving list with environment variables."""
        ENV_CONFIG_DICT["VAR1"] = "value1"
        ENV_CONFIG_DICT["VAR2"] = "value2"
        input_list = [
            "${VAR1}",
            "${VAR2:-default}",
            "static_value",
        ]
        result = resolve_env_vars(input_list)
        assert result == ["value1", "value2", "static_value"]

    @staticmethod
    def test_resolve_nested_structure():
        """Test resolving nested structure with environment variables."""
        ENV_CONFIG_DICT["HOST"] = "example.com"
        input_dict = {
            "server": {
                "host": "${HOST}",
                "port": "${PORT:-8080}",
            },
            "features": ["${FEATURE_A:-default_a}", "feature_b"],
        }
        result = resolve_env_vars(input_dict)
        assert result == {
            "server": {
                "host": "example.com",
                "port": "8080",
            },
            "features": ["default_a", "feature_b"],
        }

    @staticmethod
    def test_resolve_multiple_vars_in_string():
        """Test resolving multiple environment variables in one string."""
        ENV_CONFIG_DICT["USER"] = "john"
        ENV_CONFIG_DICT["DOMAIN"] = "example.com"
        result = resolve_env_vars("${USER}@${DOMAIN}")
        assert result == "john@example.com"

    @staticmethod
    def test_resolve_non_string_types():
        """Test that non-string types are returned as-is."""
        assert resolve_env_vars(123) == 123
        assert resolve_env_vars(True) is True
        assert resolve_env_vars(None) is None
        assert math.isclose(resolve_env_vars(3.14), 3.14)


class TestConfigFunctions:
    """Test config module functions."""

    @staticmethod
    def test_merge_template_with_override():
        """Sparse override deep-merges over template; extra override keys kept."""
        template = {"a": 1, "nested": {"x": 1, "y": 2}}
        override = {"nested": {"y": 99}, "extra": True}
        merged = merge_template_with_override(template, override)
        assert merged["a"] == 1
        assert merged["nested"]["x"] == 1
        assert merged["nested"]["y"] == 99
        assert merged["extra"] is True

    @staticmethod
    def test_get_config_raw_is_merged_sparse_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """Override file may be sparse; get_config_raw returns template ∪ override."""
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text("version: 1.0\npreferred_language: en\n", encoding="utf-8")
        monkeypatch.setattr("jiuwenclaw.utils.get_config_file", lambda: cfg_path)
        monkeypatch.setattr("jiuwenclaw.config.get_config_file", lambda: cfg_path)

        raw = get_config_raw()
        assert raw.get("preferred_language") == "en"
        assert raw.get("version") == 1.0
        tpl_path = resolve_template_config_path()
        if tpl_path.exists():
            assert "logging" in raw or "memory" in raw

        merged = get_merged_config_dict()
        assert merged.get("preferred_language") == "en"
