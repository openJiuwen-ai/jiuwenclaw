# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Evidence-grounded context management for long-horizon Agent workflows.

The package is intentionally independent from a model provider.  Its storage,
selection, verification, and accounting primitives can therefore be tested and
benchmarked without an API key, while :class:`ResearchEvidenceRail` connects
those primitives to the JiuwenSwarm lifecycle.
"""

from jiuwenswarm.research_evidence.claim_graph import ClaimEvidenceGraph
from jiuwenswarm.research_evidence.rail import ResearchEvidenceRail
from jiuwenswarm.research_evidence.schemas import (
    Claim,
    Evidence,
    EvidenceKind,
    SelectionResult,
    VerificationIssue,
)
from jiuwenswarm.research_evidence.selector import EvidenceSelector, SelectorConfig
from jiuwenswarm.research_evidence.store import EvidenceStore
from jiuwenswarm.research_evidence.workflow import (
    ResearchWorkflow,
    WorkflowConfig,
    run_research_workflow,
)

__all__ = [
    "Claim",
    "ClaimEvidenceGraph",
    "Evidence",
    "EvidenceKind",
    "EvidenceRail",
    "EvidenceSelector",
    "EvidenceStore",
    "ResearchEvidenceRail",
    "ResearchWorkflow",
    "SelectionResult",
    "SelectorConfig",
    "VerificationIssue",
    "WorkflowConfig",
    "run_research_workflow",
]

# Backwards-friendly short alias for downstream competition projects.
EvidenceRail = ResearchEvidenceRail
