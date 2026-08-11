# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""What each provider actually receives for a reasoning level.

The contract these pin down is per provider, because there is no shared shape:
OpenAI takes an effort enum, Anthropic an integer budget, DeepSeek and DashScope
an on/off switch. The one rule common to all of them is that ``reasoning_level``
itself -- an internal hint -- must never reach an SDK.
"""

from __future__ import annotations

import pytest

from jiuwenswarm.common.reasoning_injector import inject_reasoning_params


def _inject(client: dict, config: dict) -> dict:
    return inject_reasoning_params(model_client_config=client, model_config_obj=config)


def _deepseek_client() -> dict:
    return {
        "client_provider": "DeepSeek",
        "api_base": "https://api.deepseek.com",
        "model_name": "deepseek-v4-pro",
    }


def _dashscope_client() -> dict:
    return {
        "client_provider": "DashScope",
        "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model_name": "deepseek-v4-flash",
    }


def _openai_client(model: str = "o3-mini") -> dict:
    return {
        "client_provider": "OpenAI",
        "api_base": "https://api.openai.com/v1",
        "model_name": model,
    }


def _anthropic_client(model: str = "claude-sonnet-4-5") -> dict:
    return {
        "client_provider": "Anthropic",
        "api_base": "https://api.anthropic.com",
        "model_name": model,
    }


# ----------------------------------------------------- the universal rule


@pytest.mark.parametrize("level", ["off", "low", "medium", "high"])
def test_reasoning_level_never_reaches_the_sdk(level: str) -> None:
    """ModelRequestConfig allows extras, so a leak here becomes a request field."""
    for client in (_deepseek_client(), _openai_client(), _anthropic_client()):
        out = _inject(client, {"reasoning_level": level})
        assert "reasoning_level" not in out


def test_an_unresolved_provider_is_left_alone() -> None:
    out = _inject(
        {"client_provider": "Ollama", "api_base": "http://localhost:11434", "model_name": "qwen"},
        {"reasoning_level": "high", "temperature": 0.5},
    )
    assert out == {"temperature": 0.5}


# ---------------------------------------------------------------- DeepSeek


def test_low_does_not_force_reasoning_effort_high() -> None:
    """Every non-off level used to collapse to effort=high."""
    out = _inject(_deepseek_client(), {"temperature": 0.2, "reasoning_level": "low"})

    assert out["extra_body"]["thinking"] == {"type": "enabled"}
    assert "reasoning_effort" not in out
    assert out["temperature"] == 0.2


def test_medium_does_not_force_reasoning_effort_high() -> None:
    out = _inject(_deepseek_client(), {"reasoning_level": "medium"})

    assert out["extra_body"]["thinking"] == {"type": "enabled"}
    assert "reasoning_effort" not in out


def test_deepseek_high_still_sends_effort_high() -> None:
    """The one level that always meant effort=high keeps meaning it."""
    out = _inject(_deepseek_client(), {"reasoning_level": "high"})

    assert out["extra_body"]["thinking"] == {"type": "enabled"}
    assert out["reasoning_effort"] == "high"


def test_deepseek_off_disables_thinking() -> None:
    out = _inject(_deepseek_client(), {"reasoning_level": "off"})

    assert out["extra_body"]["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in out


def test_deepseek_preserves_unrelated_extra_body_keys() -> None:
    out = _inject(
        _deepseek_client(),
        {"reasoning_level": "low", "extra_body": {"custom_option": {"enabled": True}}},
    )

    assert out["extra_body"]["custom_option"] == {"enabled": True}
    assert out["extra_body"]["thinking"] == {"type": "enabled"}


# --------------------------------------------------------------- DashScope


def test_dashscope_low_enables_thinking_without_effort() -> None:
    out = _inject(_dashscope_client(), {"reasoning_level": "low"})

    assert out["extra_body"]["enable_thinking"] is True
    assert "reasoning_effort" not in out


def test_dashscope_high_still_sends_effort_high() -> None:
    out = _inject(_dashscope_client(), {"reasoning_level": "high"})

    assert out["extra_body"]["enable_thinking"] is True
    assert out["reasoning_effort"] == "high"


def test_dashscope_off_disables_thinking() -> None:
    out = _inject(_dashscope_client(), {"reasoning_level": "off"})

    assert out["extra_body"]["enable_thinking"] is False


# ------------------------------------------------------------------ OpenAI


@pytest.mark.parametrize("level", ["low", "medium", "high"])
def test_openai_maps_the_level_straight_through(level: str) -> None:
    out = _inject(_openai_client(), {"reasoning_level": level})
    assert out["reasoning_effort"] == level


def test_openai_off_omits_reasoning_effort() -> None:
    """``none`` is not sent: omitting is what every model accepts."""
    out = _inject(_openai_client(), {"reasoning_level": "off"})
    assert "reasoning_effort" not in out


def test_openai_off_clears_a_preconfigured_effort() -> None:
    out = _inject(_openai_client(), {"reasoning_level": "off", "reasoning_effort": "high"})
    assert "reasoning_effort" not in out


def test_openai_chat_model_does_not_get_reasoning_effort() -> None:
    """gpt-4o 400s on reasoning_effort; the level must be dropped, not applied."""
    out = _inject(_openai_client("gpt-4o"), {"reasoning_level": "high", "temperature": 0.5})

    assert "reasoning_effort" not in out
    assert "reasoning_level" not in out
    assert out["temperature"] == 0.5


def test_openai_gpt5_chat_model_does_not_get_reasoning_effort() -> None:
    """gpt-5-chat-latest is a ChatGPT-instant id and 400s on reasoning_effort."""
    out = _inject(_openai_client("gpt-5-chat-latest"), {"reasoning_level": "high", "temperature": 0.5})

    assert "reasoning_effort" not in out
    assert "reasoning_level" not in out
    assert out["temperature"] == 0.5


def test_openai_does_not_touch_thinking() -> None:
    out = _inject(_openai_client(), {"reasoning_level": "high"})
    assert "thinking" not in out


# --------------------------------------------------------------- Anthropic


def test_anthropic_maps_high_to_budget_tokens() -> None:
    out = _inject(_anthropic_client(), {"reasoning_level": "high", "max_tokens": 32000})

    assert out["thinking"] == {"type": "enabled", "budget_tokens": 16000}
    assert out["max_tokens"] == 32000


@pytest.mark.parametrize(
    ("level", "budget"), [("low", 1024), ("medium", 8000), ("high", 16000)],
)
def test_anthropic_budget_per_level(level: str, budget: int) -> None:
    out = _inject(_anthropic_client(), {"reasoning_level": level, "max_tokens": 64000})
    assert out["thinking"]["budget_tokens"] == budget


def test_anthropic_off_omits_thinking() -> None:
    """Omitted, not ``{type: disabled}``: models without thinking reject the field."""
    out = _inject(_anthropic_client(), {"reasoning_level": "off"})
    assert "thinking" not in out


def test_anthropic_off_clears_a_preconfigured_thinking_block() -> None:
    out = _inject(
        _anthropic_client(),
        {"reasoning_level": "off", "thinking": {"type": "enabled", "budget_tokens": 4096}},
    )
    assert "thinking" not in out


def test_anthropic_raises_max_tokens_that_cannot_fit_the_budget() -> None:
    """Anthropic requires budget_tokens < max_tokens, or the request 400s."""
    out = _inject(_anthropic_client(), {"reasoning_level": "high", "max_tokens": 8000})

    assert out["thinking"]["budget_tokens"] == 16000
    assert out["max_tokens"] > 16000


def test_anthropic_raises_max_tokens_with_only_one_token_of_answer_room() -> None:
    """budget+1 is accepted by the API and leaves a near-empty reply."""
    out = _inject(_anthropic_client(), {"reasoning_level": "medium", "max_tokens": 8001})

    assert out["thinking"]["budget_tokens"] == 8000
    assert out["max_tokens"] - 8000 >= 4096


def test_anthropic_coerces_string_max_tokens_instead_of_shrinking_them() -> None:
    out = _inject(_anthropic_client(), {"reasoning_level": "high", "max_tokens": "32000"})

    assert out["thinking"]["budget_tokens"] == 16000
    assert out["max_tokens"] == 32000


@pytest.mark.parametrize("level", ["low", "medium", "high"])
def test_anthropic_sets_max_tokens_when_it_is_unset(level: str) -> None:
    """The unset case is the one that looks safe and is not.

    The client substitutes 8192. Against that default ``high`` (16000) is
    rejected outright, and ``medium`` (8000) is *accepted* with 192 tokens left
    for the answer -- a reply that reads like the model had nothing to say.
    """
    out = _inject(_anthropic_client(), {"reasoning_level": level})

    budget = out["thinking"]["budget_tokens"]
    assert out["max_tokens"] > budget
    assert out["max_tokens"] - budget >= 4096, "the answer needs room, not a sliver"


def test_anthropic_off_does_not_invent_a_max_tokens() -> None:
    """No thinking, no reason to override the caller's ceiling."""
    out = _inject(_anthropic_client(), {"reasoning_level": "off"})

    assert "max_tokens" not in out


