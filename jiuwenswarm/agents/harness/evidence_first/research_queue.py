# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""ResearchQueue — 预算感知的多项目科研队列。

框架贡献：在固定预算下顺序执行多个 ResearchRun，产出可审计的资源日志。

- `ResearchQueue.schedule(run)`：加入队列。
- `ResearchQueue.execute(runner)`：按入队顺序执行；每个 run 交给 runner
  推进状态机（runner 由调用方提供，可注入真实引擎或离线假实现）。
- 预算护栏：总 token 超过 `budget_cap_tokens` 后，剩余 run 不再执行，
  状态置为 STOPPED 并记录 `skipped_reason="budget exhausted"`。
- 产出资源日志（每 run 的 token/调用数 + 累计），供资源报告使用。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from jiuwenswarm.agents.harness.evidence_first.run_state_machine import (
    ResearchRun, RunStage, TERMINAL_STAGES,
)

logger = logging.getLogger(__name__)

# runner 签名：接收 run，把它推进到终态（通过 run.transition/execute_tier 等）。
Runner = Callable[[ResearchRun], None]


@dataclass
class QueueResult:
    """队列执行结果。"""

    executed: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    total_tokens: int = 0
    total_calls: int = 0
    completed: int = 0
    budget_cap_tokens: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "executed": self.executed,
            "skipped": self.skipped,
            "total_tokens": self.total_tokens,
            "total_calls": self.total_calls,
            "completed": self.completed,
            "budget_cap_tokens": self.budget_cap_tokens,
        }


class ResearchQueue:
    """顺序执行、预算封顶的科研队列。"""

    def __init__(
        self,
        runs_dir: Path | str | None = None,
        *,
        budget_cap_tokens: int | None = None,
    ) -> None:
        self.runs_dir = Path(runs_dir) if runs_dir else None
        self.budget_cap_tokens = budget_cap_tokens
        self._runs: list[ResearchRun] = []

    def schedule(self, run: ResearchRun) -> None:
        self._runs.append(run)

    def execute(self, runner: Runner | None = None) -> QueueResult:
        """执行全部 run。

        runner 为空时用默认 runner：仅记录 run 已排队（不推进状态机），
        供纯离线测试使用；真实调用方应传入能驱动引擎的 runner。
        """
        result = QueueResult(budget_cap_tokens=self.budget_cap_tokens)
        total_tokens = 0
        total_calls = 0

        for run in self._runs:
            if self.budget_cap_tokens is not None and total_tokens > self.budget_cap_tokens:
                run.transition(RunStage.STOPPED, reason="budget exhausted")
                result.skipped.append({
                    "run_id": run.run_id, "reason": "budget exhausted",
                    "budget_before": total_tokens,
                })
                logger.info("[ResearchQueue] 预算耗尽，跳过 %s", run.run_id)
                continue

            try:
                if runner is None:
                    # 默认 runner：仅记账（0 token），不推进状态机。
                    # 真实调用方应传入能驱动引擎的 runner。
                    run.budget.add(0, 0, 0)
                else:
                    runner(run)
            except Exception as exc:  # noqa: BLE001
                run.fail(f"runner error: {exc}")
                logger.exception("[ResearchQueue] run %s 执行失败", run.run_id)

            total_tokens += run.budget.tokens_total
            total_calls += run.budget.calls
            result.executed.append(run.to_dict())
            if run.state == RunStage.DONE:
                result.completed += 1

        result.total_tokens = total_tokens
        result.total_calls = total_calls
        return result

    # -- 资源日志 -------------------------------------------------------------

    def write_resource_log(self, result: QueueResult, path: Path | str) -> Path:
        """写资源报告（token/调用/逐 run 明细），供赛题资源报告交付物使用。"""
        out = Path(path)
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "budget_cap_tokens": self.budget_cap_tokens,
            "total_tokens": result.total_tokens,
            "total_calls": result.total_calls,
            "completed_runs": result.completed,
            "queue_size": len(self._runs),
            "runs": [
                {
                    "run_id": r.run_id,
                    "state": r.state.value,
                    "tokens_total": r.budget.tokens_total,
                    "calls": r.budget.calls,
                    "per_stage_tokens": r.budget.per_stage_tokens,
                    "per_stage_calls": r.budget.per_stage_calls,
                }
                for r in self._runs
            ],
        }
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return out
