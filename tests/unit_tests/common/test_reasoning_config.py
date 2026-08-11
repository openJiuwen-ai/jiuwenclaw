# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Provider detection and the level tables behind it.

``reasoning_level`` is the product-facing axis. What each provider wants for it
is not the same shape -- an effort enum, an integer budget, an on/off switch --
so detection has to name the provider before anything can be mapped.
"""

from __future__ import annotations

import pytest

from jiuwenswarm.common.reasoning_config import (
    ANTHROPIC_BUDGET_TOKENS,
    ANTHROPIC_THINKING_MODEL_PREFIXES,
    OPENAI_REASONING_MODEL_PREFIXES,
    is_anthropic_thinking_model,
    is_openai_reasoning_model,
    normalize_reasoning_level,
    resolve_reasoning_target,
)


# ------------------------------------------------------------ existing kinds


def test_deepseek_official_still_resolves() -> None:
    """The two kinds that already worked must keep working."""
    assert resolve_reasoning_target(
        client_provider="DeepSeek",
        api_base="https://api.deepseek.com",
        model_name="deepseek-v4-pro",
    ) == ("deepseek_official", "deepseek-v4-pro")


def test_dashscope_bailian_still_resolves() -> None:
    assert resolve_reasoning_target(
        client_provider="DashScope",
        api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model_name="deepseek-v4-flash",
    ) == ("dashscope_bailian", "deepseek-v4-flash")


def test_a_deepseek_host_with_an_unsupported_model_does_not_resolve() -> None:
    assert resolve_reasoning_target(
        client_provider="DeepSeek",
        api_base="https://api.deepseek.com",
        model_name="deepseek-chat",
    ) is None


# ------------------------------------------------------------------- OpenAI


@pytest.mark.parametrize("model", ["o1", "o1-mini", "o3", "o3-mini", "o4-mini", "gpt-5", "gpt-5-mini"])
def test_openai_reasoning_models_are_recognised(model: str) -> None:
    assert is_openai_reasoning_model(model) is True


@pytest.mark.parametrize("model", ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo", "", "  "])
def test_plain_chat_models_are_not_reasoning_models(model: str) -> None:
    """Sending reasoning_effort to these is a 400, not a degraded answer."""
    assert is_openai_reasoning_model(model) is False


@pytest.mark.parametrize("model", ["o1lama", "o3pus", "gpt-50"])
def test_a_prefix_match_needs_a_boundary(model: str) -> None:
    """``o1`` must not swallow every model whose name starts with those letters."""
    assert is_openai_reasoning_model(model) is False


@pytest.mark.parametrize("model", ["gpt-5.1", "gpt-5.1-mini", "gpt-5.2"])
def test_dotted_gpt5_ids_are_reasoning_models(model: str) -> None:
    """OpenAI ships ``gpt-5.1`` with a dot, not a hyphen after the family."""
    assert is_openai_reasoning_model(model) is True


@pytest.mark.parametrize(
    "model",
    ["gpt-5-chat-latest", "gpt-5-chat", "gpt-5.1-chat-latest", "gpt-5.1-chat"],
)
def test_gpt5_chat_variants_are_not_reasoning_models(model: str) -> None:
    """ChatGPT-instant gpt-5 ids 400 on reasoning_effort, same as gpt-4o."""
    assert is_openai_reasoning_model(model) is False


def test_gpt5_chat_latest_does_not_resolve_as_reasoning_target() -> None:
    assert resolve_reasoning_target(
        client_provider="OpenAI",
        api_base="https://api.openai.com/v1",
        model_name="gpt-5-chat-latest",
    ) is None


def test_openai_o3_mini_resolves() -> None:
    assert resolve_reasoning_target(
        client_provider="OpenAI",
        api_base="https://api.openai.com/v1",
        model_name="o3-mini",
    ) == ("openai_reasoning", "o3-mini")


def test_openai_resolves_by_host_without_the_provider_name() -> None:
    assert resolve_reasoning_target(
        client_provider="",
        api_base="https://api.openai.com/v1",
        model_name="o3-mini",
    ) == ("openai_reasoning", "o3-mini")


def test_gpt4o_does_not_resolve_as_reasoning_target() -> None:
    assert resolve_reasoning_target(
        client_provider="OpenAI",
        api_base="https://api.openai.com/v1",
        model_name="gpt-4o",
    ) is None


def test_an_openai_compatible_third_party_host_does_not_resolve() -> None:
    """A local or reseller endpoint is not the OpenAI reasoning API."""
    assert resolve_reasoning_target(
        client_provider="OpenAI",
        api_base="https://my-gateway.internal/v1",
        model_name="o3-mini",
    ) is None


# ---------------------------------------------------------------- Anthropic


def test_anthropic_resolves_by_provider() -> None:
    assert resolve_reasoning_target(
        client_provider="Anthropic",
        api_base="https://api.anthropic.com",
        model_name="claude-sonnet-4-5",
    ) == ("anthropic", "claude-sonnet-4-5")


def test_anthropic_resolves_by_host_without_the_provider_name() -> None:
    assert resolve_reasoning_target(
        client_provider="",
        api_base="https://api.anthropic.com",
        model_name="claude-sonnet-4-5",
    ) == ("anthropic", "claude-sonnet-4-5")


def test_anthropic_needs_a_model_name() -> None:
    assert resolve_reasoning_target(
        client_provider="Anthropic",
        api_base="https://api.anthropic.com",
        model_name="",
    ) is None


@pytest.mark.parametrize(
    "model",
    [
        "claude-3-7-sonnet-20250219",
        "claude-opus-4-20250514",
        "claude-sonnet-4-5-20250929",
        "claude-haiku-4-5",
    ],
)
def test_models_with_extended_thinking_are_recognised(model: str) -> None:
    assert is_anthropic_thinking_model(model) is True


@pytest.mark.parametrize(
    "model",
    ["claude-3-5-sonnet-20241022", "claude-3-haiku-20240307", "claude-2.1", "", "  "],
)
def test_models_without_extended_thinking_are_refused(model: str) -> None:
    """Sending a thinking block to these is an error on every request.

    Before the allowlist they resolved, so a level set on Claude 3.5 turned a
    knob that did nothing into one that broke the conversation.
    """
    assert is_anthropic_thinking_model(model) is False


def test_a_model_without_extended_thinking_does_not_resolve() -> None:
    assert resolve_reasoning_target(
        client_provider="Anthropic",
        api_base="https://api.anthropic.com",
        model_name="claude-3-5-sonnet-20241022",
    ) is None


def test_the_thinking_allowlist_is_bounded_above_too() -> None:
    """A family newer than 4.x prefers adaptive thinking and may reject a budget.

    Getting no thinking is recoverable; every request failing is not, so an
    unrecognised newer model falls back to sending nothing.
    """
    assert is_anthropic_thinking_model("claude-opus-9") is False


@pytest.mark.parametrize(
    "model",
    [
        "claude-sonnet-4-6",
        "claude-sonnet-4-7",
        "claude-opus-4-7",
        "claude-haiku-4-6-20260401",
    ],
)
def test_claude_4_6_and_newer_do_not_get_a_budget(model: str) -> None:
    """``claude-sonnet-4`` must not swallow 4.6+ IDs that reject budget_tokens."""
    assert is_anthropic_thinking_model(model) is False
    assert resolve_reasoning_target(
        client_provider="Anthropic",
        api_base="https://api.anthropic.com",
        model_name=model,
    ) is None


def test_anthropic_is_not_gated_by_the_openai_sdk_provider_set() -> None:
    """Regression: the old provider allowlist would have refused Anthropic outright."""
    kind, _ = resolve_reasoning_target(
        client_provider="anthropic",
        api_base="",
        model_name="claude-opus-4-1",
    ) or (None, None)
    assert kind == "anthropic"


# ------------------------------------------------------------------- tables


def test_budget_table() -> None:
    assert ANTHROPIC_BUDGET_TOKENS["low"] == 1024
    assert ANTHROPIC_BUDGET_TOKENS["medium"] == 8000
    assert ANTHROPIC_BUDGET_TOKENS["high"] == 16000


def test_the_minimum_budget_is_the_anthropic_floor() -> None:
    """Anthropic rejects a budget below 1024."""
    assert min(ANTHROPIC_BUDGET_TOKENS.values()) >= 1024


def test_off_has_no_budget() -> None:
    """``off`` omits thinking entirely; a budget for it would be meaningless."""
    assert "off" not in ANTHROPIC_BUDGET_TOKENS


def test_prefixes_are_lowercase() -> None:
    assert all(p == p.lower() for p in OPENAI_REASONING_MODEL_PREFIXES)
    assert all(p == p.lower() for p in ANTHROPIC_THINKING_MODEL_PREFIXES)


# ----------------------------------------------------------- normalisation


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("OFF", "off"),
        ("disabled", "off"),
        ("enabled", "low"),
        ("Medium", "medium"),
        ("HIGH", "high"),
        ("nonsense", None),
        (None, None),
    ],
)
def test_level_normalisation_is_unchanged(raw, expected) -> None:
    assert normalize_reasoning_level(raw) == expected
