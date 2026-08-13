# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Cost is applied per usage event, against the model that served it."""

from __future__ import annotations

import pytest

from jiuwenswarm.server.runtime.agent_adapter import interface_deep
from jiuwenswarm.common.model_pricing import cost_status

_CFG = {
    "models": {
        "pricing": {
            # 1M in / 1M out costs exactly 1.0 and 10.0 -- round numbers so the
            # assertions read as arithmetic rather than as fixtures.
            "cheap-model": {"input": 1.0, "output": 10.0},
            "dear-model": {"input": 100.0, "output": 1000.0},
        }
    }
}


def _accumulator() -> dict:
    from jiuwenswarm.common.usage_cost import new_usage_accumulator

    return new_usage_accumulator()


@pytest.fixture(autouse=True)
def _config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jiuwenswarm.common.usage_cost.get_config", lambda: _CFG)


def _event(model: str, *, inp: int, out: int, cache: int = 0) -> dict:
    return {
        "model_name": model,
        "input_tokens": inp,
        "output_tokens": out,
        "cache_tokens": cache,
        "input_cost": 0.0,
        "output_cost": 0.0,
        "total_cost": 0.0,
    }


def test_a_turn_spanning_two_models_prices_each_against_its_own_rate() -> None:
    """The load-bearing test for pricing per event rather than per summary.

    A team drawing from a model pool can serve one turn with several models,
    and the accumulator keeps no model breakdown. Pricing the summed totals
    against a single model name would be wrong here by two orders of magnitude,
    so this is what pins the design.
    """
    acc = _accumulator()

    cheap = _event("cheap-model", inp=1_000_000, out=1_000_000)
    interface_deep._apply_usage_cost(cheap, acc)
    assert cheap["total_cost"] == pytest.approx(11.0)  # 1.0 in + 10.0 out

    dear = _event("dear-model", inp=1_000_000, out=1_000_000)
    interface_deep._apply_usage_cost(dear, acc)
    assert dear["total_cost"] == pytest.approx(1100.0)  # 100.0 in + 1000.0 out

    # Each event carries its own money; the caller sums them afterwards.
    assert cheap["total_cost"] + dear["total_cost"] == pytest.approx(1111.0)
    assert acc["priced_calls"] == 2
    assert acc["unpriced_calls"] == 0
    assert cost_status(acc["priced_calls"], acc["unpriced_calls"]) == "priced"


def test_an_unpriced_model_is_counted_not_silently_zeroed() -> None:
    acc = _accumulator()

    interface_deep._apply_usage_cost(_event("cheap-model", inp=1_000_000, out=0), acc)
    unknown = _event("self-hosted", inp=1_000_000, out=1_000_000)
    interface_deep._apply_usage_cost(unknown, acc)

    assert unknown["total_cost"] == 0.0
    assert acc["priced_calls"] == 1
    assert acc["unpriced_calls"] == 1
    # The case that actually occurs: a team mixing a hosted model with a
    # self-hosted one. Reporting only the priced half without saying so would
    # under-report the turn with no signal that it had.
    assert cost_status(acc["priced_calls"], acc["unpriced_calls"]) == "partial"


def test_a_provider_reported_cost_wins_over_the_local_table() -> None:
    """If the provider bills us, that is the truth; the table is a fallback."""
    acc = _accumulator()
    event = _event("cheap-model", inp=1_000_000, out=1_000_000)
    event["total_cost"] = 0.5

    interface_deep._apply_usage_cost(event, acc)

    assert event["total_cost"] == 0.5
    assert event["input_cost"] == 0.0
    assert acc["priced_calls"] == 1


def test_pricing_never_breaks_the_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Config failure must degrade to unpriced, not raise into the stream."""

    def _boom() -> dict:
        raise RuntimeError("config unavailable")

    monkeypatch.setattr("jiuwenswarm.common.usage_cost.get_config", _boom)
    acc = _accumulator()
    event = _event("cheap-model", inp=1_000, out=1_000)

    interface_deep._apply_usage_cost(event, acc)

    assert event["total_cost"] == 0.0
    assert acc["unpriced_calls"] == 1


def test_token_fields_are_untouched() -> None:
    """Control: pricing must not perturb the existing token accounting."""
    acc = _accumulator()
    event = _event("cheap-model", inp=123, out=45, cache=67)

    interface_deep._apply_usage_cost(event, acc)

    assert event["input_tokens"] == 123
    assert event["output_tokens"] == 45
    assert event["cache_tokens"] == 67
    assert acc["input_tokens"] == 0  # accumulation happens after, in the caller


def test_by_model_bucket_tracks_pricing_coverage_per_model() -> None:
    """Clients need one /usage line per serving model, not the adapter primary."""
    acc = _accumulator()

    interface_deep._apply_usage_cost(_event("cheap-model", inp=1_000_000, out=0), acc)
    interface_deep._apply_usage_cost(_event("self-hosted", inp=1_000_000, out=0), acc)

    by_model = acc["by_model"]
    assert by_model["cheap-model"]["priced_calls"] == 1
    assert by_model["cheap-model"]["unpriced_calls"] == 0
    assert by_model["self-hosted"]["priced_calls"] == 0
    assert by_model["self-hosted"]["unpriced_calls"] == 1


def test_accumulation_keeps_per_model_token_and_cost_totals() -> None:
    """Mirrors the llm_usage loop: price, then add tokens/cost into by_model."""
    acc = _accumulator()
    cheap = _event("cheap-model", inp=1_000_000, out=500_000)
    dear = _event("dear-model", inp=100_000, out=50_000)

    for event in (cheap, dear):
        interface_deep._apply_usage_cost(event, acc)
        bucket = interface_deep._model_usage_bucket(acc, event["model_name"])
        for token in ("input_tokens", "output_tokens", "total_tokens", "cache_tokens"):
            amount = event.get(token, 0) or 0
            acc[token] += amount
            bucket[token] += amount
        for cost in ("input_cost", "output_cost", "total_cost"):
            amount = event.get(cost, 0.0) or 0.0
            acc[cost] += amount
            bucket[cost] += amount

    assert acc["by_model"]["cheap-model"]["total_cost"] == pytest.approx(6.0)  # 1 + 5
    assert acc["by_model"]["dear-model"]["total_cost"] == pytest.approx(60.0)  # 10 + 50
    assert acc["total_cost"] == pytest.approx(66.0)


def test_last_verified_is_copied_onto_the_model_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = {
        "models": {
            "pricing": {
                "dated-model": {
                    "input": 1.0,
                    "output": 1.0,
                    "last_verified": "2026-08-05",
                }
            }
        }
    }
    monkeypatch.setattr("jiuwenswarm.common.usage_cost.get_config", lambda: cfg)
    acc = _accumulator()
    interface_deep._apply_usage_cost(_event("dated-model", inp=1_000, out=0), acc)
    assert acc["by_model"]["dated-model"]["last_verified"] == "2026-08-05"
