# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import pytest

from jiuwenclaw.gateway.channel_manager.web.app_web_handlers import WebHandlersBindParams, _register_web_handlers


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

    monkeypatch.setattr("jiuwenclaw.gateway.channel_manager.web.app_web_handlers.get_config_raw",
                        lambda: {"preferred_language": "zh"})
    monkeypatch.setattr("jiuwenclaw.gateway.channel_manager.web.app_web_handlers.get_config",
                        lambda: {"modes": {"team": {}}})
    monkeypatch.setattr(
        "jiuwenclaw.gateway.channel_manager.web.app_web_handlers.replace_teams_in_config",
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

    monkeypatch.setattr("jiuwenclaw.gateway.channel_manager.web.app_web_handlers.get_config_raw",
                        lambda: {"preferred_language": "zh"})
    monkeypatch.setattr(
        "jiuwenclaw.gateway.channel_manager.web.app_web_handlers.replace_teams_in_config",
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
