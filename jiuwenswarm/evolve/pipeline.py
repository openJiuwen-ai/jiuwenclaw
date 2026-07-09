# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Evolution Pipeline orchestrator.

Wires ProposalGenerators → DecisionPolicies → ApplyWriters into the
Trace → Proposal → Decision → Apply lifecycle.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from jiuwenswarm.evolve.models import (
    ApplyRecord,
    DecisionResult,
    Proposal,
    ProposalState,
    TraceBatch,
)

if TYPE_CHECKING:
    from jiuwenswarm.evolve.storage.base import EvolutionStore

logger = logging.getLogger(__name__)

# Default limits (overridable via config evolve.pipeline.limits)
DEFAULT_MAX_PROPOSALS = 3
DEFAULT_MAX_BEHAVIOR_PROPOSALS = 2
DEFAULT_MAX_TRACES = 10


@dataclass
class PipelineResult:
    """Result of a single evolution pipeline run."""

    batch_id: str
    proposals: list[Proposal] = field(default_factory=list)
    decision_results: list[DecisionResult] = field(default_factory=list)
    apply_records: list[ApplyRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def active_count(self) -> int:
        return sum(1 for p in self.proposals if p.state == ProposalState.ACTIVE)

    @property
    def rejected_count(self) -> int:
        return sum(1 for p in self.proposals if p.state == ProposalState.REJECTED)

    @property
    def applied_count(self) -> int:
        return sum(
            1 for r in self.apply_records if r.status.value == "applied"
        )


class EvolutionPipeline:
    """Orchestrate Trace → Proposal → Decision → Apply → Persist.

    Stages are sequential but with internal parallelism:
    - Generators run concurrently
    - Policies run concurrently per proposal
    - Writers run concurrently per proposal
    """

    def __init__(
        self,
        generators: list[object],
        policies: list[object],
        writers: list[object],
        store: EvolutionStore | None = None,
        max_proposals: int = DEFAULT_MAX_PROPOSALS,
        max_behavior_proposals: int = DEFAULT_MAX_BEHAVIOR_PROPOSALS,
        max_traces: int = DEFAULT_MAX_TRACES,
    ) -> None:
        self._generators = generators
        self._policies = policies
        self._writers = writers
        self._store = store
        self._max_proposals = max_proposals
        self._max_behavior = max_behavior_proposals
        self._max_traces = max_traces

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self, batch: TraceBatch) -> PipelineResult:
        """Execute the full pipeline on *batch*."""
        result = PipelineResult(batch_id=batch.batch_id)

        # Persist the batch first
        if self._store:
            try:
                self._store.save_trace_batch(batch)
            except Exception as exc:
                result.errors.append(f"save_trace_batch: {exc}")

        # 1. Generate
        proposals = await self._generate(batch)
        result.proposals = proposals

        # 2. Decide
        decisions_map = await self._decide(proposals)
        for decision_list in decisions_map.values():
            result.decision_results.extend(decision_list)

        # Apply decisions to proposal states
        self._update_proposal_states(proposals, decisions_map)

        # Enforce behavior proposal limit
        proposals = self._enforce_limit(proposals)

        # 3. Apply
        apply_records = await self._apply(proposals)
        result.apply_records = apply_records

        # 4. Persist
        await self._persist(proposals, result.decision_results, apply_records, batch)

        logger.info(
            "Pipeline complete: batch=%s, proposals=%d (active=%d, rejected=%d), "
            "decisions=%d, applied=%d, errors=%d",
            batch.batch_id,
            len(proposals),
            result.active_count,
            result.rejected_count,
            len(result.decision_results),
            result.applied_count,
            len(result.errors),
        )
        return result

    # ------------------------------------------------------------------
    # Stage implementations
    # ------------------------------------------------------------------

    async def _generate(self, batch: TraceBatch) -> list[Proposal]:
        """Run all generators concurrently, collect Proposals."""
        if not self._generators:
            logger.warning("No generators configured")
            return []

        tasks = []
        for gen in self._generators:
            tasks.append(gen.generate(batch))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        proposals: list[Proposal] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(
                    "Generator %s failed: %s",
                    getattr(self._generators[i], "name", i),
                    result,
                )
                continue
            if result:
                for p in result:
                    # Tag with batch_id
                    p.metadata["batch_id"] = batch.batch_id
                    proposals.append(p)

        logger.info(
            "Generation: %d proposals from %d generators",
            len(proposals), len(self._generators),
        )
        return proposals

    async def _decide(
        self, proposals: list[Proposal]
    ) -> dict[str, list[DecisionResult]]:
        """Run all policies against each proposal concurrently."""
        if not proposals or not self._policies:
            return {}

        async def evaluate_one(prop: Proposal) -> list[DecisionResult]:
            tasks = [policy.evaluate(prop) for policy in self._policies]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            valid: list[DecisionResult] = []
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    logger.error(
                        "Policy %s failed for %s: %s",
                        getattr(self._policies[i], "name", i),
                        prop.proposal_id,
                        r,
                    )
                else:
                    valid.append(r)
            return valid

        tasks = [evaluate_one(p) for p in proposals]
        results = await asyncio.gather(*tasks)

        decisions_map: dict[str, list[DecisionResult]] = {}
        for prop, dr_list in zip(proposals, results):
            decisions_map[prop.proposal_id] = dr_list
        return decisions_map

    @staticmethod
    def _update_proposal_states(
        proposals: list[Proposal],
        decisions_map: dict[str, list[DecisionResult]],
    ) -> None:
        """Apply DecisionResults to Proposal state.

        - Any blocking → REJECTED
        - All pass + at least one ACTIVE → ACTIVE
        - All pass + none ACTIVE → CANDIDATE

        Also stores the max DecisionPolicy score in metadata for the
        Apply writers.
        """
        for prop in proposals:
            drs = decisions_map.get(prop.proposal_id, [])
            if not drs:
                continue

            # Record max score
            max_score = max(d.score for d in drs)
            prop.metadata["max_score"] = max_score

            blocking = [d for d in drs if d.blocking]
            if blocking:
                prop.state = ProposalState.REJECTED
                continue

            suggestions = [d.suggestion for d in drs]
            if any(s.value == "active" for s in suggestions):
                prop.state = ProposalState.ACTIVE

    def _enforce_limit(self, proposals: list[Proposal]) -> list[Proposal]:
        """Enforce max Behavior Proposals per batch.

        Behavior Proposals = Skill or Memory type (affect next execution).
        Data Proposals = Training type (do NOT affect next execution).

        Behavior proposals beyond ``_max_behavior`` are deferred to
        CANDIDATE state. Total proposals beyond ``_max_proposals`` are
        also deferred. Training proposals are NOT counted against the
        behavior limit.
        """
        behavior = [p for p in proposals if p.target_type.value in ("skill", "memory")]
        # Total cap: keep highest-priority proposals
        if len(proposals) > self._max_proposals:
            # Sort: active first, then by score
            proposals.sort(
                key=lambda p: (
                    0 if p.state == ProposalState.ACTIVE else 1,
                    -float(p.metadata.get("max_score", 0)),
                )
            )
            for p in proposals[self._max_proposals:]:
                p.state = ProposalState.CANDIDATE
                logger.info(
                    "Total limit (%d): %s deferred to CANDIDATE",
                    self._max_proposals, p.proposal_id,
                )

        # Behavior-specific cap
        if len(behavior) > self._max_behavior:
            behavior.sort(
                key=lambda p: (
                    0 if p.state == ProposalState.ACTIVE else 1,
                    -float(p.metadata.get("max_score", 0)),
                )
            )
            for p in behavior[self._max_behavior:]:
                p.state = ProposalState.CANDIDATE
                logger.info(
                    "Behavior limit (%d): %s deferred to CANDIDATE",
                    self._max_behavior, p.proposal_id,
                )

        return proposals

    async def _apply(self, proposals: list[Proposal]) -> list[ApplyRecord]:
        """Run the appropriate writer for each active proposal."""
        if not self._writers:
            return []

        # Map target_type → writer
        writer_map: dict[str, object] = {}
        for w in self._writers:
            name = getattr(w, "name", "")
            if "skill" in name:
                writer_map["skill"] = w
            elif "memory" in name:
                writer_map["memory"] = w
            elif "training" in name:
                writer_map["training"] = w

        async def apply_one(prop: Proposal) -> ApplyRecord:
            if prop.state != ProposalState.ACTIVE:
                from jiuwenswarm.evolve.models import ApplyStatus, TargetStore

                return ApplyRecord(
                    proposal_id=prop.proposal_id,
                    target_type=prop.target_type,
                    target_store=TargetStore.SKILL_EXPERIENCE_STORE,
                    status=ApplyStatus.SKIPPED,
                    reason=f"Proposal not active (state={prop.state})",
                    applier_name="pipeline",
                )

            target_str = (
                prop.target_type.value
                if hasattr(prop.target_type, "value")
                else str(prop.target_type)
            )
            writer = writer_map.get(target_str)
            if writer is None:
                from jiuwenswarm.evolve.models import ApplyStatus, TargetStore

                return ApplyRecord(
                    proposal_id=prop.proposal_id,
                    target_type=prop.target_type,
                    target_store=TargetStore.SKILL_EXPERIENCE_STORE,
                    status=ApplyStatus.SKIPPED,
                    reason=f"No writer for target_type={target_str}",
                    applier_name="pipeline",
                )
            return await writer.apply(prop)

        tasks = [apply_one(p) for p in proposals]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        records: list[ApplyRecord] = []
        for r in results:
            if isinstance(r, Exception):
                logger.error("Apply failed: %s", r)
            else:
                records.append(r)
        return records

    async def _persist(
        self,
        proposals: list[Proposal],
        decisions: list[DecisionResult],
        apply_records: list[ApplyRecord],
        batch: TraceBatch,
    ) -> None:
        """Write all outputs to the EvolutionStore."""
        if self._store is None:
            return
        try:
            self._store.save_proposals(proposals)
            self._store.save_decision_results(decisions)
            self._store.save_apply_records(apply_records)
            logger.debug("Persisted %d proposals, %d decisions, %d apply records",
                         len(proposals), len(decisions), len(apply_records))
        except Exception as exc:
            logger.error("Persist failed: %s", exc)
