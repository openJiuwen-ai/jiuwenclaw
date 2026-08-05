# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""ResearchRun 状态机 — 科研运行的可审计生命周期。

框架贡献：在 JiuwenSwarm 中为「科研论文自动生成」提供结构化运行状态机。
一次 ResearchRun 是一个可重放的科研单位：

    IDEA → PLAN → EXPERIMENT(smoke→verify→full) → ANALYSIS → PAPER → REPLAY → DONE
                    │  └─ STOP_CEILING / STOP_LOW_METRIC → STOPPED
                    └─ 预算耗尽 → FAILED

特性：
- 每个状态迁移都落盘（时间戳 + 理由 + 产物路径 + 预算），审计可追溯。
- 分级实验由「预注册判据」驱动：达标进下一级，天花板（所有配置几乎一致）
  或低指标立即止损，不再烧预算。
- 纯 Python、不依赖 openjiuwen 引擎，可离线单测。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class RunStage(str, Enum):
    """ResearchRun 状态。"""

    IDEA = "IDEA"
    PLAN = "PLAN"
    EXPERIMENT_SMOKE = "EXPERIMENT_SMOKE"
    EXPERIMENT_VERIFY = "EXPERIMENT_VERIFY"
    EXPERIMENT_FULL = "EXPERIMENT_FULL"
    ANALYSIS = "ANALYSIS"
    PAPER = "PAPER"
    REPLAY = "REPLAY"
    DONE = "DONE"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


TERMINAL_STAGES = {RunStage.DONE, RunStage.STOPPED, RunStage.FAILED}

# 分级顺序（漏斗）。
TIER_ORDER = ("smoke", "verify", "full")
TIER_STAGE = {
    "smoke": RunStage.EXPERIMENT_SMOKE,
    "verify": RunStage.EXPERIMENT_VERIFY,
    "full": RunStage.EXPERIMENT_FULL,
}


class FunnelDecision(str, Enum):
    """预注册分级判据的输出。"""

    PASS_TO_NEXT = "PASS_TO_NEXT"
    STOP_CEILING = "STOP_CEILING"       # 所有配置几乎一致 → 无差分
    STOP_LOW_METRIC = "STOP_LOW_METRIC"  # 主指标未达阈值
    DONE = "DONE"                        # full 级完成


# 差分天花板：配置间主指标差 < 0.05 视为无差分。
CEILING_SPREAD = 0.05
# 主指标及格线（agent 级看 answer_accuracy，claim 级看 f1）。
METRIC_FLOOR = 0.6


@dataclass
class RunBudget:
    """一次 ResearchRun 的预算账本（token 与调用数）。"""

    tokens_total: int = 0
    tokens_prompt: int = 0
    tokens_completion: int = 0
    calls: int = 0
    # 按阶段记账，如 {"EXPERIMENT_SMOKE": 12345}
    per_stage_tokens: dict[str, int] = field(default_factory=dict)
    per_stage_calls: dict[str, int] = field(default_factory=dict)

    def add(self, tokens: int, prompt: int, completion: int, calls: int = 1) -> None:
        self.tokens_total += tokens
        self.tokens_prompt += prompt
        self.tokens_completion += completion
        self.calls += calls

    def add_stage(self, stage: RunStage | str, tokens: int, calls: int) -> None:
        key = stage.value if isinstance(stage, RunStage) else str(stage)
        self.per_stage_tokens[key] = self.per_stage_tokens.get(key, 0) + tokens
        self.per_stage_calls[key] = self.per_stage_calls.get(key, 0) + calls
        self.tokens_total += tokens
        self.calls += calls


@dataclass
class Transition:
    """一次状态迁移记录。"""

    from_stage: RunStage
    to_stage: RunStage
    at: str
    reason: str = ""
    artifacts: list[str] = field(default_factory=list)
    decision: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["from_stage"] = self.from_stage.value if self.from_stage else None
        d["to_stage"] = self.to_stage.value
        return d


def predefined_funnel_decision(
    study_level: str,
    metrics: dict[str, Any],
    tier: str,
    by_config: dict[str, dict[str, Any]],
) -> FunnelDecision:
    """预注册分级判据（与 Phase 2 scripts/executor.py 的漏斗逻辑一致）。

    - full 级 → DONE（不再继续）
    - 天花板：>=2 个配置且主指标极差 < 0.05 → STOP_CEILING
    - 主指标 < 0.6 → STOP_LOW_METRIC
    - 否则 PASS_TO_NEXT
    """
    if tier == "full":
        return FunnelDecision.DONE
    key = "answer_accuracy" if study_level == "agent" else "f1"
    vals = [m.get(key, 0.0) for m in by_config.values() if m]
    if len(vals) >= 2 and round(max(vals) - min(vals), 6) < CEILING_SPREAD:
        return FunnelDecision.STOP_CEILING
    ok = (metrics.get(key, 0) or 0) >= METRIC_FLOOR
    return FunnelDecision.PASS_TO_NEXT if ok else FunnelDecision.STOP_LOW_METRIC


