# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for ``mcp_config.prewarm_connected_mcps``.

The startup fire-and-forget task and the web root adapter's lazy
``_start_mcp_prewarm`` both delegate here. ``prewarm_connected_mcps`` iterates
``state.json``'s ``state==connected`` records and probes each via
``probe_mcp_live_connection`` (seeds the process-global ``Runner.resource_mgr``
cache). These tests pin the contract:

* empty connected set → no probe called (early return).
* non-empty → each name probed exactly once, in order.
* one MCP raising does not abort the rest (failure isolation).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from jiuwenswarm.common.mcp_config import prewarm_connected_mcps


def _connected(names: list[str]) -> list[dict]:
    return [{"name": n} for n in names]


@pytest.mark.asyncio
async def test_prewarm_empty_connected_set_skips_probe() -> None:
    """No connected MCPs → ``probe_mcp_live_connection`` is never called."""
    with patch(
        "jiuwenswarm.common.mcp_config.probe_mcp_live_connection",
        new=AsyncMock(),
    ) as mock_probe, patch(
        "jiuwenswarm.server.runtime.mcp.state_store.list_truly_connected_mcps",
        return_value=[],
    ):
        await prewarm_connected_mcps()
    mock_probe.assert_not_awaited()


@pytest.mark.asyncio
async def test_prewarm_probes_each_connected_mcp_once() -> None:
    """Two connected MCPs → each probed exactly once, in listed order."""
    with patch(
        "jiuwenswarm.common.mcp_config.probe_mcp_live_connection",
        new=AsyncMock(return_value=(True, "")),
    ) as mock_probe, patch(
        "jiuwenswarm.server.runtime.mcp.state_store.list_truly_connected_mcps",
        return_value=_connected(["github", "context7"]),
    ):
        await prewarm_connected_mcps()
    assert mock_probe.await_count == 2
    awaited_names = [call.args[0] for call in mock_probe.await_args_list]
    assert awaited_names == ["github", "context7"]


@pytest.mark.asyncio
async def test_prewarm_isolates_per_mcp_failures() -> None:
    """A probe raising on one MCP does not abort the rest. State is never
    downgraded here — the first chat reconciles and retries."""
    probe = AsyncMock(side_effect=[RuntimeError("boom"), (True, "")])
    with patch(
        "jiuwenswarm.common.mcp_config.probe_mcp_live_connection",
        new=probe,
    ), patch(
        "jiuwenswarm.server.runtime.mcp.state_store.list_truly_connected_mcps",
        return_value=_connected(["bad", "good"]),
    ):
        await prewarm_connected_mcps()  # must not raise
    assert probe.await_count == 2
    awaited_names = [call.args[0] for call in probe.await_args_list]
    assert awaited_names == ["bad", "good"]
