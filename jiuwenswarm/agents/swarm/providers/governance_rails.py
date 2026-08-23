# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Provider declarations for the opt-in governance rails.

Declares swarm-owned governance rails as serializable harness elements so they
can be referenced from ``config_specs`` by name and built through the manifest
catalog, exactly like the other ``swarm.*`` rails. Declarations run at import
time; the registry module imports this so the descriptors are present before
``register_from_catalog`` runs.

The rails declared here are opt-in: ``config_specs`` only mounts one when the
team config enables it, so default team assembly is unchanged.
"""

from __future__ import annotations

from typing import Any

from openjiuwen.agent_teams.harness.manifest import (
    ConstructionInput,
    ElementKind,
    harness_element,
)
from openjiuwen.harness.manifest.inputs import param_field

from jiuwenswarm.agents.harness.common.rails.quality_gate_rail import QualityGateRail
from jiuwenswarm.agents.harness.common.rails.quality_gate_scorers import resolve_scorer
from jiuwenswarm.agents.harness.common.rails.usage_report_rail import UsageReportRail

# swarm.* namespaced element names (parity with registry constants).
QUALITY_GATE = "swarm.quality_gate"
USAGE_REPORT = "swarm.usage_report"


class QualityGateInput(ConstructionInput):
    """Construction inputs for the quality-gate rail (all from ``params``)."""

    scorer: str = param_field(default="always_pass", description="Registered scorer name to resolve.")
    threshold: float = param_field(default=0.6, description="Minimum score to pass.")
    max_retries: int = param_field(default=1, description="Revise cycles before rejecting.")
    on_fail: str = param_field(default="revise", description="'revise' or 'reject' on failure.")
    gate_name: str = param_field(default="quality_gate", description="Gate label for logs / extra.")
    log_path: str = param_field(default="", description="Optional JSON-lines decision log path.")


class UsageReportInput(ConstructionInput):
    """Construction inputs for the usage-report rail (all from ``params``)."""

    report_path: str = param_field(default="usage_report.json", description="Destination JSON report path.")
    stage_label_key: str = param_field(default="stage_label", description="ctx.extra key holding the stage label.")
    default_label: str = param_field(default="unlabeled", description="Fallback stage label.")


@harness_element(
    kind=ElementKind.RAIL,
    name=QUALITY_GATE,
    description="Scores each task-loop round and passes / revises / rejects it (scorer by name).",
    input_model=QualityGateInput,
)
def _build_quality_gate_rail(params: dict[str, Any], context: Any) -> QualityGateRail:
    inp = QualityGateInput.resolve(params, context)
    return QualityGateRail(
        resolve_scorer(inp.scorer),
        threshold=inp.threshold,
        max_retries=inp.max_retries,
        on_fail=inp.on_fail,  # type: ignore[arg-type]
        gate_name=inp.gate_name,
        log_path=(inp.log_path or None),
    )


@harness_element(
    kind=ElementKind.RAIL,
    name=USAGE_REPORT,
    description="Accumulates per-stage model-call token usage and persists a merged JSON report.",
    input_model=UsageReportInput,
)
def _build_usage_report_rail(params: dict[str, Any], context: Any) -> UsageReportRail:
    inp = UsageReportInput.resolve(params, context)
    return UsageReportRail(
        inp.report_path,
        stage_label_key=inp.stage_label_key,
        default_label=inp.default_label,
    )


__all__ = ["QUALITY_GATE", "USAGE_REPORT", "QualityGateInput", "UsageReportInput"]