def test_anthropic_does_not_send_reasoning_effort() -> None:
    """An OpenAI-only knob on a Messages request is a hard error."""
    out = _inject(_anthropic_client(), {"reasoning_level": "high"})
    assert "reasoning_effort" not in out


def test_anthropic_clears_a_stale_reasoning_effort() -> None:
    out = _inject(
        _anthropic_client(),
        {"reasoning_level": "high", "reasoning_effort": "medium"},
    )
    assert "reasoning_effort" not in out


def test_anthropic_drops_incompatible_sampling_knobs_with_thinking() -> None:
    """Extended thinking rejects non-default temperature and any top_p/top_k."""
    out = _inject(
        _anthropic_client(),
        {
            "reasoning_level": "high",
            "temperature": 0.95,
            "top_p": 0.9,
            "top_k": 40,
            "max_tokens": 32000,
        },
    )

    assert out["thinking"] == {"type": "enabled", "budget_tokens": 16000}
    assert "temperature" not in out
    assert "top_p" not in out
    assert "top_k" not in out


def test_anthropic_off_preserves_sampling_knobs() -> None:
    out = _inject(
        _anthropic_client(),
        {"reasoning_level": "off", "temperature": 0.95, "top_p": 0.9},
    )

    assert "thinking" not in out
    assert out["temperature"] == 0.95
    assert out["top_p"] == 0.9


# ---------------------------------------------------------------- no level


def test_without_a_level_nothing_is_injected() -> None:
    out = _inject(_deepseek_client(), {"temperature": 0.4})

    assert out == {"temperature": 0.4}


def test_an_unrecognised_level_is_ignored() -> None:
    out = _inject(_deepseek_client(), {"reasoning_level": "turbo", "temperature": 0.4})

    assert out == {"temperature": 0.4}
