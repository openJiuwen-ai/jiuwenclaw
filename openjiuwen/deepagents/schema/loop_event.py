# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Loop event schemas for DeepAgent outer task-loop."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class DeepLoopEventType(str, Enum):
    """Event types consumed by the outer task-loop."""

    FOLLOWUP = "followup"
    STEER = "steer"
    ABORT = "abort"


_EVENT_PRIORITY_MAP: Dict[DeepLoopEventType, int] = {
    DeepLoopEventType.ABORT: 0,
    DeepLoopEventType.STEER: 1,
    DeepLoopEventType.FOLLOWUP: 10,
}


@dataclass(order=True)
class DeepLoopEvent:
    """A queued outer-loop event.

    The first two fields are ordering keys for ``PriorityQueue``:
      - ``priority``: lower is higher priority
      - ``seq``: FIFO within same priority
    """

    priority: int
    seq: int

    created_at: float = field(default_factory=time.monotonic, compare=False)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()), compare=False)
    event_type: DeepLoopEventType = field(default=DeepLoopEventType.FOLLOWUP, compare=False)
    content: str = field(default="", compare=False)
    task_id: Optional[str] = field(default=None, compare=False)
    metadata: Dict[str, Any] = field(default_factory=dict, compare=False)


def default_event_priority(event_type: DeepLoopEventType) -> int:
    """Return default queue priority for a deep loop event type."""
    return _EVENT_PRIORITY_MAP.get(event_type, 10)


def create_loop_event(
    *,
    seq: int,
    event_type: DeepLoopEventType,
    content: str,
    task_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    priority: Optional[int] = None,
) -> DeepLoopEvent:
    """Build a DeepLoopEvent with default priority if omitted."""
    event_priority = default_event_priority(event_type) if priority is None else priority
    return DeepLoopEvent(
        priority=event_priority,
        seq=seq,
        event_type=event_type,
        content=content,
        task_id=task_id,
        metadata=metadata or {},
    )
