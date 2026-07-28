# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for config module."""

import math
import os
from pathlib import Path

import pytest
import yaml

from jiuwenclaw.config import (
    clear_config_cache,
    get_config,
    get_config_raw,
    get_merged_config_dict,
    merge_template_with_override,
    resolve_env_vars,
    resolve_template_config_path,
)
from jiuwenclaw.local_env_config import (
    ENV_CONFIG_DICT,
    apply_env_overrides_to_active,
    bind_agent_env_ns,
    bind_task_env_overlay,
    clear_staged_env,
    parse_env_ns_key,
    reset_agent_env_ns,
    reset_local_env_state_for_tests,
    reset_task_env_overlay,
)


def _drop_namespaced_os_environ() -> None:
    """Remove track-B ``{sid}__{aid}__*`` keys left by other test modules."""
    for key in list(os.environ):
        if parse_env_ns_key(key) is not None:
            os.environ.pop(key, None)


@pytest.fixture(autouse=True)
def _reset_env_state():
    saved_environ = {
        k: v for k, v in os.environ.items() if parse_env_ns_key(k) is None
    }
    reset_local_env_state_for_tests()
    ENV_CONFIG_DICT.clear()
    clear_staged_env()
    _drop_namespaced_os_environ()
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

    @staticmethod
    def test_spawn_key_reads_os_environ_extension_dirs():
        """SPAWN keys (e.g. EXTENSION_DIRS) resolve from process env only."""
        os.environ["EXTENSION_DIRS"] = "E:/ext;D:/ext"
        assert resolve_env_vars("${EXTENSION_DIRS}") == "E:/ext;D:/ext"
        assert resolve_env_vars("${EXTENSION_DIRS:-extensions}") == "E:/ext;D:/ext"

    @staticmethod
    def test_spawn_key_uses_default_when_unset():
        os.environ.pop("EXTENSION_DIRS", None)
        assert resolve_env_vars("${EXTENSION_DIRS:-extensions}") == "extensions"

    @staticmethod
    def test_spawn_key_ignores_tip_bag():
        """Track-A keys must not be served from tip even if wrongly present."""
        ENV_CONFIG_DICT["EXTENSION_DIRS"] = "from-tip"
        os.environ["EXTENSION_DIRS"] = "from-process"
        assert resolve_env_vars("${EXTENSION_DIRS}") == "from-process"

    @staticmethod
    def test_business_key_ignores_bare_os_environ():
        """Agent-isolated keys must not fall back to bare os.environ (spawn leak)."""
        os.environ["API_KEY"] = "leaked-from-spawn"
        assert resolve_env_vars("${API_KEY}") == ""
        assert resolve_env_vars("${API_KEY:-fallback}") == "fallback"

    @staticmethod
    def test_business_key_prefers_tip_over_bare_os_environ():
        os.environ["API_KEY"] = "leaked-from-spawn"
        ENV_CONFIG_DICT["API_KEY"] = "from-tip"
        assert resolve_env_vars("${API_KEY}") == "from-tip"
        assert resolve_env_vars("${API_KEY:-fallback}") == "from-tip"


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


class TestGetConfigNsCache:
    """get_config() resolved cache is keyed by bind_agent_env_ns."""

    @staticmethod
    def test_different_ns_do_not_share_resolved_api_key(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            "react:\n  model_client_config:\n    api_key: ${API_KEY}\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("jiuwenclaw.utils.get_config_file", lambda: cfg_path)
        monkeypatch.setattr("jiuwenclaw.config.get_config_file", lambda: cfg_path)
        monkeypatch.setattr(
            "jiuwenclaw.config.resolve_template_config_path",
            lambda: cfg_path,
        )
        clear_config_cache()

        apply_env_overrides_to_active(
            {"API_KEY": "key-office"}, service_id="default", agent_id="office"
        )
        apply_env_overrides_to_active(
            {"API_KEY": "key-assistant"}, service_id="default", agent_id="assistant"
        )

        tok_a = bind_agent_env_ns("default", "office")
        try:
            cfg_a = get_config()
            assert cfg_a["react"]["model_client_config"]["api_key"] == "key-office"
        finally:
            reset_agent_env_ns(tok_a)

        tok_b = bind_agent_env_ns("default", "assistant")
        try:
            cfg_b = get_config()
            assert cfg_b["react"]["model_client_config"]["api_key"] == "key-assistant"
        finally:
            reset_agent_env_ns(tok_b)

        # Re-enter office: must still be office key (not assistant from shared cache).
        tok_a2 = bind_agent_env_ns("default", "office")
        try:
            cfg_a2 = get_config()
            assert cfg_a2["react"]["model_client_config"]["api_key"] == "key-office"
            assert cfg_a2 is cfg_a  # same ns slot hit
        finally:
            reset_agent_env_ns(tok_a2)

    @staticmethod
    def test_tip_mutation_invalidates_ns_slot(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            "react:\n  model_client_config:\n    api_key: ${API_KEY}\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("jiuwenclaw.utils.get_config_file", lambda: cfg_path)
        monkeypatch.setattr("jiuwenclaw.config.get_config_file", lambda: cfg_path)
        monkeypatch.setattr(
            "jiuwenclaw.config.resolve_template_config_path",
            lambda: cfg_path,
        )
        clear_config_cache()

        apply_env_overrides_to_active(
            {"API_KEY": "before"}, service_id="default", agent_id="office"
        )
        tok = bind_agent_env_ns("default", "office")
        try:
            assert get_config()["react"]["model_client_config"]["api_key"] == "before"
            apply_env_overrides_to_active(
                {"API_KEY": "after"}, service_id="default", agent_id="office"
            )
            assert get_config()["react"]["model_client_config"]["api_key"] == "after"
        finally:
            reset_agent_env_ns(tok)

    @staticmethod
    def test_overlay_bypasses_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            "react:\n  model_client_config:\n    api_key: ${API_KEY}\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("jiuwenclaw.utils.get_config_file", lambda: cfg_path)
        monkeypatch.setattr("jiuwenclaw.config.get_config_file", lambda: cfg_path)
        monkeypatch.setattr(
            "jiuwenclaw.config.resolve_template_config_path",
            lambda: cfg_path,
        )
        clear_config_cache()

        apply_env_overrides_to_active(
            {"API_KEY": "from-tip"}, service_id="default", agent_id="office"
        )
        tok = bind_agent_env_ns("default", "office")
        try:
            assert get_config()["react"]["model_client_config"]["api_key"] == "from-tip"
            overlay_tok = bind_task_env_overlay({"API_KEY": "from-overlay"})
            try:
                assert (
                    get_config()["react"]["model_client_config"]["api_key"]
                    == "from-overlay"
                )
            finally:
                reset_task_env_overlay(overlay_tok)
            assert get_config()["react"]["model_client_config"]["api_key"] == "from-tip"
        finally:
            reset_agent_env_ns(tok)