class ResearchRun:
    """可审计的科研运行状态机。"""

    def __init__(
        self,
        run_id: str,
        title: str,
        hypothesis: str,
        plan_path: Path | str | None = None,
        *,
        budget_cap_tokens: int | None = None,
    ) -> None:
        self.run_id = run_id
        self.title = title
        self.hypothesis = hypothesis
        self.plan_path = str(plan_path) if plan_path else ""
        self.budget_cap_tokens = budget_cap_tokens
        self.state = RunStage.IDEA
        self.transitions: list[Transition] = []
        self.budget = RunBudget()
        self.artifacts: dict[str, list[str]] = {}
        self.funnel_verdicts: list[dict[str, Any]] = []
        self.started_at = _now()
        self.completed_at: str | None = None
        self._record_transition(None, RunStage.IDEA, reason="run created")

    # -- 迁移 -------------------------------------------------------------

    def transition(
        self,
        to: RunStage,
        *,
        reason: str = "",
        artifacts: list[Path | str] | None = None,
        decision: FunnelDecision | str = "",
    ) -> bool:
        """尝试迁移到 to 状态。返回是否成功。

        校验规则：
        - 终态不可再迁移；
        - 预算耗尽视为 FAILED；
        - 迁移必须合法（见 _can_transition）。
        """
        if self.state in TERMINAL_STAGES:
            logger.warning("[ResearchRun %s] 终态 %s，拒绝迁移到 %s", self.run_id, self.state, to)
            return False
        if self.budget_cap_tokens is not None and self.budget.tokens_total > self.budget_cap_tokens:
            self._force_terminal(RunStage.FAILED, reason="budget cap exceeded")
            return False
        if to in TERMINAL_STAGES and to != RunStage.DONE:
            # STOPPED / FAILED 由专门的调用发起，见 _force_terminal。
            if to == RunStage.FAILED:
                self._force_terminal(RunStage.FAILED, reason=reason)
                return True
        if not self._can_transition(self.state, to):
            logger.warning("[ResearchRun %s] 非法迁移 %s -> %s", self.run_id, self.state, to)
            return False

        artifacts_str = [str(a) for a in (artifacts or [])]
        self.artifacts.setdefault(to.value, []).extend(artifacts_str)
        self._record_transition(self.state, to, reason=reason, artifacts=artifacts_str,
                                decision=decision)
        self.state = to
        if to in TERMINAL_STAGES:
            self.completed_at = _now()
        return True

    def _force_terminal(self, stage: RunStage, reason: str) -> None:
        self._record_transition(self.state, stage, reason=reason)
        self.state = stage
        self.completed_at = _now()

    @staticmethod
    def _can_transition(cur: RunStage, to: RunStage) -> bool:
        edges = {
            RunStage.IDEA: {RunStage.PLAN, RunStage.STOPPED, RunStage.FAILED},
            RunStage.PLAN: {RunStage.EXPERIMENT_SMOKE, RunStage.STOPPED, RunStage.FAILED},
            RunStage.EXPERIMENT_SMOKE: {RunStage.EXPERIMENT_VERIFY, RunStage.STOPPED, RunStage.FAILED},
            RunStage.EXPERIMENT_VERIFY: {RunStage.EXPERIMENT_FULL, RunStage.STOPPED, RunStage.FAILED},
            RunStage.EXPERIMENT_FULL: {RunStage.ANALYSIS, RunStage.FAILED},
            RunStage.ANALYSIS: {RunStage.PAPER, RunStage.FAILED},
            RunStage.PAPER: {RunStage.REPLAY, RunStage.FAILED},
            RunStage.REPLAY: {RunStage.DONE, RunStage.FAILED},
        }
        return to in edges.get(cur, set())

    def _record_transition(
        self,
        from_: RunStage | None,
        to: RunStage,
        *,
        reason: str,
        artifacts: list[str] | None = None,
        decision: FunnelDecision | str = "",
    ) -> None:
        self.transitions.append(Transition(
            from_stage=from_, to_stage=to, at=_now(),
            reason=reason, artifacts=artifacts or [],
            decision=decision.value if isinstance(decision, FunnelDecision) else str(decision),
        ))

    # -- 语义便捷方法 -------------------------------------------------------

    def plan(self, reason: str = "hypothesis selected", artifacts: list[Path | str] | None = None) -> bool:
        return self.transition(RunStage.PLAN, reason=reason, artifacts=artifacts)

    def execute_tier(
        self,
        tier: str,
        metrics: dict[str, Any],
        by_config: dict[str, dict[str, Any]],
        *,
        study_level: str = "agent",
        artifacts: list[Path | str] | None = None,
    ) -> FunnelDecision:
        """执行一级实验并按预注册判据决定去向。

        返回 FunnelDecision；会按决策自动迁移（止损/进入下一级/完成）。
        """
        assert tier in TIER_ORDER, f"未知分级 {tier}"
        decision = predefined_funnel_decision(study_level, metrics, tier, by_config)
        stage = TIER_STAGE[tier]
        self.funnel_verdicts.append({
            "tier": tier, "decision": decision.value,
            "metrics": metrics, "by_config": by_config,
        })

        if decision in (FunnelDecision.STOP_CEILING, FunnelDecision.STOP_LOW_METRIC):
            reason = ("配置间无差分（天花板），止损" if decision == FunnelDecision.STOP_CEILING
                      else "主指标未达阈值")
            self.transition(RunStage.STOPPED, reason=reason, artifacts=artifacts,
                            decision=decision)
            return decision

        # 先确保进入当前级实验状态（smoke 从 PLAN 进入；verify/full 通常已在上一级）。
        if self.state != stage:
            if not self.transition(stage, reason=f"进入 {tier} 级实验", artifacts=artifacts,
                                   decision=decision):
                self.fail(f"无法进入 {tier} 级（从 {self.state}）")
                return decision

        if decision == FunnelDecision.DONE:
            self.transition(RunStage.ANALYSIS, reason="full 级完成，进入分析", decision=decision)
        else:
            # PASS_TO_NEXT：迁到下一级实验状态（smoke→verify→full）。
            next_tier = TIER_ORDER[TIER_ORDER.index(tier) + 1]
            self.transition(TIER_STAGE[next_tier], reason=f"{tier} 达标，进入 {next_tier}",
                            decision=decision)
        return decision

    def analyze(self, artifacts: list[Path | str] | None = None) -> bool:
        return self.transition(RunStage.ANALYSIS, reason="实验完成，进入分析", artifacts=artifacts)

    def write_paper(self, artifacts: list[Path | str] | None = None) -> bool:
        return self.transition(RunStage.PAPER, reason="论文生成", artifacts=artifacts)

    def replay(self, artifacts: list[Path | str] | None = None) -> bool:
        return self.transition(RunStage.REPLAY, reason="复现验证", artifacts=artifacts)

    def done(self) -> bool:
        return self.transition(RunStage.DONE, reason="复现通过")

    def stop(self, reason: str = "") -> bool:
        self._force_terminal(RunStage.STOPPED, reason=reason or "手动止损")
        return True

    def fail(self, reason: str) -> bool:
        self._force_terminal(RunStage.FAILED, reason=reason)
        return True

    # -- 序列化 -------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "title": self.title,
            "hypothesis": self.hypothesis,
            "plan_path": self.plan_path,
            "state": self.state.value,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "budget_cap_tokens": self.budget_cap_tokens,
            "budget": asdict(self.budget),
            "transitions": [t.to_dict() for t in self.transitions],
            "artifacts": self.artifacts,
            "funnel_verdicts": self.funnel_verdicts,
        }

    def save(self, path: Path | str) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path | str) -> "ResearchRun":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        run = cls(data["run_id"], data["title"], data["hypothesis"], data.get("plan_path"),
                  budget_cap_tokens=data.get("budget_cap_tokens"))
        run.state = RunStage(data["state"])
        run.started_at = data["started_at"]
        run.completed_at = data.get("completed_at")
        for k, v in data.get("budget", {}).items():
            setattr(run.budget, k, v)
        run.artifacts = data.get("artifacts", {})
        run.funnel_verdicts = data.get("funnel_verdicts", [])
        run.transitions = [
            Transition(
                from_stage=RunStage(t["from_stage"]) if t["from_stage"] else None,
                to_stage=RunStage(t["to_stage"]), at=t["at"],
                reason=t.get("reason", ""), artifacts=t.get("artifacts", []),
                decision=t.get("decision", ""))
            for t in data.get("transitions", [])
        ]
        return run


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
