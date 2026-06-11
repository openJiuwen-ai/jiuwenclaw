# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Memory Policy Apply writer.

Writes the targeted_fix of an active Memory Proposal to the Memory
Policy Store.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from jiuwenswarm.evolve.apply_writers.base import ApplyWriter
from jiuwenswarm.evolve.models import (
    ApplyRecord,
    ApplyStatus,
    Proposal,
    ProposalState,
    ProposalTargetType,
    TargetStore,
)
from jiuwenswarm.evolve.registry import apply_writers

logger = logging.getLogger(__name__)


@apply_writers.register("memory_writer")
class MemoryPolicyWriter(ApplyWriter):
    """Write Memory Proposals to the memory policy directory."""

    def __init__(
        self,
        memory_dir: str | None = None,
    ) -> None:
        super().__init__(name="memory_writer")
        if memory_dir:
            self._memory_dir = Path(memory_dir)
        else:
            from jiuwenswarm.common.utils import get_user_workspace_dir

            self._memory_dir = (
                get_user_workspace_dir() / "agent" / "workspace" / "memory"
            )
        self._policies_dir = self._memory_dir / "policies"
        self._policies_dir.mkdir(parents=True, exist_ok=True)

    async def apply(self, proposal: Proposal) -> ApplyRecord:
        if proposal.target_type != ProposalTargetType.MEMORY:
            return ApplyRecord(
                proposal_id=proposal.proposal_id,
                target_type=ProposalTargetType.MEMORY,
                target_store=TargetStore.MEMORY_POLICY_STORE,
                status=ApplyStatus.SKIPPED,
                reason=f"target_type={proposal.target_type} does not match memory_writer",
                applier_name=self.name,
            )

        if proposal.state != ProposalState.ACTIVE:
            return ApplyRecord(
                proposal_id=proposal.proposal_id,
                target_type=ProposalTargetType.MEMORY,
                target_store=TargetStore.MEMORY_POLICY_STORE,
                status=ApplyStatus.SKIPPED,
                reason=f"Proposal state is {proposal.state}, not active",
                applier_name=self.name,
            )

        try:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            filename = (
                f"policy-{proposal.proposal_type}-{timestamp}-"
                f"{proposal.proposal_id[:8]}.json"
            )
            filepath = self._policies_dir / filename

            policy = {
                "source": "evolution_pipeline",
                "proposal_id": proposal.proposal_id,
                "proposal_type": proposal.proposal_type,
                "root_cause": proposal.root_cause,
                "targeted_fix": proposal.targeted_fix,
                "predicted_impact": proposal.predicted_impact,
                "risk": proposal.risk,
                "created_at": proposal.created_at,
            }
            filepath.write_text(
                json.dumps(policy, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            logger.info(
                "MemoryPolicyWriter: wrote %s → %s",
                proposal.proposal_id,
                filepath,
            )
            return ApplyRecord(
                proposal_id=proposal.proposal_id,
                target_type=ProposalTargetType.MEMORY,
                target_store=TargetStore.MEMORY_POLICY_STORE,
                target_id=str(filepath),
                status=ApplyStatus.APPLIED,
                stored_object_id=str(filepath),
                reason=f"Memory policy written to {filepath}",
                applier_name=self.name,
            )
        except Exception as exc:
            logger.error(
                "MemoryPolicyWriter: failed for %s: %s",
                proposal.proposal_id,
                exc,
            )
            return ApplyRecord(
                proposal_id=proposal.proposal_id,
                target_type=ProposalTargetType.MEMORY,
                target_store=TargetStore.MEMORY_POLICY_STORE,
                status=ApplyStatus.FAILED,
                reason=str(exc),
                applier_name=self.name,
            )
