from __future__ import annotations

import asyncio
import json

from jiuwenswarm.research_evidence.benchmark import (
    TaskMetric,
    _deterministic_metrics_sha256,
)
from jiuwenswarm.research_evidence.workflow import (
    STAGES,
    WorkflowConfig,
    run_research_workflow,
)


def test_offline_workflow_materializes_all_stages_and_audit_artifacts(tmp_path):
    output = tmp_path / "workflow"
    manifest = asyncio.run(
        run_research_workflow(
            WorkflowConfig(
                topic="evidence governance for research agents",
                output_dir=output,
                mode="offline",
            )
        )
    )

    assert manifest["status"] == "completed"
    assert manifest["stages"] == list(STAGES)
    assert manifest["resource_summary"]["input_tokens"] == 0
    assert manifest["resource_summary"]["output_tokens"] == 0
    assert manifest["claim_verification_issues"] == []
    assert (output / "paper_draft.md").is_file()
    assert (output / "resource_events.jsonl").is_file()
    assert (output / "evidence_store" / "evidence.json").is_file()
    payload = json.loads((output / "workflow_manifest.json").read_text(encoding="utf-8"))
    assert len(payload["artifacts"]) == 6
    experiment_artifact = next(
        artifact for artifact in payload["artifacts"] if artifact["stage"] == "experiment"
    )
    assert experiment_artifact["duration_seconds"] > 0


def test_deterministic_metric_hash_excludes_wall_clock_latency():
    common = dict(
        task_id="task",
        method="EvidenceRail",
        size=32,
        seed=0,
        gold_recall=1.0,
        precision=1.0,
        claim_coverage=1.0,
        conflict_complete=1.0,
        source_diversity=1.0,
        context_risk=0.0,
        selected_tokens=100,
        token_ratio=0.1,
        utility=1.0,
    )
    fast = TaskMetric(**common, latency_ms=1.0)
    slow = TaskMetric(**common, latency_ms=999.0)

    assert _deterministic_metrics_sha256([fast]) == _deterministic_metrics_sha256([slow])
