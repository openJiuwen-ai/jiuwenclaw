# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Per-stage token-usage accounting for the DeepAgent model loop.

``UsageReportRail`` reads the real token counts off each model response and
accumulates them under a *stage label*, then persists a merged JSON report. It
reuses the robust usage-reading approach from openjiuwen's ``SwarmflowBudgetRail``
(``openjiuwen/agent_teams/workflow/backends/budget_rail.py``): read
``AssistantMessage.usage_metadata.total_tokens``, and fall back to the
input/output split only when a provider omits the total. No length-based guessing:
a provider that reports nothing contributes zero rather than a fabricated number.

The report is merged across invokes, so one pipeline run — many members, many
``invoke`` calls — accumulates into a single file. Each ``after_invoke`` folds the
current invoke's tallies into the file and then clears the in-memory tallies, so
re-persisting never double-counts.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    ModelCallInputs,
)
from openjiuwen.harness.rails.base import DeepAgentRail

logger = logging.getLogger(__name__)

#: ``ctx.extra`` key the pipeline writes the active stage label under.
DEFAULT_STAGE_LABEL_KEY = "stage_label"
#: Label used when no stage label is available.
DEFAULT_LABEL = "unlabeled"


def read_usage_tokens(response: object | None) -> tuple[int, int, int]:
    """Return ``(input, output, total)`` tokens a model response reports.

    Mirrors ``SwarmflowBudgetRail._usage_tokens`` semantics: prefer
    ``usage_metadata.total_tokens``; when it is absent or zero, fall back to the
    ``input_tokens + output_tokens`` split. A response that reports no usage
    yields ``(0, 0, 0)`` rather than an estimate.
    """
    if response is None:
        return 0, 0, 0
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return 0, 0, 0
    inp = int(getattr(usage, "input_tokens", 0) or 0)
    out = int(getattr(usage, "output_tokens", 0) or 0)
    total = int(getattr(usage, "total_tokens", 0) or 0)
    if total <= 0:
        total = inp + out
    return inp, out, total


def _empty_bucket() -> dict[str, int]:
    return {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


class UsageReportRail(DeepAgentRail):
    """Accumulate model-call token usage per stage label and persist to JSON.

    Args:
        report_path: Destination JSON file. Parent directories are created on
            demand. The file is merged in place across invokes.
        stage_label_key: ``ctx.extra`` key the pipeline writes the stage label
            under. When missing, ``default_label`` is used.
        default_label: Fallback label. Defaults to ``"unlabeled"``.

    Attributes:
        stages: In-memory tallies for the current (not-yet-persisted) invoke,
            keyed by stage label.
    """

    #: Low priority: pure observation, no need to run before other rails.
    priority: int = 20

    def __init__(
        self,
        report_path: str | Path,
        *,
        stage_label_key: str = DEFAULT_STAGE_LABEL_KEY,
        default_label: str = DEFAULT_LABEL,
    ) -> None:
        super().__init__()
        self._report_path = Path(report_path)
        self._stage_label_key = stage_label_key
        self._default_label = default_label
        self.stages: dict[str, dict[str, int]] = {}

    # -- lifecycle ---------------------------------------------------------

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        """Bill the model call that just returned to its stage label."""
        inputs = ctx.inputs
        if not isinstance(inputs, ModelCallInputs):
            return
        inp, out, total = read_usage_tokens(inputs.response)
        label = self._resolve_label(ctx)
        bucket = self.stages.setdefault(label, _empty_bucket())
        bucket["calls"] += 1
        bucket["input_tokens"] += inp
        bucket["output_tokens"] += out
        bucket["total_tokens"] += total

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        """Fold this invoke's tallies into the on-disk report and reset."""
        if not self.stages:
            # Still bump invoke_count so the report reflects the run faithfully.
            self._persist({})
            return
        self._persist(self.stages)
        self.stages = {}

    # -- helpers -----------------------------------------------------------

    def _resolve_label(self, ctx: AgentCallbackContext) -> str:
        extra = getattr(ctx, "extra", None)
        if isinstance(extra, dict):
            value = extra.get(self._stage_label_key)
            if value:
                return str(value)
        return self._default_label

    def _persist(self, delta: dict[str, dict[str, int]]) -> None:
        """Merge *delta* into the report file (read-modify-write).

        The whole read-modify-write critical section is held under an
        ``fcntl.flock`` on a sidecar ``<report>.lock`` file so concurrent
        processes never interleave updates. The write itself goes to a
        same-directory temp file and is published via ``os.replace`` so the
        report on disk is always either the previous complete JSON or the
        new complete JSON, never a partial write.
        """
        self._report_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._report_path.with_name(self._report_path.name + ".lock")
        lock_fh = lock_path.open("a+")
        try:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
            try:
                report = self._load()
                stages = report.setdefault("stages", {})
                for label, bucket in delta.items():
                    target = stages.setdefault(label, _empty_bucket())
                    for key, value in bucket.items():
                        target[key] = int(target.get(key, 0)) + int(value)
                report["grand_total"] = self._grand_total(stages)
                report["invoke_count"] = int(report.get("invoke_count", 0)) + 1
                report["updated_at"] = time.time()

                tmp_path = self._report_path.with_name(self._report_path.name + ".tmp")
                with tmp_path.open("w", encoding="utf-8") as fh:
                    json.dump(report, fh, ensure_ascii=False, indent=2)
                os.replace(tmp_path, self._report_path)
            finally:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            logger.exception("[usage_report] failed to persist to %s", self._report_path)
        finally:
            lock_fh.close()

    def _load(self) -> dict[str, Any]:
        if not self._report_path.exists():
            return {}
        try:
            with self._report_path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except Exception:
            corrupt_path = self._report_path.with_name(
                f"{self._report_path.name}.corrupt-{int(time.time())}"
            )
            try:
                os.replace(self._report_path, corrupt_path)
            except OSError:
                logger.exception(
                    "[usage_report] failed to preserve corrupt report at %s", self._report_path
                )
            logger.warning(
                "[usage_report] existing report unreadable; preserved as %s and starting fresh",
                corrupt_path,
            )
            return {}

    @staticmethod
    def _grand_total(stages: dict[str, dict[str, int]]) -> dict[str, int]:
        total = _empty_bucket()
        for bucket in stages.values():
            for key in total:
                total[key] += int(bucket.get(key, 0))
        return total


__all__ = ["UsageReportRail", "read_usage_tokens", "DEFAULT_STAGE_LABEL_KEY", "DEFAULT_LABEL"]
