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
    2. ``proposal.targeted_fix["tool"]``
    3. ``"general"``
    """
    if proposal.target_id:
        return proposal.target_id
    fix = proposal.targeted_fix
    if isinstance(fix, dict):
        tool = fix.get("tool", "")
        if tool:
            return tool
    return "general"


def _build_content(proposal: Proposal) -> str:
    """Build a textual experience content block from the Proposal."""
    root = proposal.root_cause.strip()
    impact = proposal.predicted_impact.strip()
    risk = (proposal.risk or "").strip()

    parts = [f"**Problem**: {root}"]
    if impact:
        parts.append(f"**Expected improvement**: {impact}")
    if risk:
        parts.append(f"**Risk**: {risk}")

    # Include specific fix details if available
    fix = proposal.targeted_fix
    if isinstance(fix, dict):
        action = fix.get("action", "")
        suggestion = fix.get("suggestion", "")
        if action:
            parts.append(f"**Fix action**: {action}")
        if suggestion:
            parts.append(f"**Suggestion**: {suggestion}")
        # Flatten remaining keys
        for k, v in fix.items():
            if k not in ("action", "suggestion", "tool"):
                parts.append(f"**{k}**: {v}")

    return "\n\n".join(parts)


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

            # Build a new EvolutionRecord and append it
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

        # Carry over score if present in metadata
        score = float(proposal.metadata.get("max_score", 0.6))
        summary = proposal.predicted_impact.strip() or None

        return EvolutionRecord.make(
            source="evolution_pipeline",
            context=(
                f"Auto-generated from trace analysis. "
                f"Proposal: {proposal.proposal_id}. "
                f"Type: {proposal.proposal_type}."
            ),
            change=patch,
            score=score,
            summary=summary,
        )
