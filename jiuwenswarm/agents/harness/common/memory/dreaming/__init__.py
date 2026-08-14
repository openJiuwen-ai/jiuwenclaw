# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Public API of Dreaming Memory Integration Module

Example:
    await start_dreaming(
        sessions_dir="/path/to/sessions",
        output_dir="/path/to/memory",
        mode="agent",
        busy_checker=lambda: sm.has_active_tasks(),
    )
    await stop_dreaming(mode="agent")
"""
from __future__ import annotations

import logging
from typing import Callable

from openjiuwen.core.memory.dreaming import DreamingOrchestrator

from .scheduler import CronDreamingOrchestrator

__all__ = [
    "start_dreaming",
    "stop_dreaming",
    "get_dreaming_orchestrator",
    "DreamingOrchestrator",
    "CronDreamingOrchestrator",
]

logger = logging.getLogger(__name__)

DreamingService = DreamingOrchestrator | CronDreamingOrchestrator

_orchestrators: dict[str, DreamingService] = {}


def get_dreaming_orchestrator(mode: str = "agent") -> DreamingService | None:
    return _orchestrators.get(mode)


async def start_dreaming(
    sessions_dir: str,
    output_dir: str,
    mode: str = "agent",
    busy_checker: Callable[[], bool] | None = None,
) -> DreamingService | None:
    """Start dreaming service.
    Idempotent: same mode repeated call returns existing instance.
    """
    if mode in _orchestrators:
        return _orchestrators[mode]

    from .sweeper import DreamingConfig, Sweeper

    cfg = DreamingConfig.load(mode)
    if not cfg.enabled:
        logger.info("[dreaming] %s mode enabled=false, not started", mode)
        return None

    sweeper = Sweeper(sessions_dir, output_dir, mode=mode)
    sweeper.init()

    if cfg.cron_expr is not None:
        orch: DreamingService = CronDreamingOrchestrator(
            sweep_fn=sweeper.run_sweep,
            cron_expr=cfg.cron_expr,
            timezone=cfg.timezone,
            busy_checker=busy_checker,
            name=f"dreaming-{mode}",
        )
    else:
        orch = DreamingOrchestrator(
            sweep_fn=sweeper.run_sweep,
            interval_seconds=cfg.interval_seconds,
            busy_checker=busy_checker,
            name=f"dreaming-{mode}",
        )
    await orch.start()
    _orchestrators[mode] = orch
    return orch


async def stop_dreaming(mode: str | None = None) -> None:
    """Stop dreaming service.
    Idempotent: same mode repeated call does nothing.
    """
    if mode is not None:
        orch = _orchestrators.pop(mode, None)
        if orch:
            try:
                await orch.stop()
            except Exception as exc:
                logger.warning("[dreaming] stop(%s) exception: %s", mode, exc)
    else:
        for m, orch in list(_orchestrators.items()):
            _orchestrators.pop(m, None)
            try:
                await orch.stop()
            except Exception as exc:
                logger.warning("[dreaming] stop(%s) exception: %s", m, exc)
