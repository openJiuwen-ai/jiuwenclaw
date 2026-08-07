"""wait_all timeout should not count HITL pause time."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_patch_module():
    path = Path(__file__).resolve().parents[2] / "jiuwenclaw" / "jiuwen_core_patch.py"
    spec = importlib.util.spec_from_file_location("jiuwen_core_patch_under_test", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # Avoid executing top-level openjiuwen imports if missing: load by exec of
    # only the pause-clock helpers via importing the real package when available.
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:  # noqa: BLE001 — fallback for stripped test envs
        pytest.skip(f"jiuwen_core_patch import failed: {exc}")
    return mod


@pytest.mark.asyncio
async def test_wait_timeout_clock_pause_extends_deadline():
    mod = _load_patch_module()
    clock = mod._WaitTimeoutClock()
    assert clock.pause_depth == 0
    clock.pause()
    assert clock.pause_depth == 1
    await asyncio.sleep(0.05)
    clock.resume()
    assert clock.pause_depth == 0
    assert clock.paused_total >= 0.04


@pytest.mark.asyncio
async def test_wait_all_excludes_paused_hitl_time(monkeypatch):
    mod = _load_patch_module()

    class _FakeExecutor:
        def __init__(self):
            self.cancelled = False

        def cancel_all(self):
            self.cancelled = True

    async def _slow_wait_all(executor):
        # Simulate tool body: pause during HITL, then finish after total ~0.25s wall.
        clock = mod._executor_wait_clock(executor)
        await asyncio.sleep(0.05)
        clock.pause()
        await asyncio.sleep(0.20)
        clock.resume()
        await asyncio.sleep(0.05)
        return [(SimpleNamespace(name="tool"), "ok")]

    executor = _FakeExecutor()
    # Active budget 0.15s; wall ~0.30s but paused 0.20s => should succeed.
    results = await mod._wait_all_with_pauseable_timeout(executor, _slow_wait_all, 0.15)
    assert len(results) == 1
    assert getattr(results[0][0], "name", None) == "tool"
    assert results[0][1] == "ok"
    assert not executor.cancelled


@pytest.mark.asyncio
async def test_wait_all_still_times_out_without_enough_budget():
    mod = _load_patch_module()

    class _FakeExecutor:
        def __init__(self):
            self.cancelled = False
            self._done = asyncio.Event()

        def cancel_all(self):
            self.cancelled = True
            self._done.set()

    async def _hang_until_cancelled(executor):
        await executor._done.wait()
        # Match production shape: tool_call is an object with ``name``.
        return [(SimpleNamespace(name="tool"), asyncio.CancelledError())]

    executor = _FakeExecutor()
    results = await mod._wait_all_with_pauseable_timeout(executor, _hang_until_cancelled, 0.05)
    assert executor.cancelled
    assert isinstance(results[0][1], TimeoutError)
    assert "Tool timed out after 0.05s: tool" in str(results[0][1])
