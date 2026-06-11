# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Training Candidate Apply writer.

Writes trace_ids from a Training Candidate Proposal into the
``training_candidates`` table in evolution.db with status ``pending``.
"""

from __future__ import annotations

import logging

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


@apply_writers.register("training_writer")
class TrainingCandidateWriter(ApplyWriter):
    """Write trace_ids from Training Candidate Proposals into the
    ``training_candidates`` table.

    Each trace_id from the Proposal's ``failure_evidence`` is inserted
    with ``status: pending`` for later review by the model evolution track.
    """

    def __init__(self, store: object | None = None) -> None:
        super().__init__(name="training_writer")
        self._store = store  # EvolutionStore — set by pipeline

    async def apply(self, proposal: Proposal) -> ApplyRecord:
        if proposal.target_type != ProposalTargetType.TRAINING:
            return ApplyRecord(
                proposal_id=proposal.proposal_id,
                target_type=ProposalTargetType.TRAINING,
                target_store=TargetStore.TRAINING_CANDIDATE_STORE,
                status=ApplyStatus.SKIPPED,
                reason=f"target_type={proposal.target_type} does not match training_writer",
                applier_name=self.name,
            )

        if proposal.state != ProposalState.ACTIVE:
            return ApplyRecord(
                proposal_id=proposal.proposal_id,
                target_type=ProposalTargetType.TRAINING,
                target_store=TargetStore.TRAINING_CANDIDATE_STORE,
                status=ApplyStatus.SKIPPED,
                reason=f"Proposal state is {proposal.state}, not active",
                applier_name=self.name,
            )

        if self._store is None:
            return ApplyRecord(
                proposal_id=proposal.proposal_id,
                target_type=ProposalTargetType.TRAINING,
                target_store=TargetStore.TRAINING_CANDIDATE_STORE,
                status=ApplyStatus.FAILED,
                reason="No EvolutionStore configured for training_writer",
                applier_name=self.name,
            )

        try:
            batch_id = proposal.metadata.get("batch_id", "unknown")
            inserted_count = 0
            for evidence in proposal.failure_evidence:
                self._store.save_training_candidate(
                    trace_id=evidence.trace_id,
                    proposal_id=proposal.proposal_id,
                    batch_id=batch_id,
                )
                inserted_count += 1

            logger.info(
                "TrainingCandidateWriter: inserted %d trace_ids for %s",
                inserted_count,
                proposal.proposal_id,
            )
            return ApplyRecord(
                proposal_id=proposal.proposal_id,
                target_type=ProposalTargetType.TRAINING,
                target_store=TargetStore.TRAINING_CANDIDATE_STORE,
                status=ApplyStatus.APPLIED,
                stored_object_id=f"training_candidates:{inserted_count}",
                reason=f"Inserted {inserted_count} trace_ids into training_candidates table (status=pending)",
                applier_name=self.name,
            )
        except Exception as exc:
            logger.error(
                "TrainingCandidateWriter: failed for %s: %s",
                proposal.proposal_id,
                exc,
            )
            return ApplyRecord(
                proposal_id=proposal.proposal_id,
                target_type=ProposalTargetType.TRAINING,
                target_store=TargetStore.TRAINING_CANDIDATE_STORE,
                status=ApplyStatus.FAILED,
                reason=str(exc),
                applier_name=self.name,
            )
