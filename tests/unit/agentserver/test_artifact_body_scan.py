from __future__ import annotations

import asyncio
import sys
import threading
import time
import types
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from jiuwenclaw.agentserver.deep_agent.artifact_body_scan import (
    BODY_SCAN_MAX_LINE_LEN,
    scan_body_text_for_paths,
)
from jiuwenclaw.agentserver.deep_agent.artifact_emitter import (
    ArtifactEmitContext,
    emit_artifact_generated,
)

_RAILS_PACKAGE = "jiuwenclaw.agentserver.deep_agent.rails"
_TASK_EXECUTION_RAIL_MODULE = f"{_RAILS_PACKAGE}.task_execution_rail"
_BODY_SCAN_TIMEOUT_S = 2.0

_RAIL_EXTRACT_HOOK: Callable[..., list[dict[str, Any]]] | None = None


def _rail_build_artifacts_from_explicit_paths(
    *_args: Any,
    **_kwargs: Any,
) -> list[dict[str, Any]]:
    return []


def _rail_is_recently_sent(_path: str) -> bool:
    return False


def _rail_mark_as_sent(_path: str) -> None:
    return None


def _rail_extract_artifact_paths_from_tool_result(
    *_args: Any,
    **_kwargs: Any,
) -> list[dict[str, Any]]:
    if _RAIL_EXTRACT_HOOK is None:
        return []
    return _RAIL_EXTRACT_HOOK(*_args, **_kwargs)


def _set_rail_extract_hook(
    fn: Callable[..., list[dict[str, Any]]],
) -> None:
    global _RAIL_EXTRACT_HOOK
    _RAIL_EXTRACT_HOOK = fn


def _ensure_task_execution_rail_module() -> None:
    """Avoid importing rails/__init__.py when openjiuwen extras are absent."""
    sys.modules.setdefault(_RAILS_PACKAGE, types.ModuleType(_RAILS_PACKAGE))
    if _TASK_EXECUTION_RAIL_MODULE in sys.modules:
        return
    rail_mod = types.ModuleType(_TASK_EXECUTION_RAIL_MODULE)
    rail_mod.__dict__.update(
        {
            "_extract_artifact_paths_from_tool_result": (
                _rail_extract_artifact_paths_from_tool_result
            ),
            "_build_artifacts_from_explicit_paths": (
                _rail_build_artifacts_from_explicit_paths
            ),
            "_is_recently_sent": _rail_is_recently_sent,
            "_mark_as_sent": _rail_mark_as_sent,
        }
    )
    sys.modules[_TASK_EXECUTION_RAIL_MODULE] = rail_mod


def _session_id_sess_hang() -> str:
    return "sess-hang"


def _session_id_sess_timeout() -> str:
    return "sess-timeout"


def _build_path_dump_text(total_bytes: int) -> str:
    """Simulate code stdout with many Windows paths (08:36 incident shape)."""
    line = "F:\\OfficeClaw_Project\\to\\高企需要提供的相关准备资料\\file_{:04d}.xlsx\n"
    chunks: list[str] = []
    size = 0
    index = 0
    while size < total_bytes:
        chunk = line.format(index)
        chunks.append(chunk)
        size += len(chunk)
        index += 1
    return "".join(chunks)


@pytest.mark.asyncio
async def test_1mb_body_scan_completes_without_blocking_event_loop() -> None:
    """Regression: 1MB path-heavy stdout must finish within a few seconds."""
    text = _build_path_dump_text(1_000_000)
    ticks: list[float] = []

    async def heartbeat() -> None:
        for _ in range(20):
            await asyncio.sleep(0.05)
            ticks.append(time.monotonic())

    start = time.monotonic()
    _, scan_result = await asyncio.gather(
        heartbeat(),
        asyncio.to_thread(scan_body_text_for_paths, text),
    )
    elapsed = time.monotonic() - start

    candidates, matches, scanned, skipped = scan_result
    assert elapsed < 15.0
    assert len(ticks) >= 10
    assert scanned + skipped > 0
    assert isinstance(candidates, list)
    assert isinstance(matches, int)


