# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Append-only resource and lifecycle accounting."""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from jiuwenswarm.research_evidence.schemas import utc_now_iso


@dataclass(slots=True)
class ResourceEvent:
    run_id: str
    stage: str
    event: str
    timestamp: str = field(default_factory=utc_now_iso)
    duration_seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    tool: str = ""
    success: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


class ResourceLedger:
    """Append-only JSONL ledger suitable for resource-report generation."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._starts: dict[str, float] = {}

    def start(self, key: str) -> None:
        self._starts[str(key)] = time.perf_counter()

    def elapsed(self, key: str) -> float:
        started = self._starts.pop(str(key), None)
        return max(0.0, time.perf_counter() - started) if started is not None else 0.0

    def append(self, event: ResourceEvent) -> None:
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(asdict(event), ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def read(self) -> list[ResourceEvent]:
        if not self.path.exists():
            return []
        events: list[ResourceEvent] = []
        with self.path.open(encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    events.append(ResourceEvent(**json.loads(line)))
        return events

    def summary(self) -> dict[str, Any]:
        events = self.read()
        return {
            "events": len(events),
            "input_tokens": sum(item.input_tokens for item in events),
            "output_tokens": sum(item.output_tokens for item in events),
            "duration_seconds": sum(item.duration_seconds for item in events),
            "failures": sum(1 for item in events if not item.success),
            "by_stage": _group(events, "stage"),
            "by_event": _group(events, "event"),
        }


def _group(events: list[ResourceEvent], field_name: str) -> dict[str, dict[str, float | int]]:
    result: dict[str, dict[str, float | int]] = {}
    for event in events:
        key = str(getattr(event, field_name) or "unknown")
        bucket = result.setdefault(
            key,
            {"events": 0, "input_tokens": 0, "output_tokens": 0, "duration_seconds": 0.0},
        )
        bucket["events"] = int(bucket["events"]) + 1
        bucket["input_tokens"] = int(bucket["input_tokens"]) + event.input_tokens
        bucket["output_tokens"] = int(bucket["output_tokens"]) + event.output_tokens
        bucket["duration_seconds"] = float(bucket["duration_seconds"]) + event.duration_seconds
    return result
