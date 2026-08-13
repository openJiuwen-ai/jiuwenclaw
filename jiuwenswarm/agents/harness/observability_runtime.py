# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared process-level trajectory processor access."""

from __future__ import annotations

import threading
from typing import Any

_DEMAND_LOCK = threading.RLock()
_TRAJECTORY_SPAN_PROCESSOR: Any | None = None


def get_trajectory_span_processor() -> Any:
    """Return the trajectory processor shared by all JiuwenClaw runtimes."""
    global _TRAJECTORY_SPAN_PROCESSOR
    with _DEMAND_LOCK:
        if _TRAJECTORY_SPAN_PROCESSOR is not None:
            return _TRAJECTORY_SPAN_PROCESSOR

        from openjiuwen.agent_evolving.trajectory.processor import (
            TrajectorySpanProcessor,
        )

        _TRAJECTORY_SPAN_PROCESSOR = TrajectorySpanProcessor()
        return _TRAJECTORY_SPAN_PROCESSOR
