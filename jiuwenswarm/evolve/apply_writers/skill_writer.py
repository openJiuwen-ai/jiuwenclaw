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

        # Carry over score if present in metadata
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
