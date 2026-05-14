# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for config module."""

import math
from pathlib import Path

import pytest
import yaml

from jiuwenclaw.common.config import get_config_raw, replace_teams_in_config, resolve_env_vars


class TestResolveEnvVars:
    """Test environment variable resolution in config."""

    @staticmethod
    def test_resolve_string_with_env_var(monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("TEST_VAR", "test_value")
        result = resolve_env_vars("${TEST_VAR}")
        assert result == "test_value"

    @staticmethod
    def test_resolve_string_with_default():
        result = resolve_env_vars("${TEST_VAR:-default_value}")
        assert result == "default_value"

    @staticmethod
    def test_resolve_string_with_env_and_default(monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("TEST_VAR", "actual_value")
        result = resolve_env_vars("${TEST_VAR:-default_value}")
        assert result == "actual_value"

    @staticmethod
    def test_resolve_empty_string():
        result = resolve_env_vars("")
        assert result == ""

    @staticmethod
    def test_resolve_string_without_env_var():
        result = resolve_env_vars("plain_string")
        assert result == "plain_string"

    @staticmethod
    def test_resolve_dict_with_env_vars(monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("API_KEY", "secret_key")
        monkeypatch.setenv("PORT", "8080")
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
    def test_resolve_list_with_env_vars(monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("VAR1", "value1")
        monkeypatch.setenv("VAR2", "value2")
        input_list = [
            "${VAR1}",
            "${VAR2:-default}",
            "static_value",
        ]
        result = resolve_env_vars(input_list)
        assert result == ["value1", "value2", "static_value"]

    @staticmethod
    def test_resolve_nested_structure(monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("HOST", "example.com")
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
    def test_resolve_multiple_vars_in_string(monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("USER", "john")
        monkeypatch.setenv("DOMAIN", "example.com")
        result = resolve_env_vars("${USER}@${DOMAIN}")
        assert result == "john@example.com"

    @staticmethod
    def test_resolve_non_string_types():
        assert resolve_env_vars(123) == 123
        assert resolve_env_vars(True) is True
        assert resolve_env_vars(None) is None
        assert math.isclose(resolve_env_vars(3.14), 3.14)


class TestConfigFunctions:
    """Test config module functions."""

    @staticmethod
    def test_get_config_raw(temp_config_file: Path):
        config = get_config_raw()
        assert config is not None
        assert "model" in config or "channels" in config

    @staticmethod
    def test_config_file_structure(temp_config_file: Path):
        config = get_config_raw()
        expected_keys = {"model", "channels", "evolution", "heartbeat"}
        actual_keys = set(config.keys())
        assert len(actual_keys & expected_keys) > 0, "Config should have at least some expected keys"


class TestTeamModesConfig:
    """Test team config persistence under modes.team."""

    @staticmethod
    def _front_payload(team_names: list[str] | None = None, *, include_teammate: bool = False) -> dict:
        names = team_names or ["alpha_team", "beta_team"]
        return {
            "agents": {
                "agent_1": {
                    "model": {
                        "provider": "OpenAI",
                        "model": "gpt-4.1",
                        "api_base": "${OPENAI_BASE_URL:-https://api.openai.com/v1}",
                        "api_key": "${OPENAI_API_KEY}",
                    },
                    "skills": ["team-management"],
                    "workspace": {
                        "stable_base": True,
                    },
                    "max_iterations": 200,
                    "completion_timeout": 600.0,
                },
                "agent_2": {
                    "model": {
                        "provider": "OpenAI",
                        "model": "gpt-4.1-mini",
                        "api_base": "${OPENAI_BASE_URL:-https://api.openai.com/v1}",
                        "api_key": "${OPENAI_API_KEY}",
                    },
                    "skills": ["coding"],
                    "workspace": {
                        "stable_base": True,
                    },
                    "max_iterations": 80,
                    "completion_timeout": 600.0,
                },
            },
            "team": [
                {
                    "team_name": team_name,
                    "lifecycle": "persistent",
                    "teammate_mode": "build_mode",
                    "spawn_mode": "inprocess",
                    "leader": {
                        "member_name": f"{team_name}_leader",
                        "display_name": f"{team_name} leader",
                        "persona": "Lead planning and coordination",
                        "agent_key": "agent_1",
                    },
                    **(
                        {
                            "teammate": {
                                "member_name": f"{team_name}_teammate",
                                "display_name": f"{team_name} teammate",
                                "persona": "Handle analysis and execution",
                                "agent_key": "agent_2",
                            }
                        }
                        if include_teammate
                        else {}
                    ),
                    "predefined_members": [
                        {
                            "member_name": "analyst",
                            "display_name": "Analyst",
                            "role_type": "teammate",
                            "persona": "Analyze requirements",
                            "prompt_hint": "Analyze first",
                            "agent_key": "agent_1",
                        },
                        {
                            "member_name": "coder",
                            "display_name": "Coder",
                            "role_type": "teammate",
                            "persona": "Implement and debug",
                            "prompt_hint": "Modify and verify directly",
                            "agent_key": "agent_2",
                        },
                    ],
                }
                for team_name in names
            ],
        }

    @staticmethod
    def test_replace_teams_in_config_writes_modes_team_and_keeps_legacy_team(
        monkeypatch: pytest.MonkeyPatch,
        temp_config_file: Path,
    ):
        temp_config_file.write_text(
            """
channels:
  web:
    enabled: true
team:
  team_name: legacy_team
modes:
  agent:
    fast: {}
  code: {}
""",
            encoding="utf-8",
        )
        monkeypatch.setattr("jiuwenclaw.common.config._CONFIG_YAML_PATH", temp_config_file)

        replace_teams_in_config(TestTeamModesConfig._front_payload(["alpha_team"]))

        raw = yaml.safe_load(temp_config_file.read_text(encoding="utf-8"))
        assert raw["team"] == {"team_name": "legacy_team"}
        saved = raw["modes"]["team"]["alpha_team"]
        assert saved["team_name"] == "alpha_team"
        assert saved["leader"] == {
            "member_name": "alpha_team_leader",
            "display_name": "alpha_team leader",
            "persona": "Lead planning and coordination",
        }
        assert all("agent_key" not in item for item in saved["predefined_members"])
        assert saved["agents"]["leader"]["model"]["model_client_config"]["client_provider"] == "OpenAI"
        assert saved["agents"]["leader"]["model"]["model_client_config"]["timeout"] == 1800
        assert saved["agents"]["leader"]["model"]["model_client_config"]["verify_ssl"] is False
        assert saved["agents"]["leader"]["model"]["model_client_config"]["custom_headers"] == {}
        assert saved["agents"]["leader"]["model"]["model_request_config"]["model"] == "gpt-4.1"
        assert saved["agents"]["analyst"]["skills"] == ["team-management"]
        assert saved["agents"]["coder"]["skills"] == ["coding"]
        assert "teammate" not in saved
        assert "teammate" not in saved["agents"]

    @staticmethod
    def test_replace_teams_in_config_expands_reused_agent_specs_without_yaml_aliases(
        monkeypatch: pytest.MonkeyPatch,
        temp_config_file: Path,
    ):
        temp_config_file.write_text(
            """
channels:
  web:
    enabled: true
modes:
  team: {}
""",
            encoding="utf-8",
        )
        monkeypatch.setattr("jiuwenclaw.common.config._CONFIG_YAML_PATH", temp_config_file)

        replace_teams_in_config(TestTeamModesConfig._front_payload(["alpha_team"], include_teammate=True))

        saved_text = temp_config_file.read_text(encoding="utf-8")
        assert "&id" not in saved_text
        assert "*id" not in saved_text
        raw = yaml.safe_load(saved_text)
        saved = raw["modes"]["team"]["alpha_team"]
        assert saved["teammate"] == {
            "member_name": "alpha_team_teammate",
            "display_name": "alpha_team teammate",
            "persona": "Handle analysis and execution",
        }
        assert saved["agents"]["teammate"]["skills"] == ["coding"]
        assert saved["agents"]["teammate"] is not saved["agents"]["coder"]

    @staticmethod
    def test_replace_teams_in_config_only_writes_teammate_when_explicitly_provided(
        monkeypatch: pytest.MonkeyPatch,
        temp_config_file: Path,
    ):
        monkeypatch.setattr("jiuwenclaw.common.config._CONFIG_YAML_PATH", temp_config_file)

        replace_teams_in_config(TestTeamModesConfig._front_payload(["alpha_team"]))

        raw = yaml.safe_load(temp_config_file.read_text(encoding="utf-8"))
        saved = raw["modes"]["team"]["alpha_team"]
        assert "teammate" not in saved
        assert "teammate" not in saved["agents"]

    @staticmethod
    def test_replace_teams_in_config_rejects_duplicate_team_names(
        monkeypatch: pytest.MonkeyPatch,
        temp_config_file: Path,
    ):
        monkeypatch.setattr("jiuwenclaw.common.config._CONFIG_YAML_PATH", temp_config_file)

        with pytest.raises(ValueError, match="duplicate team_name"):
            replace_teams_in_config(TestTeamModesConfig._front_payload(["alpha_team", "alpha_team"]))

    @staticmethod
    def test_replace_teams_in_config_rejects_unknown_agent_key(
        monkeypatch: pytest.MonkeyPatch,
        temp_config_file: Path,
    ):
        monkeypatch.setattr("jiuwenclaw.common.config._CONFIG_YAML_PATH", temp_config_file)
        payload = TestTeamModesConfig._front_payload(["alpha_team"])
        payload["team"][0]["predefined_members"][1]["agent_key"] = "missing_agent"

        with pytest.raises(ValueError, match="unknown agent_key"):
            replace_teams_in_config(payload)

    @staticmethod
    def test_replace_teams_in_config_rejects_unknown_teammate_agent_key(
        monkeypatch: pytest.MonkeyPatch,
        temp_config_file: Path,
    ):
        monkeypatch.setattr("jiuwenclaw.common.config._CONFIG_YAML_PATH", temp_config_file)
        payload = TestTeamModesConfig._front_payload(["alpha_team"], include_teammate=True)
        payload["team"][0]["teammate"]["agent_key"] = "missing_agent"

        with pytest.raises(ValueError, match="unknown agent_key"):
            replace_teams_in_config(payload)

    @staticmethod
    def test_replace_teams_in_config_replaces_entire_modes_team(
        monkeypatch: pytest.MonkeyPatch,
        temp_config_file: Path,
    ):
        monkeypatch.setattr("jiuwenclaw.common.config._CONFIG_YAML_PATH", temp_config_file)

        replace_teams_in_config(TestTeamModesConfig._front_payload(["alpha_team", "beta_team"]))
        replace_teams_in_config(TestTeamModesConfig._front_payload(["gamma_team"]))

        raw = yaml.safe_load(temp_config_file.read_text(encoding="utf-8"))
        assert list(raw["modes"]["team"].keys()) == ["gamma_team"]

    @staticmethod
    def test_replace_teams_in_config_rejects_duplicate_member_names(
        monkeypatch: pytest.MonkeyPatch,
        temp_config_file: Path,
    ):
        monkeypatch.setattr("jiuwenclaw.common.config._CONFIG_YAML_PATH", temp_config_file)
        payload = TestTeamModesConfig._front_payload(["alpha_team"])
        payload["team"][0]["predefined_members"][1]["member_name"] = "analyst"

        with pytest.raises(ValueError, match="duplicate member_name"):
            replace_teams_in_config(payload)
