# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Tests for context.usage emission (limit coercion and used-token semantics)."""

# pylint: disable=protected-access

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenclaw.agentserver.react_agent import (
    JiuClawReActAgent,
    _coerce_context_window_limit_tokens,
    _read_nonneg_usage_int,
)


def test_coerce_limit_accepts_numeric_forms() -> None:
    assert _coerce_context_window_limit_tokens(128000) == 128000
    assert _coerce_context_window_limit_tokens("128000") == 128000
    assert _coerce_context_window_limit_tokens(128000.0) == 128000


def test_coerce_limit_invalid_falls_back() -> None:
    assert _coerce_context_window_limit_tokens("nope") == 128000
    assert _coerce_context_window_limit_tokens(True) == 128000
    assert _coerce_context_window_limit_tokens(0) == 128000
    assert _coerce_context_window_limit_tokens(-1) == 128000


def test_read_usage_dict_and_aliases() -> None:
    assert _read_nonneg_usage_int({"prompt_tokens": 3, "input_tokens": 1}, "input_tokens", "prompt_tokens") == 1
    assert _read_nonneg_usage_int({"prompt_tokens": 3}, "input_tokens", "prompt_tokens") == 3


@pytest.mark.asyncio
async def test_emit_context_usage_uses_input_plus_output() -> None:
    session = AsyncMock()
    msg = SimpleNamespace(
        usage_metadata=SimpleNamespace(
            input_tokens=1000,
            output_tokens=9000,
            total_tokens=None,
            cache_tokens=None,
        )
    )
    with patch("jiuwenclaw.agentserver.react_agent.get_config") as gc:
        gc.return_value = {"react": {"context_window_limit_tokens": 20000}}
        await JiuClawReActAgent._emit_context_usage(MagicMock(), session, msg)
    event = session.write_stream.await_args.args[0]
    assert event.type == "context.usage"
    assert event.payload["used_tokens"] == 10000
    assert event.payload["usage_percent"] == 50.0


@pytest.mark.asyncio
async def test_emit_context_usage_max_with_total() -> None:
    session = AsyncMock()
    msg = SimpleNamespace(
        usage_metadata=SimpleNamespace(
            input_tokens=100,
            output_tokens=100,
            total_tokens=5000,
            cache_tokens=None,
        )
    )
    with patch("jiuwenclaw.agentserver.react_agent.get_config") as gc:
        gc.return_value = {"react": {"context_window_limit_tokens": 10000}}
        await JiuClawReActAgent._emit_context_usage(MagicMock(), session, msg)
    event = session.write_stream.await_args.args[0]
    assert event.payload["used_tokens"] == 5000
    assert event.payload["usage_percent"] == 50.0


@pytest.mark.asyncio
async def test_emit_context_usage_string_limit_coerced() -> None:
    session = AsyncMock()
    msg = SimpleNamespace(
        usage_metadata=SimpleNamespace(
            input_tokens=64000,
            output_tokens=0,
            total_tokens=None,
            cache_tokens=None,
        )
    )
    with patch("jiuwenclaw.agentserver.react_agent.get_config") as gc:
        gc.return_value = {"react": {"context_window_limit_tokens": "128000"}}
        await JiuClawReActAgent._emit_context_usage(MagicMock(), session, msg)
    event = session.write_stream.await_args.args[0]
    assert event.payload["limit_tokens"] == 128000
    assert event.payload["usage_percent"] == 50.0


@pytest.mark.asyncio
async def test_emit_context_usage_no_metadata_percent_none() -> None:
    session = AsyncMock()
    msg = SimpleNamespace(usage_metadata=None)
    with patch("jiuwenclaw.agentserver.react_agent.get_config") as gc:
        gc.return_value = {"react": {"context_window_limit_tokens": 1000}}
        await JiuClawReActAgent._emit_context_usage(MagicMock(), session, msg)
    event = session.write_stream.await_args.args[0]
    assert event.payload["used_tokens"] is None
    assert event.payload["usage_percent"] is None
