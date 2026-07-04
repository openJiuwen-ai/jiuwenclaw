# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Timing statistics for AHE algorithm execution stages.

Tracks time spent in each stage of the AHE pipeline:
- LOAD: Loading traces from database
- CLEAN: Normalizing and validating traces
- FILTER: Filtering traces without LLM interaction
- EVAL: Evaluating trace outcomes
- DIAG: Diagnosis agent execution
- GOV: Governance context retrieval
- PROPOSE: Proposal generation via LLM
- APPLY: Writing proposals to storage

Provides detailed timing reports with:
- Per-stage time breakdown
- Per-trace timing within stages
- Overall execution summary
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StageTiming:
    """Timing data for a single stage."""
    name: str
    start_time: float = 0.0
    end_time: float = 0.0
    duration: float = 0.0
    call_count: int = 0
    trace_count: int = 0
    details: list[dict] = field(default_factory=list)

    def start(self) -> None:
        """Mark stage start."""
        self.start_time = time.time()
        self.call_count += 1

    def end(self, trace_count: int = 0, metadata: dict | None = None) -> None:
        """Mark stage end and calculate duration."""
        self.end_time = time.time()
        self.duration = self.end_time - self.start_time
        self.trace_count += trace_count

        # Store detail record
        detail = {
            "call": self.call_count,
            "duration": self.duration,
            "trace_count": trace_count,
            "start": self.start_time,
            "end": self.end_time,
        }
        if metadata:
            detail["metadata"] = metadata
        self.details.append(detail)

    def add_detail(self, trace_id: str, duration: float, metadata: dict | None = None) -> None:
        """Add per-trace timing detail."""
        detail = {
            "trace_id": trace_id,
            "duration": duration,
        }
        if metadata:
            detail["metadata"] = metadata
        self.details.append(detail)


