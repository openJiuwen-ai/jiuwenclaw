# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""register_evidence_first_rails — 把 evidence_first 的 Rails 装到 DeepAgent 上。

用法（在 swarm/agent 装配阶段调用）：

    from jiuwenswarm.agents.harness.evidence_first.registration import (
        install_evidence_first_rails,
    )
    ...
    await install_evidence_first_rails(
        deep_agent,
        budget_cap_tokens=200_000,
        task_id="task_042", config="verify+ledger", seed=20260805,
        output_schema={...},
    )

注册的 Rails：
- BudgetRail            （预算感知 + 超支拦截）
- ExecutionVerdictRail  （工具结果执行判定）
- ClaimEvidenceRail     （声明-证据绑定）
- OutputSchemaRail      （输出 JSON Schema 强制）
"""

from __future__ import annotations

import logging
from typing import Any

from jiuwenswarm.agents.harness.evidence_first.rails.budget_rail import BudgetRail
from jiuwenswarm.agents.harness.evidence_first.rails.claim_evidence_rail import (
    ClaimEvidenceRail,
)
from jiuwenswarm.agents.harness.evidence_first.rails.execution_verdict_rail import (
    ExecutionVerdictRail,
)
from jiuwenswarm.agents.harness.evidence_first.rails.output_schema_rail import (
    OutputSchemaRail,
)

logger = logging.getLogger(__name__)


async def install_evidence_first_rails(
    agent: Any,
    *,
    budget_cap_tokens: int = 0,
    task_id: str = "",
    config: str = "",
    seed: int = 0,
    output_schema: dict[str, Any] | None = None,
) -> list[Any]:
    """把 evidence_first 的 rails 注册到 agent（DeepAgent 实例）。

    返回已注册的 rail 实例列表，便于调用方后续读取绑定/判定结果。
    """
    rails = [
        BudgetRail(cap_tokens=budget_cap_tokens),
        ExecutionVerdictRail(),
        ClaimEvidenceRail(task_id=task_id, config=config, seed=seed),
    ]
    if output_schema:
        rails.append(OutputSchemaRail(output_schema))

    for rail in rails:
        if hasattr(agent, "register_rail"):
            await agent.register_rail(rail)
        else:
            logger.warning("[evidence_first] agent 无 register_rail，跳过 %s",
                           type(rail).__name__)
    logger.info("[evidence_first] 已安装 %d 个 rail 到 agent %s", len(rails), getattr(agent, "name", agent))
    return rails