@pytest.mark.asyncio
async def test_emit_artifact_generated_1mb_completes_within_timeout() -> None:
    def scan_only_extract(
        tool_result,
        workspace_base=None,
        tool_start_time=None,
        *,
        scan_body_text=False,
        skip_mtime_check=False,
        cancel_event=None,
    ):
        del workspace_base, tool_start_time, skip_mtime_check, cancel_event
        if not scan_body_text:
            return []
        text = tool_result if isinstance(tool_result, str) else str(tool_result)
        scan_body_text_for_paths(text)
        return []

    _set_rail_extract_hook(scan_only_extract)
    _ensure_task_execution_rail_module()

    text = _build_path_dump_text(1_000_000)
    session = SimpleNamespace(
        get_session_id=_session_id_sess_hang,
        write_stream=AsyncMock(),
    )
    ctx = ArtifactEmitContext(
        session=session,
        tool_result=text,
        tool_name="code",
        workspace_base=None,
        tool_start_time=None,
        task_id="task-hang",
    )

    start = time.monotonic()
    emitted = await asyncio.wait_for(emit_artifact_generated(ctx), timeout=15.0)
    elapsed = time.monotonic() - start

    assert elapsed < 15.0
    assert emitted is False
    session.write_stream.assert_not_awaited()


@pytest.mark.asyncio
async def test_emit_artifact_generated_timeout_returns_empty() -> None:
    def slow_extract(*_args, **_kwargs):
        time.sleep(_BODY_SCAN_TIMEOUT_S + 2.0)
        return [{"path": "/tmp/slow.txt", "exists": True}]

    _set_rail_extract_hook(slow_extract)
    _ensure_task_execution_rail_module()

    extract_ctx = ArtifactEmitContext(
        session=SimpleNamespace(get_session_id=_session_id_sess_timeout),
        tool_result="slow",
        tool_name="code",
        workspace_base=None,
        tool_start_time=None,
        task_id="task-timeout",
        log_prefix="[Test]",
    )

    start = time.monotonic()
    emitted = await emit_artifact_generated(extract_ctx)
    elapsed = time.monotonic() - start

    assert emitted is False
    assert elapsed < _BODY_SCAN_TIMEOUT_S + 0.5


def test_scan_body_text_exits_early_when_cancelled() -> None:
    text = _build_path_dump_text(1_000_000)
    cancel_event = threading.Event()
    cancel_event.set()

    start = time.monotonic()
    _, _, scanned, _ = scan_body_text_for_paths(text, cancel_event=cancel_event)
    elapsed = time.monotonic() - start

    assert elapsed < 0.5
    assert scanned == 0


@pytest.mark.asyncio
async def test_body_scan_stops_quickly_when_cancel_event_set_mid_scan() -> None:
    text = _build_path_dump_text(1_000_000)
    cancel_event = threading.Event()
    total_lines = len(text.splitlines())

    async def cancel_after_delay() -> None:
        await asyncio.sleep(0.05)
        cancel_event.set()

    start = time.monotonic()
    _, scan_result = await asyncio.gather(
        cancel_after_delay(),
        asyncio.to_thread(
            scan_body_text_for_paths,
            text,
            cancel_event=cancel_event,
        ),
    )
    elapsed = time.monotonic() - start
    _, _, scanned, _ = scan_result

    assert scanned < total_lines
    assert elapsed < 2.0


def test_scan_body_text_skips_oversized_line() -> None:
    normal_line = "{workspace}/output/report.xlsx\n"
    oversized = "F:" + ("\\OfficeClaw_Project" * 600) + ".xlsx\n"
    assert len(oversized) > BODY_SCAN_MAX_LINE_LEN

    candidates, matches, scanned, skipped = scan_body_text_for_paths(
        normal_line + oversized
    )

    assert scanned == 1
    assert skipped == 1
    assert matches >= 1
    assert any("report.xlsx" in path for path in candidates)


def test_scan_body_text_does_not_scan_full_text_in_one_findall() -> None:
    """Line-based scan should skip a single 10KB path dump line."""
    oversized_only = "x" * (BODY_SCAN_MAX_LINE_LEN + 1)
    _, _, scanned, skipped = scan_body_text_for_paths(oversized_only)

    assert scanned == 0
    assert skipped == 1
