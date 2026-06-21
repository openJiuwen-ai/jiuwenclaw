# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Skill Experience Apply writer.

Writes active Skill Proposals into ``evolutions.json`` inside the target
skill's directory so the existing ``SkillEvolutionRail`` /
``TeamSkillEvolutionRail`` can load and apply them in the next agent run.
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
    ExperienceOperationType,
    ExperienceOperation,
)
from jiuwenswarm.evolve.registry import apply_writers

logger = logging.getLogger(__name__)

# Compatible section for error/fix experiences.
_DEFAULT_SECTION = "Troubleshooting"
_DEFAULT_ACTION = "append"


def _resolve_skill_name(proposal: Proposal) -> str:
    """Return the skill directory name for *proposal*.

    Priority:
    1. ``proposal.target_id`` (if set)
    2. ``"general"`` (fallback)
    """
    if proposal.target_id:
        return proposal.target_id
    return "general"


def _build_content(proposal: Proposal) -> str:
    """Build a textual experience content block from the Proposal.

    The output is injected into the agent's context, so it should contain
    ACTIONABLE KNOWLEDGE, not diagnostic reports.
    """
    fix = proposal.targeted_fix
    if isinstance(fix, dict):
        suggestion = fix.get("suggestion", "")
        if suggestion:
            # The suggestion IS the knowledge — use it directly
            return suggestion

    # Fallback: use root_cause + predicted_impact
    root = proposal.root_cause.strip()
    impact = proposal.predicted_impact.strip()
    parts = []
    if root:
        parts.append(root)
    if impact:
        parts.append(impact)
    return "\n\n".join(parts) if parts else "No experience content"


