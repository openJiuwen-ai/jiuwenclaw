# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from jiuwenclaw.perf.events import LlmPerfEvent, TaskPerfEvent, ToolPerfEvent
from jiuwenclaw.perf.stats import (
    LlmStatsAccumulator,
    ToolStatsAccumulator,
    maintain_top_n,
    ms_to_s,
)


@dataclass
class RequestMeta:
    session_id: str
    request_id: str
    channel_id: str
    mode: str
    trace_id: str | None
    started_at: float
    service_id: str = "default"
    agent_id: str = "default"

    def with_trace_id(self, trace_id: str | None) -> RequestMeta:
        if trace_id:
            return RequestMeta(
                session_id=self.session_id,
                request_id=self.request_id,
                channel_id=self.channel_id,
                mode=self.mode,
                trace_id=trace_id,
                started_at=self.started_at,
                service_id=self.service_id,
                agent_id=self.agent_id,
            )
        return self


@dataclass
class RequestSummaryAccumulator:
    meta: RequestMeta
    status: str = "ok"
    first_byte_latency_ms: int | None = None
    ended_at: float | None = None
    llm_stats: LlmStatsAccumulator = field(default_factory=LlmStatsAccumulator)
    tool_stats: ToolStatsAccumulator = field(default_factory=ToolStatsAccumulator)
    task_count: int = 0
    task_total_ms: float = 0.0
    task_fail_count: int = 0
    unattributed_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    tasks: list[dict[str, Any]] = field(default_factory=list)
    bottleneck_llm: list[dict[str, Any]] = field(default_factory=list)
    bottleneck_tool: list[dict[str, Any]] = field(default_factory=list)
    bottleneck_task: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    flushed: bool = False
    _bottleneck_top_n: int = 3
    _include_errors: bool = False
    _task_stats: dict[str, tuple[LlmStatsAccumulator, ToolStatsAccumulator]] = field(
        default_factory=dict
    )

    def _task_stat_pair(self, task_id: str) -> tuple[LlmStatsAccumulator, ToolStatsAccumulator]:
        pair = self._task_stats.get(task_id)
        if pair is None:
            pair = (LlmStatsAccumulator(), ToolStatsAccumulator())
            self._task_stats[task_id] = pair
        return pair

    def _append_error(self, entry: dict[str, Any]) -> None:
        if not self._include_errors:
            return
        self.errors.append(entry)

    def set_first_byte_latency_ms(self, latency_ms: float) -> None:
        if self.first_byte_latency_ms is None and latency_ms >= 0:
            self.first_byte_latency_ms = int(round(latency_ms))

    def record_llm(self, event: LlmPerfEvent) -> None:
        self.llm_stats.record(
            duration_ms=event.duration_ms,
            status=event.status,
        )
        self.input_tokens += max(0, event.input_tokens)
        self.output_tokens += max(0, event.output_tokens)
        if event.task_id:
            llm_acc, _ = self._task_stat_pair(event.task_id)
            llm_acc.record(
                duration_ms=event.duration_ms,
                status=event.status,
            )
        else:
            self.unattributed_ms += event.duration_ms

        entry: dict[str, Any] = {
            "duration_s": ms_to_s(event.duration_ms),
            "agent_id": event.agent_id,
            "task_id": event.task_id,
            "model": event.model,
            "iteration": event.iteration,
            "input_tokens": event.input_tokens,
            "output_tokens": event.output_tokens,
        }
        if event.stream_source_id:
            entry["stream_source_id"] = event.stream_source_id
        if event.status != "ok":
            error_entry: dict[str, Any] = {
                "kind": "llm",
                "status": "error",
                "name": event.model or "unknown",
                "error": event.error_message or "unknown error",
                "task_id": event.task_id,
                "agent_id": event.agent_id,
                "iteration": event.iteration,
                "duration_s": ms_to_s(event.duration_ms),
            }
            self._append_error(error_entry)
        self.bottleneck_llm = maintain_top_n(
            self.bottleneck_llm,
            entry,
            top_n=self._bottleneck_top_n,
        )

    def record_tool(self, event: ToolPerfEvent) -> None:
        self.tool_stats.record(
            duration_ms=event.duration_ms,
            status=event.status,
            name=event.name,
            iteration=event.iteration,
        )
        if event.task_id:
            _, tool_acc = self._task_stat_pair(event.task_id)
            tool_acc.record(
                duration_ms=event.duration_ms,
                status=event.status,
                name=event.name,
                iteration=event.iteration,
            )
        else:
            self.unattributed_ms += event.duration_ms
        if event.status != "ok":
            error_entry: dict[str, Any] = {
                "kind": "tool",
                "status": "error",
                "name": event.name or "unknown",
                "call_id": event.tool_call_id,
                "error": event.error_message or "unknown error",
                "task_id": event.task_id,
                "agent_id": event.agent_id,
                "iteration": event.iteration,
                "duration_s": ms_to_s(event.duration_ms),
            }
            self._append_error(error_entry)
        entry = {
            "duration_s": ms_to_s(event.duration_ms),
            "name": event.name,
            "tool_call_id": event.tool_call_id,
            "agent_id": event.agent_id,
            "task_id": event.task_id,
            "iteration": event.iteration,
        }
        self.bottleneck_tool = maintain_top_n(
            self.bottleneck_tool,
            entry,
            top_n=self._bottleneck_top_n,
        )

    def record_task(self, event: TaskPerfEvent) -> None:
        self.task_count += 1
        self.task_total_ms += event.duration_ms
        if event.status not in ("completed", "succeeded", "ok"):
            self.task_fail_count += 1

        task_stats = self._task_stats.pop(event.task_id, None)
        if task_stats is None:
            llm_stats = LlmStatsAccumulator()
            tool_stats = ToolStatsAccumulator()
        else:
            llm_stats, tool_stats = task_stats

        task_entry = {
            "order": len(self.tasks) + 1,
            "task_id": event.task_id,
            "task_content": event.task_content,
            "source": event.source,
            "started_at": event.started_at,
            "ended_at": event.ended_at,
            "duration_s": ms_to_s(event.duration_ms),
            "status": event.status,
            "stats": {
                "llm": llm_stats.to_dict(),
                "tool": tool_stats.to_dict(),
            },
        }
        self.tasks.append(task_entry)
        task_rank_entry = {
            "duration_s": ms_to_s(event.duration_ms),
            "task_id": event.task_id,
            "task_content": event.task_content,
        }
        self.bottleneck_task = maintain_top_n(
            self.bottleneck_task,
            task_rank_entry,
            top_n=self._bottleneck_top_n,
        )

    def finalize(self, *, status: str | None = None, ended_at: float | None = None) -> dict[str, Any]:
        if status is not None:
            self.status = status
        self.ended_at = ended_at if ended_at is not None else time.time()
        total_s = ms_to_s(max(0.0, (self.ended_at - self.meta.started_at) * 1000))
        unattributed_s = ms_to_s(self.unattributed_ms)
        if self.first_byte_latency_ms is None:
            first_byte_latency_s = 0.0
        else:
            first_byte_latency_s = ms_to_s(float(self.first_byte_latency_ms))

        summary: dict[str, Any] = {
            "schema_version": 1,
            "meta": {
                "session_id": self.meta.session_id,
                "request_id": self.meta.request_id,
                "channel_id": self.meta.channel_id,
                "mode": self.meta.mode,
                "trace_id": self.meta.trace_id,
                "started_at": self.meta.started_at,
                "ended_at": self.ended_at,
            },
            "summary": {
                "total_s": total_s,
                "first_byte_latency_s": first_byte_latency_s,
                "status": self.status,
                "stats": {
                    "llm": self.llm_stats.to_dict(),
                    "tool": self.tool_stats.to_dict(),
                    "task": {
                        "count": self.task_count,
                        "total_s": ms_to_s(self.task_total_ms),
                        "fail_count": self.task_fail_count,
                    },
                    "unattributed_s": unattributed_s,
                },
                "tokens": {
                    "input": self.input_tokens,
                    "output": self.output_tokens,
                    "cache_read": self.cache_read_tokens,
                },
            },
            "tasks": self.tasks,
            "bottleneck": {
                "task": self.bottleneck_task,
                "llm": self.bottleneck_llm,
                "tool": self.bottleneck_tool,
            },
        }
        if self._include_errors:
            summary["errors"] = self.errors
        return summary
