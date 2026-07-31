from __future__ import annotations

from typing import Any

import pytest

from jiuwenswarm.agents.harness.team import team_name_generator


def _team_config() -> dict[str, Any]:
    return {
        "preferred_language": "zh",
        "models": {
            "defaults": [
                {
                    "model_client_config": {
                        "model_name": "mock-model",
                        "client_provider": "OpenAI",
                        "api_key": "mock-api-key",
                        "api_base": "http://127.0.0.1:1234/v1",
                    },
                    "model_config_obj": {"temperature": 0},
                }
            ]
        },
        "modes": {
            "team": {
                "default_team": {
                    "team_name": "default_team",
                    "agents": {"leader": {}, "teammate": {}},
                }
            }
        },
    }


@pytest.mark.asyncio
async def test_generate_team_name_uses_default_template_model(monkeypatch):
    captured: dict[str, Any] = {}

    class FakeTinyAgent:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def run(self, content: str):
            captured["content"] = content
            return {"team_name": "multilingual_research_team"}

    def fake_create_tiny_agent(**kwargs):
        captured.update(kwargs)
        captured["resolved_model"] = kwargs["model_resolver"](kwargs["model_name"])
        return FakeTinyAgent()

    monkeypatch.setattr(team_name_generator, "create_tiny_agent", fake_create_tiny_agent)

    result = await team_name_generator.generate_team_name(
        "研究多语言大模型",
        config_base=_team_config(),
        template_id="default_team",
    )

    assert result == "multilingual_research_team"
    assert captured["model_name"] == "mock-model"
    assert captured["resolved_model"].model_request_config.model_name == "mock-model"
    assert captured["content"] == "研究多语言大模型"
    assert "概括任务主题" in captured["system_prompt"]
    assert "TeamLeader" in captured["system_prompt"]


@pytest.mark.asyncio
async def test_generate_team_name_uses_tiny_agent_even_when_query_mentions_team_name(monkeypatch):
    prompts: list[str] = []

    class FakeTinyAgent:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def run(self, content: str):
            prompts.append(content)
            return {"team_name": "team_setup_task"}

    monkeypatch.setattr(
        team_name_generator,
        "create_tiny_agent",
        lambda **kwargs: FakeTinyAgent(),
    )

    result = await team_name_generator.generate_team_name(
        "新建一个team_name为123的team",
        config_base=_team_config(),
        template_id="default_team",
    )

    assert result == "team_setup_task"
    assert prompts == ["新建一个team_name为123的team"]


@pytest.mark.asyncio
async def test_generate_team_name_retries_generic_placeholder(monkeypatch):
    prompts: list[str] = []

    class FakeTinyAgent:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def run(self, content: str):
            prompts.append(content)
            if len(prompts) == 1:
                return {"team_name": "team_namer"}
            return {"team_name": "silver_orbit"}

    monkeypatch.setattr(
        team_name_generator,
        "create_tiny_agent",
        lambda **kwargs: FakeTinyAgent(),
    )

    result = await team_name_generator.generate_team_name(
        "帮我随机想一个 team_name",
        config_base=_team_config(),
        template_id="default_team",
    )

    assert result == "silver_orbit"
    assert len(prompts) == 2
    assert "过于通用" in prompts[1]


@pytest.mark.asyncio
async def test_generate_team_name_rejects_invalid_result(monkeypatch):
    class FakeTinyAgent:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def run(self, content: str):
            return {"team_name": "../escape"}

    monkeypatch.setattr(
        team_name_generator,
        "create_tiny_agent",
        lambda **kwargs: FakeTinyAgent(),
    )

    with pytest.raises(
        team_name_generator.TeamNameGenerationError,
        match="invalid team_name",
    ):
        await team_name_generator.generate_team_name(
            "任意任务",
            config_base=_team_config(),
            template_id="default_team",
        )
