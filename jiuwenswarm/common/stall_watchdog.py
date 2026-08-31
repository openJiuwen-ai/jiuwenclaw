# coding: utf-8
"""Stall watchdog: dump all asyncio task + thread stacks when an operation hangs.

背景：2026-08-30 两起 session adapter 创建卡死（191s/222s 后自愈一次、
另一次卡到进程被杀），日志在卡死窗口内完全静默，无法定位悬挂点。
本模块提供一个 async 上下文管理器：被包裹的操作超过阈值未完成时，
把全量 asyncio task 栈 + 全线程帧打到日志，供事后定位。

默认开启；env ``JIUWEN_STALL_WATCHDOG=off`` 整体关闭。
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import traceback
from contextlib import asynccontextmanager
from typing import AsyncIterator

from openjiuwen.core.common.logging import logger

_ENABLED = os.environ.get("JIUWEN_STALL_WATCHDOG", "on").strip().lower() not in (
    "off",
    "0",
    "false",
)


def _dump_stacks(label: str, elapsed: float) -> None:
    """Dump every asyncio task stack and every thread frame to the log."""
    lines = [
        f"[stall-watchdog] {label} stalled for {elapsed:.0f}s — dumping stacks",
    ]
    try:
        tasks = asyncio.all_tasks()
    except Exception as exc:  # pragma: no cover - defensive
        tasks = []
        lines.append(f"(asyncio.all_tasks failed: {exc})")
    for task in sorted(tasks, key=lambda t: t.get_name()):
        state = "done" if task.done() else ("cancelled" if task.cancelled() else "pending")
        lines.append(f"--- task {task.get_name()} [{state}]")
        stack = task.get_stack(limit=30)
        if not stack:
            lines.append("    (no stack — not currently suspended)")
        for frame in stack:
            for line in traceback.format_stack(frame, limit=30):
                lines.extend(f"    {sub}" for sub in line.rstrip().splitlines())
    for thread_id, frame in sys._current_frames().items():
        lines.append(f"--- thread {thread_id}")
        for line in traceback.format_stack(frame, limit=30):
            lines.extend(f"    {sub}" for sub in line.rstrip().splitlines())
    # 逐行输出：openjiuwen 的 formatter 会把换行转义成字面 \n，单条巨型 message
    # 在 app.log 里不可读；逐行 warning 保证每个栈帧独立成行。
    for line in lines:
        logger.warning("%s", line)


@asynccontextmanager
async def stall_watchdog(
    label: str,
    *,
    first_after_seconds: float = 30.0,
    repeat_after_seconds: float = 60.0,
    max_dumps: int = 3,
) -> AsyncIterator[None]:
    """Wrap an awaitable region; dump stacks if it stalls past the thresholds.

    Dumps at ``first_after_seconds``, then every ``repeat_after_seconds``,
    up to ``max_dumps`` times. Zero overhead when the region completes in time.
    """
    if not _ENABLED:
        yield
        return

    started = time.monotonic()

    async def _watch() -> None:
        for index in range(max_dumps):
            delay = first_after_seconds if index == 0 else repeat_after_seconds
            await asyncio.sleep(delay)
            try:
                _dump_stacks(label, time.monotonic() - started)
            except Exception:
                logger.exception("[stall-watchdog] dump failed: %s", label)

    task = asyncio.create_task(_watch(), name=f"stall-watchdog[{label[:40]}]")
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
