# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""``reasoning_level`` must never reach the model request config of a team agent.

It is an internal hint. ``ModelRequestConfig`` is declared with ``extra=allow``,
so anything left in the request config is forwarded to
``AsyncCompletions.create()`` and fails the call with
``TypeError: got an unexpected keyword argument 'reasoning_level'``.
"""

from __future__ import annotations

from typing import Any

from jiuwenswarm.agents.harness.team.config_loader import load_team_spec_dict


def _config_base(
    *,
    reasoning_level: Any = "medium",
    model_name: str = "deepseek-v4-pro",
    api_base: str = "https://api.deepseek.com",
    provider: str = "DeepSeek",
) -> dict[str, Any]:
    model_config_obj: dict[str, Any] = {"temperature": 0.95}
    if reasoning_level is not None:
        model_config_obj["reasoning_level"] = reasoning_level
    return {
        "models": {
            "defaults": [
                {
                    "model_client_config": {
                        "model_name": model_name,
                        "api_base": api_base,
                        "api_key": "sk-test",
                        "client_provider": provider,
                    },
                    "model_config_obj": model_config_obj,
                }
            ]
        },
        "modes": {
            "team": {
                "jiuwen_team": {
                    "team_name": "jiuwen_team",
                    "agents": {"leader": {}},
                }
            }
        },
    }


def _request_configs(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        key: (agent.get("model") or {}).get("model_request_config") or {}
        for key, agent in (spec.get("agents") or {}).items()
    }


def test_reasoning_level_is_not_forwarded_as_a_request_param():
    spec = load_team_spec_dict(config_base=_config_base())

    request_configs = _request_configs(spec)
    assert request_configs, "team spec produced no agents"
    for agent_key, request_config in request_configs.items():
        assert "reasoning_level" not in request_config, (
            f"agent '{agent_key}' would send reasoning_level to the OpenAI SDK: "
            f"{request_config}"
        )


def test_reasoning_level_medium_enables_thinking_without_effort():
    """medium must not collapse to reasoning_effort=high."""
    spec = load_team_spec_dict(config_base=_config_base())

    for request_config in _request_configs(spec).values():
        assert request_config["extra_body"] == {"thinking": {"type": "enabled"}}
        assert "reasoning_effort" not in request_config


def test_reasoning_level_high_still_sends_effort_high():
    spec = load_team_spec_dict(config_base=_config_base(reasoning_level="high"))

    for request_config in _request_configs(spec).values():
        assert request_config["extra_body"] == {"thinking": {"type": "enabled"}}
        assert request_config["reasoning_effort"] == "high"


def test_reasoning_level_off_disables_thinking():
    spec = load_team_spec_dict(config_base=_config_base(reasoning_level="off"))

    for request_config in _request_configs(spec).values():
        assert "reasoning_level" not in request_config
        assert request_config["extra_body"] == {"thinking": {"type": "disabled"}}
        assert "reasoning_effort" not in request_config


def test_unsupported_provider_still_drops_the_internal_hint():
    """No provider mapping exists, but the hint must not leak to the SDK either."""
    spec = load_team_spec_dict(
        config_base=_config_base(
            model_name="nvidia/nemotron-3-ultra-550b-a55b:free",
            api_base="https://openrouter.ai/api/v1",
            provider="OpenRouter",
        )
    )

    for request_config in _request_configs(spec).values():
        assert "reasoning_level" not in request_config
        assert request_config["temperature"] == 0.95


def test_model_name_is_still_resolved_into_the_request_config():
    spec = load_team_spec_dict(config_base=_config_base(reasoning_level=None))

    for request_config in _request_configs(spec).values():
        assert request_config["model"] == "deepseek-v4-pro"
        assert request_config["temperature"] == 0.95


def test_explicit_model_in_model_config_obj_keeps_precedence():
    config_base = _config_base(reasoning_level=None)
    config_base["models"]["defaults"][0]["model_config_obj"]["model"] = "pinned-model"

    spec = load_team_spec_dict(config_base=config_base)

    for request_config in _request_configs(spec).values():
        assert request_config["model"] == "pinned-model"


def test_missing_model_name_adds_no_empty_model_key():
    config_base = _config_base(reasoning_level=None)
    config_base["models"]["defaults"][0]["model_client_config"].pop("model_name")

    spec = load_team_spec_dict(config_base=config_base)

    for request_config in _request_configs(spec).values():
        assert "model" not in request_config


def test_per_agent_model_override_is_sanitized():
    """Explicit per-agent overrides bypass the default model path entirely."""
    config_base = _config_base()
    config_base["modes"]["team"]["jiuwen_team"]["agents"] = {
        "leader": {
            "model": {
                "model_client_config": {
                    "model_name": "deepseek-v4-pro",
                    "api_base": "https://api.deepseek.com",
                    "api_key": "sk-test",
                    "client_provider": "DeepSeek",
                },
                "model_request_config": {
                    "temperature": 0.3,
                    "reasoning_level": "high",
                },
            }
        }
    }

    spec = load_team_spec_dict(config_base=config_base)

    leader_request = _request_configs(spec)["leader"]
    assert "reasoning_level" not in leader_request
    assert leader_request["extra_body"] == {"thinking": {"type": "enabled"}}
    assert leader_request["reasoning_effort"] == "high"
    assert leader_request["temperature"] == 0.3


def test_openai_reasoning_level_maps_through_the_config_loader():
    spec = load_team_spec_dict(
        config_base=_config_base(
            reasoning_level="medium",
            model_name="o3-mini",
            api_base="https://api.openai.com/v1",
            provider="OpenAI",
        )
    )

    for request_config in _request_configs(spec).values():
        assert "reasoning_level" not in request_config
        assert request_config["reasoning_effort"] == "medium"
        assert "thinking" not in request_config


def test_detection_uses_the_request_model_id_not_the_client_name():
    """A DeepSeek client name must not leak its knob onto a different wire model.

    ``model_config_obj["model"]`` overrides the on-the-wire model id (see
    ``test_explicit_model_in_model_config_obj_keeps_precedence``). Detection
    must use that same id, not the client's ``model_name``, or a DeepSeek
    client config paired with a ``gpt-4o`` override would still get
    ``extra_body.thinking`` injected onto a request that will actually be
    sent as ``gpt-4o``.
    """
    config_base = _config_base(reasoning_level="high")
    config_base["models"]["defaults"][0]["model_config_obj"]["model"] = "gpt-4o"

    spec = load_team_spec_dict(config_base=config_base)

    for request_config in _request_configs(spec).values():
        assert request_config["model"] == "gpt-4o"
        assert "extra_body" not in request_config
        assert "reasoning_effort" not in request_config
        assert "reasoning_level" not in request_config


def test_detection_uses_the_request_model_id_inverse_case():
    """A chat client name overridden to a reasoning request id must get the knob."""
    config_base = _config_base(
        reasoning_level="medium",
        model_name="gpt-4o",
        api_base="https://api.openai.com/v1",
        provider="OpenAI",
    )
    config_base["models"]["defaults"][0]["model_config_obj"]["model"] = "o3-mini"

    spec = load_team_spec_dict(config_base=config_base)

    for request_config in _request_configs(spec).values():
        assert request_config["model"] == "o3-mini"
        assert request_config["reasoning_effort"] == "medium"
        assert "reasoning_level" not in request_config


def test_anthropic_reasoning_level_maps_through_the_config_loader():
    spec = load_team_spec_dict(
        config_base=_config_base(
            reasoning_level="high",
            model_name="claude-sonnet-4-5",
            api_base="https://api.anthropic.com",
            provider="Anthropic",
        )
    )

    for request_config in _request_configs(spec).values():
        assert "reasoning_level" not in request_config
        assert request_config["thinking"] == {
            "type": "enabled",
            "budget_tokens": 16000,
        }
        assert request_config["max_tokens"] > 16000
        assert "temperature" not in request_config
