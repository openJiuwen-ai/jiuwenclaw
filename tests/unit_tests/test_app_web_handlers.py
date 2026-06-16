# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import pytest

from jiuwenswarm.gateway.channel_manager.web.app_web_handlers import (
    WebHandlersBindParams,
    _flatten_modes_team_for_config_panel,
    _flatten_symphony_for_config_panel,
    _register_web_handlers,
)


class FakeWebChannel:
    def __init__(self):
        self.methods: dict[str, object] = {}
        self.responses: list[dict] = []
        self.connect_handler = None

    def register_method(self, name, handler):
        self.methods[name] = handler

    def on_connect(self, handler):
        self.connect_handler = handler

    async def send_response(self, ws, req_id, *, ok, payload=None, error=None, code=None):
        self.responses.append(
            {
                "id": req_id,
                "ok": ok,
                "payload": payload,
                "error": error,
                "code": code,
            }
        )


@pytest.mark.asyncio
async def test_config_set_routes_team_payload_to_modes_team_helper(monkeypatch):
    channel = FakeWebChannel()
    recorded: list[dict] = []

    _register_web_handlers(WebHandlersBindParams(channel=channel))

    monkeypatch.setattr("jiuwenswarm.gateway.channel_manager.web.app_web_handlers.get_config_raw",
                        lambda: {"preferred_language": "zh"})
    monkeypatch.setattr("jiuwenswarm.gateway.channel_manager.web.app_web_handlers.get_config",
                        lambda: {"modes": {"team": {}}})
    monkeypatch.setattr(
        "jiuwenswarm.gateway.channel_manager.web.app_web_handlers.replace_teams_in_config",
        lambda payload: recorded.append(payload),
    )

    await channel.methods["config.set"](
        object(),
        "req-1",
        {
            "agents": {"agent_1": {"model": {"provider": "OpenAI"}}},
            "team": [{"team_name": "alpha_team", "leader": {"agent_key": "agent_1"}}],
        },
        "sess-1",
    )

    assert recorded and recorded[0]["team"][0]["team_name"] == "alpha_team"
    assert channel.responses[-1] == {
        "id": "req-1",
        "ok": True,
        "payload": {"updated": ["modes.team"], "applied_without_restart": True},
        "error": None,
        "code": None,
    }


@pytest.mark.asyncio
async def test_config_set_returns_bad_request_when_team_payload_is_invalid(monkeypatch):
    channel = FakeWebChannel()

    _register_web_handlers(WebHandlersBindParams(channel=channel))

    monkeypatch.setattr("jiuwenswarm.gateway.channel_manager.web.app_web_handlers.get_config_raw",
                        lambda: {"preferred_language": "zh"})
    monkeypatch.setattr(
        "jiuwenswarm.gateway.channel_manager.web.app_web_handlers.replace_teams_in_config",
        lambda payload: (_ for _ in ()).throw(ValueError("duplicate team_name: alpha_team")),
    )

    await channel.methods["config.set"](
        object(),
        "req-2",
        {
            "agents": {"agent_1": {"model": {"provider": "OpenAI"}}},
            "team": [{"team_name": "alpha_team", "leader": {"agent_key": "agent_1"}}],
        },
        "sess-2",
    )

    assert channel.responses[-1] == {
        "id": "req-2",
        "ok": False,
        "payload": None,
        "error": "duplicate team_name: alpha_team",
        "code": "BAD_REQUEST",
    }


def test_config_panel_flatten_reads_standalone_agent_registry():
    raw = {
        "web_config_panel": {
            "agent_team_agents": {
                "agent_1": {
                    "model": {
                        "model_request_config": {
                            "model": "gpt-4.1",
                            "api_base": "https://api.openai.com/v1",
                            "api_key": "${OPENAI_API_KEY}",
                        },
                        "model_client_config": {"client_provider": "OpenAI"},
                    },
                    "skills": ["coding"],
                    "max_iterations": 12,
                    "completion_timeout": 34,
                }
            }
        }
    }

    flat = _flatten_modes_team_for_config_panel(raw)

    assert flat["agent_name_0"] == "agent_1"
    assert flat["agent_model_0"] == "gpt-4.1"
    assert flat["agent_skills_0"] == "coding"
    assert flat["agent_max_iterations_0"] == "12"
    assert flat["agent_completion_timeout_0"] == "34"


def test_config_panel_flatten_reads_symphony_enabled_and_skill_retrieval():
    raw = {
        "symphony": {
            "enabled": True,
            "orchestration": {"mode": "fast"},
            "skill_retrieval": {
                "enabled": True,
                "build": {"branching_factor": 64},
                "retrieve": {"top_k": 5, "flatten_tree": True},
            },
        }
    }

    flat = _flatten_symphony_for_config_panel(raw)

    assert flat["symphony_enabled"] == "true"
    assert "symphony_orchestration_mode" not in flat
    assert flat["skill_retrieval_enabled"] == "true"
    assert flat["skill_retrieval_build_branching_factor"] == "64"
    assert "skill_retrieval_retrieve_top_k" not in flat
    assert flat["skill_retrieval_retrieve_flatten_tree"] == "true"


@pytest.mark.asyncio
async def test_config_set_routes_symphony_payload_to_config_helper(monkeypatch):
    channel = FakeWebChannel()
    recorded_symphony: list[dict] = []
    recorded_skill_retrieval: list[dict] = []

    _register_web_handlers(WebHandlersBindParams(channel=channel))

    monkeypatch.setattr(
        "jiuwenswarm.gateway.channel_manager.web.app_web_handlers.get_config_raw",
        lambda: {"preferred_language": "zh"},
    )
    monkeypatch.setattr(
        "jiuwenswarm.gateway.channel_manager.web.app_web_handlers.get_config",
        lambda: {"symphony": {}},
    )
    monkeypatch.setattr(
        "jiuwenswarm.gateway.channel_manager.web.app_web_handlers.update_symphony_in_config",
        lambda updates: recorded_symphony.append(updates),
    )
    monkeypatch.setattr(
        "jiuwenswarm.gateway.channel_manager.web.app_web_handlers.update_skill_retrieval_in_config",
        lambda updates: recorded_skill_retrieval.append(updates),
    )

    await channel.methods["config.set"](
        object(),
        "req-3",
        {
            "symphony_enabled": "true",
            "skill_retrieval_enabled": "false",
            "skill_retrieval_retrieve_flatten_tree": "true",
        },
        "sess-3",
    )

    assert recorded_symphony == [{"enabled": True}]
    assert recorded_skill_retrieval == [{"enabled": False, "retrieve": {"flatten_tree": True}}]
    assert channel.responses[-1] == {
        "id": "req-3",
        "ok": True,
        "payload": {
            "updated": [
                "symphony_enabled",
                "skill_retrieval_enabled",
                "skill_retrieval_retrieve_flatten_tree",
            ],
            "applied_without_restart": True,
        },
        "error": None,
        "code": None,
    }