@apply_writers.register("skill_writer")
class SkillExperienceWriter(ApplyWriter):
    """Write Skill Proposals as ``EvolutionRecord`` entries in
    ``{skills_dir}/{skill_name}/evolutions.json``.

    The output format follows the ``EvolutionLog`` schema from openjiuwen's
    ``agent_evolving.checkpointing.types``, which is the same format that
    ``SkillEvolutionRail`` and ``TeamSkillEvolutionRail`` consume at load
    time.
    """

    def __init__(
        self,
        skills_dir: str | None = None,
    ) -> None:
        super().__init__(name="skill_writer")
        if skills_dir:
            self._skills_dir = Path(skills_dir)
        else:
            from jiuwenswarm.common.utils import get_user_workspace_dir

            self._skills_dir = (
                get_user_workspace_dir() / "agent" / "workspace" / "skills"
            )

    async def apply(self, proposal: Proposal) -> ApplyRecord:
        if proposal.target_type != ProposalTargetType.SKILL:
            return ApplyRecord(
                proposal_id=proposal.proposal_id,
                target_type=ProposalTargetType.SKILL,
                target_store=TargetStore.SKILL_EXPERIENCE_STORE,
                status=ApplyStatus.SKIPPED,
                reason=(
                    f"target_type={proposal.target_type.value} "
                    "does not match skill_writer"
                ),
                applier_name=self.name,
            )

        if proposal.state != ProposalState.ACTIVE:
            return ApplyRecord(
                proposal_id=proposal.proposal_id,
                target_type=ProposalTargetType.SKILL,
                target_store=TargetStore.SKILL_EXPERIENCE_STORE,
                status=ApplyStatus.SKIPPED,
                reason=f"Proposal state is {proposal.state.value}, not active",
                applier_name=self.name,
            )

        try:
            skill_name = _resolve_skill_name(proposal)
            skill_dir = self._skills_dir / skill_name
            skill_dir.mkdir(parents=True, exist_ok=True)

            evolution_path = skill_dir / "evolutions.json"

            # Read existing log or create a fresh one
            evolution_log = self._load_or_create_log(
                evolution_path, skill_name
            )

            # Check for ExperienceOperation in metadata
            operations_raw = proposal.metadata.get("operations", [])
            if operations_raw:
                # Dispatch per operation — PDA-style governance-aware pipeline
                for op_dict in operations_raw:
                    op = ExperienceOperation(**op_dict)
                    match op.op:
                        case ExperienceOperationType.ADD:
                            self._apply_add(evolution_log, proposal, op)
                        case ExperienceOperationType.MERGE:
                            self._apply_merge(evolution_log, proposal, op)
                        case ExperienceOperationType.REPLACE:
                            self._apply_replace(evolution_log, proposal, op)
                        case ExperienceOperationType.UPDATE:
                            self._apply_update(evolution_log, proposal, op)
                        case ExperienceOperationType.DEPRECATE:
                            self._apply_deprecate(evolution_log, proposal, op)
                        case ExperienceOperationType.NOOP:
                            pass  # No action needed
            else:
                # Legacy path (no operations): default ADD behavior
                record = self._build_record(proposal)
                evolution_log.entries.append(record)  # type: ignore[attr-defined]

            evolution_log.updated_at = (  # type: ignore[attr-defined]
                datetime.now(timezone.utc).isoformat()
            )

            # Write back
            evolution_path.write_text(
                json.dumps(
                    evolution_log.to_dict(),  # type: ignore[attr-defined]
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            # Render evolution-index into SKILL.md + detail files
            # so SkillTool can discover body experiences on next read.
            try:
                from openjiuwen.agent_evolving.checkpointing import (
                    EvolutionStore as _OJStore,
                )

                oj_store = _OJStore(str(self._skills_dir))
                await oj_store.render_evolution_markdown(skill_name)
                logger.info(
                    "SkillExperienceWriter: rendered markdown for '%s'",
                    skill_name,
                )
            except Exception as render_exc:
                logger.warning(
                    "SkillExperienceWriter: render_evolution_markdown "
                    "failed for '%s' (non-fatal): %s",
                    skill_name,
                    render_exc,
                )

            logger.info(
                "SkillExperienceWriter: appended record %s → %s",
                record.id,  # type: ignore[attr-defined]
                evolution_path,
            )
            return ApplyRecord(
                proposal_id=proposal.proposal_id,
                target_type=ProposalTargetType.SKILL,
                target_store=TargetStore.SKILL_EXPERIENCE_STORE,
                target_id=str(evolution_path),
                status=ApplyStatus.APPLIED,
                stored_object_id=str(evolution_path),
                reason=(
                    f"EvolutionRecord {record.id} appended to "  # type: ignore[attr-defined]
                    f"{skill_name}/evolutions.json"
                ),
                applier_name=self.name,
            )
        except Exception as exc:
            logger.error(
                "SkillExperienceWriter: failed for %s: %s",
                proposal.proposal_id,
                exc,
            )
            return ApplyRecord(
                proposal_id=proposal.proposal_id,
                target_type=ProposalTargetType.SKILL,
                target_store=TargetStore.SKILL_EXPERIENCE_STORE,
                status=ApplyStatus.FAILED,
                reason=str(exc),
                applier_name=self.name,
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _load_or_create_log(path: Path, skill_id: str):
        """Load an existing ``EvolutionLog`` or create an empty one."""
        from openjiuwen.agent_evolving.checkpointing.types import EvolutionLog

        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return EvolutionLog.from_dict(data)
            except Exception as exc:
                logger.warning(
                    "Failed to parse existing evolutions.json for %s "
                    "(will create new): %s",
                    skill_id,
                    exc,
                )
        return EvolutionLog(
            skill_id=skill_id,
            version="1.0.0",
            entries=[],
        )

    @staticmethod
    def _build_record(proposal: Proposal):
        """Build an ``EvolutionRecord`` from a Proposal."""
        from openjiuwen.agent_evolving.checkpointing.types import (
            EvolutionPatch,
            EvolutionRecord,
        )
        from openjiuwen.agent_evolving.signal.base import EvolutionTarget

        content = _build_content(proposal)
        patch = EvolutionPatch(
            section=_DEFAULT_SECTION,
            action=_DEFAULT_ACTION,
            content=content,
            target=EvolutionTarget.BODY,
        )

        score = float(proposal.metadata.get("max_score", 0.6))
        summary = proposal.predicted_impact.strip() or None

        context_parts = [
            f"Auto-generated from trace analysis.",
            f"Proposal: {proposal.proposal_id}.",
            f"Type: {proposal.proposal_type}.",
        ]
        if proposal.target_id:
            context_parts.append(f"Target skill: {proposal.target_id}.")

        return EvolutionRecord.make(
            source="evolution_pipeline",
            context=" ".join(context_parts),
            change=patch,
            score=score,
            summary=summary,
        )

    # ── PDA ExperienceOperation handlers ──────────────────────────────

    def _apply_add(
        self, evolution_log, proposal: Proposal, op: ExperienceOperation
    ) -> None:
        """ADD a new experience entry with state=candidate."""
        from openjiuwen.agent_evolving.checkpointing.types import (
            EvolutionPatch, EvolutionRecord,
        )
        from openjiuwen.agent_evolving.signal.base import EvolutionTarget

        content = op.new_content or _build_content(proposal)
        patch = EvolutionPatch(
            section=_DEFAULT_SECTION, action=_DEFAULT_ACTION,
            content=content, target=EvolutionTarget.BODY,
        )
        record = EvolutionRecord.make(
            source="pda_proposer",
            context=f"PDA proposal {proposal.proposal_id}: {op.reason}",
            change=patch,
            score=float(proposal.metadata.get("max_score", 0.6)),
            summary=proposal.predicted_impact.strip() or None,
        )
        record.metadata["state"] = "candidate"
        record.metadata["proposal_id"] = proposal.proposal_id
        record.metadata["evidence_refs"] = [
            e.model_dump() for e in op.evidence_refs
        ]
        record.metadata["created_at"] = datetime.now(timezone.utc).isoformat()
        record.metadata["hit_count"] = 0
        record.metadata["success_after_hit_count"] = 0
        evolution_log.entries.append(record)

    def _apply_merge(
        self, evolution_log, proposal: Proposal, op: ExperienceOperation
    ) -> None:
        """MERGE evidence_refs into an existing entry."""
        target_id = op.target_experience_id
        for entry in evolution_log.entries:
            if entry.id == target_id:
                if entry.metadata is None:
                    entry.metadata = {}
                existing_ev = entry.metadata.get("evidence_refs", [])
                existing_ev.extend(
                    e.model_dump() for e in op.evidence_refs
                )
                entry.metadata["evidence_refs"] = existing_ev
                entry.metadata["merged_from"] = proposal.proposal_id
                logger.info("MERGE evidence to %s (%d refs)", target_id, len(op.evidence_refs))
                return
        logger.warning("MERGE target %s not found", target_id)

    def _apply_replace(
        self, evolution_log, proposal: Proposal, op: ExperienceOperation
    ) -> None:
        """REPLACE an existing entry's content."""
        target_id = op.target_experience_id
        new_content = op.new_content or _build_content(proposal)
        for entry in evolution_log.entries:
            if entry.id == target_id:
                if entry.change is not None:
                    entry.change.content = new_content
                entry.metadata["replaced_by"] = proposal.proposal_id
                entry.metadata["replaced_at"] = datetime.now(timezone.utc).isoformat()
                logger.info("REPLACE %s with new content", target_id)
                return
        logger.warning("REPLACE target %s not found", target_id)

    def _apply_update(
        self, evolution_log, proposal: Proposal, op: ExperienceOperation
    ) -> None:
        """UPDATE an existing entry's content."""
        self._apply_replace(evolution_log, proposal, op)

    def _apply_deprecate(
        self, evolution_log, proposal: Proposal, op: ExperienceOperation
    ) -> None:
        """DEPRECATE an existing entry."""
        target_id = op.target_experience_id
        for entry in evolution_log.entries:
            if entry.id == target_id:
                entry.metadata["state"] = "deprecated"
                entry.metadata["deprecated_by"] = proposal.proposal_id
                entry.metadata["deprecated_at"] = datetime.now(timezone.utc).isoformat()
                logger.info("DEPRECATE %s", target_id)
                return
        logger.warning("DEPRECATE target %s not found", target_id)
