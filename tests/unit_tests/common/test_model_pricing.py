# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for per-model token pricing."""

from __future__ import annotations

import pytest

from jiuwenswarm.common.model_pricing import (
    DEFAULT_PRICES,
    ModelPrice,
    cost_status,
    price_usage,
    resolve_price,
)

_CFG = {
    "models": {
        "pricing": {
            "hosted-model": {"input": 1.0, "output": 2.0, "cache_read": 0.1},
            "no-cache-rate": {"input": 4.0, "output": 8.0},
            # Anthropic shape: cache reads are cheap, cache writes cost more
            # than ordinary input.
            "cached-model": {
                "input": 1.0,
                "output": 2.0,
                "cache_read": 0.1,
                "cache_write": 1.25,
            },
            "broken": {"input": "not-a-number", "output": 2.0},
            "negative": {"input": -1.0, "output": 2.0},
        }
    }
}


def test_no_prices_are_bundled() -> None:
    """Shipping rates would go stale silently and report wrong money."""
    assert DEFAULT_PRICES == {}


def test_config_declares_the_rate() -> None:
    price = resolve_price("hosted-model", _CFG)
    assert price is not None
    assert price.input == 1.0
    assert price.output == 2.0
    assert price.cache_read == 0.1
    assert price.currency == "USD"


def test_unknown_model_has_no_price() -> None:
    assert resolve_price("never-configured", _CFG) is None
    assert resolve_price("", _CFG) is None
    assert resolve_price(None, _CFG) is None
    assert resolve_price("hosted-model", None) is None


def test_malformed_entries_are_ignored_not_raised() -> None:
    """One bad entry must not break the turn or the other models' rates."""
    assert resolve_price("broken", _CFG) is None
    assert resolve_price("negative", _CFG) is None
    assert resolve_price("hosted-model", _CFG) is not None


def test_cache_tokens_are_a_subset_of_input_not_an_extra() -> None:
    """Pricing cached tokens on top of the input count would double-bill them."""
    price = resolve_price("hosted-model", _CFG)

    # 1M input of which 900k cached: 100k at 1.0 + 900k at 0.1 = 0.1 + 0.09
    cached = price_usage(price, input_tokens=1_000_000, output_tokens=0, cache_tokens=900_000)
    assert cached.input_cost == pytest.approx(0.19)

    # Same input, nothing cached: 1M at 1.0
    uncached = price_usage(price, input_tokens=1_000_000, output_tokens=0, cache_tokens=0)
    assert uncached.input_cost == pytest.approx(1.0)

    # The discount must be visible, otherwise the cache rate is being ignored.
    assert cached.input_cost < uncached.input_cost


def test_cache_rate_defaults_to_the_input_rate() -> None:
    """Absent a cache rate, overstate rather than understate."""
    price = resolve_price("no-cache-rate", _CFG)
    assert price is not None
    assert price.effective_cache_read == 4.0

    usage = price_usage(price, input_tokens=1_000_000, output_tokens=0, cache_tokens=500_000)
    assert usage.input_cost == pytest.approx(4.0)


def test_output_is_priced_at_the_output_rate() -> None:
    price = resolve_price("hosted-model", _CFG)
    usage = price_usage(price, input_tokens=0, output_tokens=500_000, cache_tokens=0)
    assert usage.output_cost == pytest.approx(1.0)
    assert usage.total_cost == pytest.approx(usage.input_cost + usage.output_cost)


def test_unpriced_is_not_free() -> None:
    """A zero that means 'unknown' must be distinguishable from one that means 'free'."""
    usage = price_usage(None, input_tokens=1_000_000, output_tokens=1_000_000)
    assert usage.priced is False
    assert usage.total_cost == 0.0


def test_cache_tokens_cannot_exceed_input() -> None:
    """Defensive: a provider over-reporting cache must not make cost negative."""
    price = resolve_price("hosted-model", _CFG)
    usage = price_usage(price, input_tokens=100, output_tokens=0, cache_tokens=10_000)
    assert usage.input_cost >= 0.0


def test_cache_write_is_charged_at_a_premium_not_a_discount() -> None:
    """Anthropic bills cache creation above the input rate, unlike cache reads."""
    price = resolve_price("cached-model", _CFG)
    assert price is not None
    assert price.effective_cache_write == 1.25
    assert price.effective_cache_read == 0.1

    # 1M prompt, all of it written into cache: 1M at 1.25
    written = price_usage(
        price, input_tokens=1_000_000, output_tokens=0, cache_write_tokens=1_000_000
    )
    assert written.input_cost == pytest.approx(1.25)

    # Same prompt, all of it read from cache: 1M at 0.1
    read = price_usage(
        price, input_tokens=1_000_000, output_tokens=0, cache_tokens=1_000_000
    )
    assert read.input_cost == pytest.approx(0.1)

    # A write must cost more than plain input, and a read must cost less.
    plain = price_usage(price, input_tokens=1_000_000, output_tokens=0)
    assert read.input_cost < plain.input_cost < written.input_cost


def test_the_three_prompt_rates_partition_the_input_total() -> None:
    """input_tokens = uncached + cache_read + cache_write, priced once each."""
    price = resolve_price("cached-model", _CFG)
    usage = price_usage(
        price,
        input_tokens=1_000_000,
        output_tokens=0,
        cache_tokens=500_000,
        cache_write_tokens=300_000,
    )
    # 200k uncached @1.0 + 500k read @0.1 + 300k write @1.25
    expected = (200_000 * 1.0 + 500_000 * 0.1 + 300_000 * 1.25) / 1_000_000
    assert usage.input_cost == pytest.approx(expected)


def test_cache_write_defaults_to_the_input_rate_and_understates() -> None:
    """No safe default exists; the floor is documented rather than guessed."""
    price = resolve_price("no-cache-rate", _CFG)
    assert price is not None
    assert price.effective_cache_write == 4.0


def test_over_reported_cache_counts_cannot_produce_a_credit() -> None:
    """Defensive: the two counts together must not exceed the prompt total."""
    price = resolve_price("cached-model", _CFG)
    usage = price_usage(
        price,
        input_tokens=1_000,
        output_tokens=0,
        cache_tokens=900,
        cache_write_tokens=900,
    )
    assert usage.input_cost >= 0.0
    assert usage.total_cost >= 0.0


def test_cost_status_distinguishes_partial_from_the_others() -> None:
    """partial is the real case: a team mixing a priced and an unpriced model."""
    assert cost_status(3, 0) == "priced"
    assert cost_status(2, 1) == "partial"
    assert cost_status(0, 4) == "unpriced"
    assert cost_status(0, 0) == "unpriced"


def test_price_is_immutable() -> None:
    price = ModelPrice(input=1.0, output=2.0)
    with pytest.raises(Exception):
        price.input = 5.0  # type: ignore[misc]