@dataclass
class AheTimingStats:
    """Complete timing statistics for an AHE execution run."""

    # Overall execution
    run_start: float = 0.0
    run_end: float = 0.0
    total_duration: float = 0.0

    # Trace counts
    total_trace_ids: int = 0
    total_batches: int = 0
    traces_loaded: int = 0
    traces_normalized: int = 0
    traces_filtered: int = 0
    traces_evaluated: int = 0
    traces_failed: int = 0
    traces_diagnosed: int = 0

    # Proposal counts
    total_proposals: int = 0
    proposals_per_batch: list[int] = field(default_factory=list)

    # Stage timings
    stages: dict[str, StageTiming] = field(default_factory=lambda: {
        "LOAD": StageTiming("LOAD"),
        "CLEAN": StageTiming("CLEAN"),
        "FILTER": StageTiming("FILTER"),
        "EVAL": StageTiming("EVAL"),
        "DIAG": StageTiming("DIAG"),
        "GOV": StageTiming("GOV"),
        "PROPOSE": StageTiming("PROPOSE"),
        "APPLY": StageTiming("APPLY"),
        "TOTAL": StageTiming("TOTAL"),
    })

    def start_run(self, total_trace_ids: int) -> None:
        """Mark the start of an AHE run."""
        self.run_start = time.time()
        self.total_trace_ids = total_trace_ids
        self.stages["TOTAL"].start()

    def end_run(self, total_proposals: int) -> None:
        """Mark the end of an AHE run."""
        self.run_end = time.time()
        self.total_duration = self.run_end - self.run_start
        self.total_proposals = total_proposals
        self.stages["TOTAL"].end(trace_count=self.total_trace_ids)

    def start_stage(self, stage_name: str) -> None:
        """Start timing a stage."""
        if stage_name in self.stages:
            self.stages[stage_name].start()

    def end_stage(
        self,
        stage_name: str,
        trace_count: int = 0,
        metadata: dict | None = None
    ) -> None:
        """End timing a stage."""
        if stage_name in self.stages:
            self.stages[stage_name].end(trace_count=trace_count, metadata=metadata)

    def add_stage_detail(
        self,
        stage_name: str,
        trace_id: str,
        duration: float,
        metadata: dict | None = None
    ) -> None:
        """Add per-trace timing detail for a stage."""
        if stage_name in self.stages:
            self.stages[stage_name].add_detail(trace_id, duration, metadata)

    def generate_report(self) -> str:
        """Generate a detailed timing report."""
        lines = []
        lines.append("=" * 70)
        lines.append("AHE Algorithm Execution Timing Report")
        lines.append("=" * 70)

        # Overall summary
        lines.append("\n## Execution Summary")
        lines.append(f"Total Duration: {self.total_duration:.2f}s ({self._format_duration(self.total_duration)})")
        lines.append(f"Total Trace IDs: {self.total_trace_ids}")
        lines.append(f"Total Batches: {self.total_batches}")
        lines.append(f"Total Proposals: {self.total_proposals}")

        # Trace flow statistics
        lines.append("\n## Trace Flow Statistics")
        lines.append(f"  Traces Loaded: {self.traces_loaded}")
        lines.append(f"  Traces Normalized (CLEAN): {self.traces_normalized}")
        lines.append(f"  Traces Filtered (no LLM): {self.traces_filtered}")
        lines.append(f"  Traces Evaluated: {self.traces_evaluated}")
        lines.append(f"  Traces Failed/Uncertain: {self.traces_failed}")
        lines.append(f"  Traces Diagnosed: {self.traces_diagnosed}")

        if self.traces_diagnosed > 0:
            proposals_per_trace = self.total_proposals / self.traces_diagnosed
            lines.append(f"  Proposals per Failed Trace: {proposals_per_trace:.2f}")

        # Stage timing breakdown
        lines.append("\n## Stage Timing Breakdown")
        lines.append("-" * 70)
        lines.append(f"{'Stage':<12} {'Calls':<8} {'Traces':<8} {'Duration':<12} {'%Total':<8} {'Avg/Call':<12}")
        lines.append("-" * 70)

        # Calculate total stage time (excluding TOTAL itself)
        total_stage_time = sum(
            s.duration for name, s in self.stages.items()
            if name != "TOTAL" and s.duration > 0
        )

        for stage_name in ["LOAD", "CLEAN", "FILTER", "EVAL", "DIAG", "GOV", "PROPOSE", "APPLY"]:
            stage = self.stages[stage_name]
            if stage.call_count > 0:
                pct = (stage.duration / self.total_duration * 100) if self.total_duration > 0 else 0
                avg_per_call = stage.duration / stage.call_count
                lines.append(
                    f"{stage_name:<12} {stage.call_count:<8} {stage.trace_count:<8} "
                    f"{stage.duration:>10.2f}s {pct:>6.1f}% {avg_per_call:>10.2f}s"
                )

        lines.append("-" * 70)
        lines.append(
            f"{'TOTAL':<12} {1:<8} {self.total_trace_ids:<8} "
            f"{self.total_duration:>10.2f}s {100.0:>6.1f}%"
        )
        lines.append("-" * 70)

        # Detailed stage breakdown (optional)
        if any(len(s.details) > 1 for s in self.stages.values()):
            lines.append("\n## Detailed Stage Breakdown")
            for stage_name, stage in self.stages.items():
                if len(stage.details) > 1:
                    lines.append(f"\n### {stage_name} Stage Details")
                    for detail in stage.details:
                        if "trace_id" in detail:
                            lines.append(
                                f"  Trace {detail['trace_id'][:16]}: {detail['duration']:.2f}s"
                            )
                        elif "call" in detail:
                            lines.append(
                                f"  Call #{detail['call']}: {detail['duration']:.2f}s "
                                f"(traces: {detail['trace_count']})"
                            )

        # Per-batch summary
        if self.proposals_per_batch:
            lines.append("\n## Batch Processing Summary")
            for i, prop_count in enumerate(self.proposals_per_batch, 1):
                lines.append(f"  Batch {i}: {prop_count} proposals")

        # Performance metrics
        lines.append("\n## Performance Metrics")
        if self.traces_loaded > 0:
            time_per_trace = self.total_duration / self.traces_loaded
            lines.append(f"  Time per Loaded Trace: {time_per_trace:.2f}s")

        if self.traces_failed > 0:
            time_per_failed = (
                self.stages["DIAG"].duration +
                self.stages["GOV"].duration +
                self.stages["PROPOSE"].duration
            ) / self.traces_failed
            lines.append(f"  Time per Failed Trace (DIAG+GOV+PROPOSE): {time_per_failed:.2f}s")

        if self.total_proposals > 0:
            time_per_proposal = self.total_duration / self.total_proposals
            lines.append(f"  Time per Proposal Generated: {time_per_proposal:.2f}s")

        # Bottleneck identification
        lines.append("\n## Performance Bottleneck Analysis")
        bottleneck_stage = max(
            [(name, s.duration) for name, s in self.stages.items() if name != "TOTAL"],
            key=lambda x: x[1]
        )
        if bottleneck_stage[1] > 0:
            pct = bottleneck_stage[1] / self.total_duration * 100
            lines.append(f"  Primary Bottleneck: {bottleneck_stage[0]} ({pct:.1f}% of total time)")

        # LLM call analysis
        llm_stages = ["EVAL", "DIAG", "PROPOSE"]
        llm_time = sum(self.stages[s].duration for s in llm_stages)
        if llm_time > 0:
            llm_pct = llm_time / self.total_duration * 100
            lines.append(f"  LLM Processing Time: {llm_time:.2f}s ({llm_pct:.1f}% of total)")
            lines.append(f"    - EVAL: {self.stages['EVAL'].duration:.2f}s")
            lines.append(f"    - DIAG: {self.stages['DIAG'].duration:.2f}s")
            lines.append(f"    - PROPOSE: {self.stages['PROPOSE'].duration:.2f}s")

        lines.append("\n" + "=" * 70)

        return "\n".join(lines)

    def _format_duration(self, seconds: float) -> str:
        """Format duration in human-readable format."""
        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            minutes = seconds / 60
            return f"{minutes:.1f}m"
        else:
            hours = seconds / 3600
            return f"{hours:.1f}h"

    def to_dict(self) -> dict[str, Any]:
        """Export timing stats as dict for serialization."""
        return {
            "total_duration": self.total_duration,
            "total_trace_ids": self.total_trace_ids,
            "total_batches": self.total_batches,
            "total_proposals": self.total_proposals,
            "traces_loaded": self.traces_loaded,
            "traces_normalized": self.traces_normalized,
            "traces_filtered": self.traces_filtered,
            "traces_evaluated": self.traces_evaluated,
            "traces_failed": self.traces_failed,
            "traces_diagnosed": self.traces_diagnosed,
            "stages": {
                name: {
                    "duration": stage.duration,
                    "call_count": stage.call_count,
                    "trace_count": stage.trace_count,
                }
                for name, stage in self.stages.items()
            },
            "proposals_per_batch": self.proposals_per_batch,
        }


# Global timing stats instance (singleton pattern)
_current_stats: AheTimingStats | None = None


def get_timing_stats() -> AheTimingStats:
    """Get current timing stats instance."""
    global _current_stats
    if _current_stats is None:
        _current_stats = AheTimingStats()
    return _current_stats


def reset_timing_stats() -> AheTimingStats:
    """Reset timing stats for a new run."""
    global _current_stats
    _current_stats = AheTimingStats()
    return _current_stats