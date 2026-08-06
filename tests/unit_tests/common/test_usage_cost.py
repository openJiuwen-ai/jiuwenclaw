# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared usage accumulator — model / member / agent dimensions."""

from __future__ import annotations

import pytest

from jiuwenswarm.common.usage_cost import (
    apply_usage_cost,
    build_usage_summary,
    new_usage_accumulator,
    record_usage_event,
)

_CFG = {
    "models": {
        "pricing": {
            "cheap-model": {"input": 1.0, "output": 10.0, "last_verified": "2026-08-05"},
            "dear-model": {"input": 100.0, "output": 1000.0},
        }
    }
}


def _meta(model: str, *, inp: int, out: int) -> dict:
    return {
        "model_name": model,
        "input_tokens": inp,
        "output_tokens": out,
        "total_tokens": inp + out,
        "cache_tokens": 0,
        "input_cost": 0.0,
        "output_cost": 0.0,
        "total_cost": 0.0,
    }


def test_by_member_partitions_a_team_turn() -> None:
    acc = new_usage_accumulator()
    record_usage_event(
        _meta("cheap-model", inp=1_000_000, out=0),
        acc,
        member="leader",
        config=_CFG,
    )
    record_usage_event(
        _meta("dear-model", inp=100_000, out=0),
        acc,
        member="reviewer",
        config=_CFG,
    )
    record_usage_event(
        _meta("self-hosted", inp=50_000, out=0),
        acc,
        member="reviewer",
        config=_CFG,
    )

    summary = build_usage_summary(acc)
    assert summary["cost_status"] == "partial"
    by_member = {e["member"]: e for e in summary["by_member"]}
    assert by_member["leader"]["total_cost"] == pytest.approx(1.0)
    assert by_member["leader"]["cost_status"] == "priced"
    assert by_member["reviewer"]["cost_status"] == "partial"
    assert by_member["reviewer"]["total_cost"] == pytest.approx(10.0)  # dear only
    models_with_cost = {
        e["model"] for e in summary["by_model"] if (e.get("total_cost") or 0) > 0
    }
    assert models_with_cost == {"cheap-model", "dear-model"}


def test_by_agent_attributes_subagent_calls() -> None:
    acc = new_usage_accumulator()
    record_usage_event(
        _meta("cheap-model", inp=1_000_000, out=500_000),
        acc,
        agent="research_agent",
        config=_CFG,
    )
    summary = build_usage_summary(acc)
    assert summary["by_agent"][0]["agent"] == "research_agent"
    assert summary["by_agent"][0]["total_cost"] == pytest.approx(6.0)
    assert summary["by_agent"][0]["cost_status"] == "priced"
    assert summary["by_model"][0]["last_verified"] == "2026-08-05"


def test_summary_omits_empty_dimensions() -> None:
    acc = new_usage_accumulator()
    record_usage_event(_meta("cheap-model", inp=1_000, out=0), acc, config=_CFG)
    summary = build_usage_summary(acc)
    assert "by_member" not in summary
    assert "by_agent" not in summary
    assert "by_model" in summary


# --- pricing cascade: provider → local table → unpriced -----------------


def test_openrouter_provider_cost_prices_without_local_table() -> None:
    """OpenRouter forwards usage.cost; no models.pricing entry is required."""
    acc = new_usage_accumulator()
    meta = _meta("nvidia/nemotron-3-ultra-550b-a55b:free", inp=194, out=2)
    meta["total_cost"] = 0.0015  # as openai_model_client would set from usage.cost

    record_usage_event(meta, acc, config={"models": {"pricing": {}}})

    summary = build_usage_summary(acc)
    assert summary["cost_status"] == "priced"
    assert summary["total_cost"] == pytest.approx(0.0015)
    assert acc["priced_calls"] == 1
    assert acc["unpriced_calls"] == 0


def test_deepseek_style_uses_local_pricing_when_provider_sends_no_cost() -> None:
    acc = new_usage_accumulator()
    meta = _meta("cheap-model", inp=1_000_000, out=0)
    # No provider cost — DeepSeek shape.
    apply_usage_cost(meta, acc, config=_CFG)

    assert meta["total_cost"] == pytest.approx(1.0)
    assert acc["priced_calls"] == 1


def test_local_model_without_rate_stays_unpriced() -> None:
    """Gemma / self-host with neither provider cost nor table entry."""
    acc = new_usage_accumulator()
    meta = _meta("Gemma4-26B", inp=50_000, out=1_000)

    record_usage_event(meta, acc, config={"models": {"pricing": {}}})

    summary = build_usage_summary(acc)
    assert summary["cost_status"] == "unpriced"
    assert "total_cost" not in summary
    assert acc["unpriced_calls"] == 1


def test_summary_total_matches_rounded_input_plus_output() -> None:
    """Independent rounding of in/out/total must not invent a mismatch."""
    acc = new_usage_accumulator()
    acc["priced_calls"] = 1
    # Values that round differently per-field at 6dp vs their sum.
    acc["input_cost"] = 0.0012345
    acc["output_cost"] = 0.0023456
    acc["total_cost"] = 0.0012345 + 0.0023456

    summary = build_usage_summary(acc)

    assert summary["input_cost"] + summary["output_cost"] == pytest.approx(
        summary["total_cost"]
    )


def test_summary_omits_split_when_provider_total_disagrees() -> None:
    """OpenRouter: billed total wins; do not show a conflicting in/out split."""
    acc = new_usage_accumulator()
    acc["priced_calls"] = 1
    acc["input_cost"] = 0.001
    acc["output_cost"] = 0.002
    acc["total_cost"] = 0.0015  # billed ≠ 0.003

    summary = build_usage_summary(acc)

    assert summary["total_cost"] == pytest.approx(0.0015)
    assert "input_cost" not in summary
    assert "output_cost" not in summary


def test_openrouter_total_only_has_no_zero_split_fields() -> None:
    acc = new_usage_accumulator()
    meta = _meta("openrouter/model", inp=100, out=10)
    meta["total_cost"] = 0.0038
    record_usage_event(meta, acc, config={"models": {"pricing": {}}})

    summary = build_usage_summary(acc)

    assert summary["total_cost"] == pytest.approx(0.0038)
    assert "input_cost" not in summary
    assert "output_cost" not in summary
