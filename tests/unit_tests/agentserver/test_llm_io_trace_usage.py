# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for the usage accumulation helpers in ``llm_io_trace``.

These tests cover the fix that ensures the ``llm_usage summary`` emitted at
``interface_deep.py`` aggregates token usage across the main agent and all
spawn/fork subagents. The accumulator lives in a ``ContextVar`` and is
mutated in-place at the ``Model.invoke`` / ``Model.stream`` patch boundary,
which is the only path every LLM call must pass through.
"""

from __future__ import annotations

import asyncio
import contextvars
from types import SimpleNamespace

import pytest

from jiuwenclaw.agentserver.llm_io_trace import (
    _LLM_USAGE_ACCUMULATOR,
    _coerce_usage_metadata,
    add_llm_usage,
    add_llm_usage_from_assistant,
    begin_usage_accumulation,
    reset_usage_accumulation,
)


# ---------------------------------------------------------------------------
# _coerce_usage_metadata
# ---------------------------------------------------------------------------


class TestCoerceUsageMetadata:
    """``_coerce_usage_metadata`` must accept dict / object / string forms."""

    def test_returns_empty_for_none(self) -> None:
        assert _coerce_usage_metadata(None) == {}

    def test_dict_input(self) -> None:
        usage = {
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
            "cache_tokens": 5,
            "input_cost": 0.001,
            "output_cost": 0.002,
            "total_cost": 0.003,
        }
        out = _coerce_usage_metadata(usage)
        assert out["input_tokens"] == 100
        assert out["output_tokens"] == 20
        assert out["total_tokens"] == 120
        assert out["cache_tokens"] == 5
        assert out["input_cost"] == pytest.approx(0.001)
        assert out["output_cost"] == pytest.approx(0.002)
        assert out["total_cost"] == pytest.approx(0.003)

    def test_object_input_pydantic_like(self) -> None:
        usage = SimpleNamespace(
            input_tokens=53585,
            output_tokens=5473,
            total_tokens=59058,
            cache_tokens=0,
            input_cost=0.0,
            output_cost=0.0,
            total_cost=0.0,
        )
        out = _coerce_usage_metadata(usage)
        assert out["input_tokens"] == 53585
        assert out["output_tokens"] == 5473
        assert out["total_tokens"] == 59058
        assert out["cache_tokens"] == 0

    def test_string_input_matches_log_format(self) -> None:
        # The exact format produced by the upstream serializer (seen in full.log).
        usage_str = (
            "code=0 err_msg='' prompt='' task_id='' model_name='glm-5.1' "
            "total_latency=0.0 first_token_time='******' request_start_time='' "
            "input_tokens=53585 output_tokens=5473 total_tokens=59058 "
            "cache_tokens=0 input_cost=0.0 output_cost=0.0 total_cost=0.0"
        )
        out = _coerce_usage_metadata(usage_str)
        assert out["input_tokens"] == 53585
        assert out["output_tokens"] == 5473
        assert out["total_tokens"] == 59058
        assert out["cache_tokens"] == 0
        assert out["input_cost"] == pytest.approx(0.0)

    def test_dict_with_none_values_is_skipped(self) -> None:
        usage = {"input_tokens": None, "output_tokens": 10, "total_tokens": 10}
        out = _coerce_usage_metadata(usage)
        assert "input_tokens" not in out
        assert out["output_tokens"] == 10
        assert out["total_tokens"] == 10

    def test_object_with_missing_fields(self) -> None:
        usage = SimpleNamespace(input_tokens=5)
        out = _coerce_usage_metadata(usage)
        assert out == {"input_tokens": 5}

    def test_unparseable_string_returns_empty(self) -> None:
        # No ``key=number`` pairs at all.
        assert _coerce_usage_metadata("nothing useful here") == {}


# ---------------------------------------------------------------------------
# begin_usage_accumulation / add_llm_usage / reset_usage_accumulation
# ---------------------------------------------------------------------------


class TestAccumulator:
    def test_begin_returns_zeroed_dict_and_token(self) -> None:
        acc, token = begin_usage_accumulation()
        try:
            assert acc["input_tokens"] == 0
            assert acc["output_tokens"] == 0
            assert acc["total_tokens"] == 0
            assert acc["cache_tokens"] == 0
            assert acc["input_cost"] == 0.0
            assert acc["output_cost"] == 0.0
            assert acc["total_cost"] == 0.0
            # ContextVar should now point to the same dict object.
            assert _LLM_USAGE_ACCUMULATOR.get() is acc
        finally:
            reset_usage_accumulation(token)

    def test_reset_restores_previous_value(self) -> None:
        assert _LLM_USAGE_ACCUMULATOR.get() is None
        _, token = begin_usage_accumulation()
        assert _LLM_USAGE_ACCUMULATOR.get() is not None
        reset_usage_accumulation(token)
        assert _LLM_USAGE_ACCUMULATOR.get() is None

    def test_reset_is_idempotent(self) -> None:
        _, token = begin_usage_accumulation()
        reset_usage_accumulation(token)
        # Second reset must not raise.
        reset_usage_accumulation(token)
        assert _LLM_USAGE_ACCUMULATOR.get() is None

    def test_add_llm_usage_no_active_accumulator_is_noop(self) -> None:
        # Without ``begin_usage_accumulation`` there is no active accumulator;
        # calls must silently be skipped.
        add_llm_usage({"input_tokens": 100, "output_tokens": 1, "total_tokens": 101})
        assert _LLM_USAGE_ACCUMULATOR.get() is None

    def test_add_llm_usage_accumulates_dict(self) -> None:
        acc, token = begin_usage_accumulation()
        try:
            add_llm_usage(
                {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}
            )
            add_llm_usage(
                {"input_tokens": 50, "output_tokens": 5, "total_tokens": 55,
                 "cache_tokens": 2}
            )
            assert acc["input_tokens"] == 150
            assert acc["output_tokens"] == 15
            assert acc["total_tokens"] == 165
            assert acc["cache_tokens"] == 2
        finally:
            reset_usage_accumulation(token)

    def test_add_llm_usage_accumulates_string(self) -> None:
        acc, token = begin_usage_accumulation()
        try:
            add_llm_usage(
                "input_tokens=53585 output_tokens=5473 total_tokens=59058 "
                "cache_tokens=0"
            )
            add_llm_usage(
                "input_tokens=31740 output_tokens=283 total_tokens=32023 "
                "cache_tokens=0"
            )
            assert acc["input_tokens"] == 85325
            assert acc["output_tokens"] == 5756
            assert acc["total_tokens"] == 91081
            assert acc["cache_tokens"] == 0
        finally:
            reset_usage_accumulation(token)

    def test_add_llm_usage_accumulates_costs(self) -> None:
        acc, token = begin_usage_accumulation()
        try:
            add_llm_usage({"input_cost": 0.001, "output_cost": 0.002, "total_cost": 0.003})
            add_llm_usage({"input_cost": 0.004, "output_cost": 0.005, "total_cost": 0.009})
            assert acc["input_cost"] == pytest.approx(0.005)
            assert acc["output_cost"] == pytest.approx(0.007)
            assert acc["total_cost"] == pytest.approx(0.012)
        finally:
            reset_usage_accumulation(token)

    def test_add_llm_usage_from_assistant_dict(self) -> None:
        acc, token = begin_usage_accumulation()
        try:
            assistant = {
                "role": "assistant",
                "content": "ok",
                "usage_metadata": {
                    "input_tokens": 7, "output_tokens": 1, "total_tokens": 8,
                },
            }
            add_llm_usage_from_assistant(assistant)
            assert acc["total_tokens"] == 8
        finally:
            reset_usage_accumulation(token)

    def test_add_llm_usage_from_assistant_object(self) -> None:
        acc, token = begin_usage_accumulation()
        try:
            assistant = SimpleNamespace(
                role="assistant",
                content="ok",
                usage_metadata=SimpleNamespace(
                    input_tokens=7, output_tokens=1, total_tokens=8, cache_tokens=0,
                ),
            )
            add_llm_usage_from_assistant(assistant)
            assert acc["input_tokens"] == 7
            assert acc["total_tokens"] == 8
        finally:
            reset_usage_accumulation(token)

    def test_add_llm_usage_from_assistant_no_metadata(self) -> None:
        acc, token = begin_usage_accumulation()
        try:
            add_llm_usage_from_assistant({"role": "assistant", "content": "ok"})
            add_llm_usage_from_assistant(None)
            assert acc["total_tokens"] == 0
        finally:
            reset_usage_accumulation(token)

    def test_add_llm_usage_unknown_payload_is_safe(self) -> None:
        acc, token = begin_usage_accumulation()
        try:
            add_llm_usage("nothing parsable")
            add_llm_usage({})
            add_llm_usage({"unrelated": 5})
            assert acc["total_tokens"] == 0
            assert acc["input_tokens"] == 0
        finally:
            reset_usage_accumulation(token)


# ---------------------------------------------------------------------------
# Subagent-style aggregation: the whole point of the fix.
# ---------------------------------------------------------------------------


class TestSubagentAggregation:
    """Simulate the runtime: a parent scope with an accumulator, then a
    nested ``contextvars.copy_context()`` (which is what ``Runner.run_agent``
    effectively does for spawn/fork subagents). The child copies inherit the
    same dict reference, so their ``add_llm_usage`` calls update the same
    accumulator that the parent will read at the end."""

    def test_subagent_calls_aggregate_into_parent(self) -> None:
        acc, token = begin_usage_accumulation()
        try:
            # Main agent's own LLM call.
            add_llm_usage({"input_tokens": 100, "output_tokens": 10, "total_tokens": 110})

            # Simulated subagent call running in a copied context.
            child_ctx = contextvars.copy_context()
            child_ctx.run(
                add_llm_usage,
                {"input_tokens": 200, "output_tokens": 20, "total_tokens": 220},
            )
            child_ctx.run(
                add_llm_usage,
                {"input_tokens": 50, "output_tokens": 5, "total_tokens": 55},
            )

            # All three calls land in the same accumulator dict.
            assert acc["input_tokens"] == 350
            assert acc["output_tokens"] == 35
            assert acc["total_tokens"] == 385
        finally:
            reset_usage_accumulation(token)

    def test_subagent_async_runner_aggregates(self) -> None:
        """Mimic ``Runner.run_agent`` invoked from an asyncio context."""

        async def _subagent_run(usage: dict) -> None:
            # Subagent does some asynchronous work and emits usage from inside.
            await asyncio.sleep(0)
            add_llm_usage(usage)

        async def _scenario() -> dict:
            acc, token = begin_usage_accumulation()
            try:
                add_llm_usage({"input_tokens": 1000, "output_tokens": 0, "total_tokens": 1000})
                await asyncio.gather(
                    _subagent_run({"input_tokens": 200, "output_tokens": 20, "total_tokens": 220}),
                    _subagent_run({"input_tokens": 300, "output_tokens": 30, "total_tokens": 330}),
                )
                return dict(acc)
            finally:
                reset_usage_accumulation(token)

        result = asyncio.run(_scenario())
        assert result["input_tokens"] == 1500
        assert result["output_tokens"] == 50
        assert result["total_tokens"] == 1550

    def test_isolated_request_scopes_do_not_leak(self) -> None:
        """Two sequential request scopes must not share state."""
        acc1, token1 = begin_usage_accumulation()
        add_llm_usage({"input_tokens": 100, "output_tokens": 1, "total_tokens": 101})
        reset_usage_accumulation(token1)
        assert acc1["total_tokens"] == 101

        acc2, token2 = begin_usage_accumulation()
        try:
            assert acc2["total_tokens"] == 0
            add_llm_usage({"input_tokens": 50, "output_tokens": 1, "total_tokens": 51})
            assert acc2["total_tokens"] == 51
            # First accumulator dict is unchanged by second scope.
            assert acc1["total_tokens"] == 101
        finally:
            reset_usage_accumulation(token2)

    def test_concurrent_requests_in_separate_contexts(self) -> None:
        """Two concurrently running requests (each in their own copied context)
        must have isolated accumulators."""

        async def _request(per_call_input: int, n_calls: int) -> dict:
            acc, token = begin_usage_accumulation()
            try:
                for _ in range(n_calls):
                    await asyncio.sleep(0)
                    add_llm_usage(
                        {
                            "input_tokens": per_call_input,
                            "output_tokens": 1,
                            "total_tokens": per_call_input + 1,
                        }
                    )
                return dict(acc)
            finally:
                reset_usage_accumulation(token)

        async def _scenario() -> tuple[dict, dict]:
            return await asyncio.gather(_request(10, 3), _request(100, 5))

        first, second = asyncio.run(_scenario())
        assert first["input_tokens"] == 30
        assert first["total_tokens"] == 33
        assert second["input_tokens"] == 500
        assert second["total_tokens"] == 505
