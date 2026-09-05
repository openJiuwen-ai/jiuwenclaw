# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""evidence_first — 面向科研论文自动生成的证据优先扩展。

模块
----
- run_state_machine   ：ResearchRun 状态机 + 预注册漏斗判据
- verdict             ：ExecutionVerdict 执行判定分类器
- claim_evidence      ：声明-证据绑定 + Replay Certificate
- research_queue      ：预算感知多项目队列
- rails               ：DeepAgentRail 扩展（预算/执行判定/声明证据/输出Schema）
"""

from jiuwenswarm.agents.harness.evidence_first.claim_evidence import (
    ClaimBinding,
    ReplayCertificate,
    bind_claim,
    evidence_binding_ok,
)
from jiuwenswarm.agents.harness.evidence_first.research_queue import (
    ResearchQueue,
)
from jiuwenswarm.agents.harness.evidence_first.run_state_machine import (
    FunnelDecision,
    ResearchRun,
    RunBudget,
    RunStage,
    Transition,
    predefined_funnel_decision,
)
from jiuwenswarm.agents.harness.evidence_first.verdict import (
    ExecutionVerdict,
    classify,
)

__all__ = [
    "ClaimBinding", "ReplayCertificate", "bind_claim", "evidence_binding_ok",
    "ResearchQueue", "FunnelDecision", "ResearchRun", "RunBudget", "RunStage",
    "Transition", "predefined_funnel_decision", "ExecutionVerdict", "classify",
]
