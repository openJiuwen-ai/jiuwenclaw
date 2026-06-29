# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio

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


class FakeAgentClient:
    def __init__(self):
        self.reload_started = asyncio.Event()
        self.release_reload = asyncio.Event()
        self.reload_finished = asyncio.Event()

    async def send_request(self, envelope):
        self.reload_started.set()
        try:
            await self.release_reload.wait()
            return type("Resp", (), {"ok": True, "payload": {}})()
        finally:
            self.reload_finished.set()


class FakeChannelManager:
    def __init__(self):
        self.configs: dict[str, dict] = {}

    async def set_conf(self, channel_id, new_conf):
        self.configs[channel_id] = dict(new_conf)

    def get_conf(self, channel_id):
        return dict(self.configs.get(channel_id, {}))


class FakeHeartbeatService:
    def __init__(self):
        self.config = {"every": 60.0, "target": "web"}

    async def set_heartbeat_conf(self, *, every=None, target=None, active_hours=None):
        if every is not None:
            self.config["every"] = every
        if target is not None:
            self.config["target"] = target
        if active_hours is not None:
            self.config["active_hours"] = active_hours

    def get_heartbeat_conf(self):
        return dict(self.config)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "params"),
    [
        ("channel.feishu.set_conf", {"enabled": False, "app_id": "app-1"}),
        ("channel.dingtalk.set_conf", {"enabled": False, "client_id": "client-1"}),
        ("heartbeat.set_conf", {"every": 30, "target": "web"}),
    ],
)
async def test_config_save_handlers_respond_before_agent_reload_finishes(monkeypatch, method, params):
    channel = FakeWebChannel()
    agent_client = FakeAgentClient()
    channel_manager = FakeChannelManager()
    heartbeat_service = FakeHeartbeatService()
    persisted: list[tuple[str, dict]] = []

    monkeypatch.setattr(
        "jiuwenswarm.gateway.channel_manager.web.app_web_handlers.update_channel_in_config",
        lambda channel_id, conf: persisted.append((channel_id, dict(conf))),
    )
    monkeypatch.setattr(
        "jiuwenswarm.gateway.channel_manager.web.app_web_handlers.update_heartbeat_in_config",
        lambda payload: persisted.append(("heartbeat", dict(payload))),
    )

    _register_web_handlers(
        WebHandlersBindParams(
            channel=channel,
            agent_client=agent_client,
            channel_manager=channel_manager,
            heartbeat_service=heartbeat_service,
        )
    )

    task = asyncio.create_task(channel.methods[method](object(), "req-save", params, "sess-1"))
    try:
        await asyncio.wait_for(agent_client.reload_started.wait(), timeout=0.5)

        assert persisted
        assert channel.responses[-1]["id"] == "req-save"
        assert channel.responses[-1]["ok"] is True
    finally:
        agent_client.release_reload.set()
        await task
        await asyncio.wait_for(agent_client.reload_finished.wait(), timeout=0.5)


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


@pytest.mark.parametrize(
    ("enabled", "expected"),
    [
        (True, "true"),
        (False, "false"),
    ],
)
def test_config_panel_flatten_reads_team_enable_permissions(enabled: bool, expected: str) -> None:
    raw = {
        "modes": {
            "team": {
                "alpha_team": {
                    "team_name": "alpha_team",
                    "enable_permissions": enabled,
                },
            },
        },
    }

    flat = _flatten_modes_team_for_config_panel(raw)

    assert flat["team_0_enable_permissions"] == expected


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
