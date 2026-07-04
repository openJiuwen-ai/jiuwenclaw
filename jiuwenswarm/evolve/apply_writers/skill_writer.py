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
_EFFECTIVE_AFTER_RELOAD_MESSAGE = (
    "Skill evolution applied. It will take effect in newly created sessions "
    "or after agent reload."
)


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

    Priority:
    1. targeted_fix.suggestion (most actionable)
    2. targeted_fix.action + targeted_fix.suggestion
    3. root_cause + targeted_fix.action (explanation + concrete action)
    4. root_cause + predicted_impact (fallback, less actionable)
    """
    fix = proposal.targeted_fix
    if isinstance(fix, dict):
        suggestion = fix.get("suggestion", "")
        action = fix.get("action", "")

        # Priority 1: Use suggestion directly (most actionable)
        if suggestion:
            return suggestion

        # Priority 2: Combine action + suggestion
        if action and suggestion:
            return f"{action}\n\n{suggestion}"

        # Priority 3: root_cause + action (explanation + concrete fix)
        if action:
            root = proposal.root_cause.strip()
            if root:
                return f"{root}\n\n建议操作：{action}"
            return action

    # Priority 4: Fallback: use root_cause + predicted_impact (least actionable)
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

            # SAFETY CHECK 1: Is this a builtin/system skill?
            if self._is_builtin_skill(skill_name):
                logger.error(
                    "SkillExperienceWriter: rejecting write to builtin/system skill '%s'",
                    skill_name,
                )
                return ApplyRecord(
                    proposal_id=proposal.proposal_id,
                    target_type=ProposalTargetType.SKILL,
                    target_store=TargetStore.SKILL_EXPERIENCE_STORE,
                    status=ApplyStatus.SKIPPED,
                    reason=(
                        f"skill '{skill_name}' is a builtin/system skill "
                        "and cannot be modified by evolution."
                    ),
                    applier_name=self.name,
                )

            # SAFETY CHECK 2: Does this skill already exist in user workspace?
            # CRITICAL: Cannot create new skills - only modify existing ones
            skill_dir = self._skills_dir / skill_name
            if not skill_dir.exists() or not skill_dir.is_dir():
                logger.error(
                    "SkillExperienceWriter: skill '%s' does NOT exist in user workspace "
                    "(skills_dir=%s). Cannot create new skills, only modify existing ones.",
                    skill_name,
                    self._skills_dir,
                )
                return ApplyRecord(
                    proposal_id=proposal.proposal_id,
                    target_type=ProposalTargetType.SKILL,
                    target_store=TargetStore.SKILL_EXPERIENCE_STORE,
                    status=ApplyStatus.SKIPPED,
                    reason=(
                        f"skill '{skill_name}' does not exist in user workspace "
                        f"(skills_dir={self._skills_dir}). "
                        "Evolution can only modify EXISTING skills, "
                        "cannot create new skills. "
                        "This proposal might have hallucinated skill name."
                    ),
                    applier_name=self.name,
                )

            # Skill exists - proceed with modification (no mkdir needed)
            evolution_path = skill_dir / "evolutions.json"

            # Read existing log or create a fresh one
            evolution_log = self._load_or_create_log(
                evolution_path, skill_name
            )

            # Check for ExperienceOperation in metadata
            operations_raw = proposal.metadata.get("operations", [])
            record = None  # Initialize to avoid UnboundLocalError

            if operations_raw:
                # Dispatch per operation — PDA-style governance-aware pipeline
                for op_dict in operations_raw:
                    op = ExperienceOperation(**op_dict)
                    match op.op:
                        case ExperienceOperationType.ADD:
                            record = self._apply_add(evolution_log, proposal, op)
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
                evolution_log["entries"].append(record)

            # Update timestamp (dict format)
            evolution_log["updated_at"] = datetime.now(timezone.utc).isoformat()

            # Write back (dict format)
            evolution_path.write_text(
                json.dumps(evolution_log, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            # Render using openjiuwen.EvolutionStore (proper experience management)
            # Let ImportError propagate if openjiuwen is not installed
            from openjiuwen.agent_evolving.checkpointing import EvolutionStore

            # Create EvolutionStore instance
            oj_store = EvolutionStore(str(self._skills_dir))

            # Render Skill.md index + evolution/*.md files
            await oj_store.render_evolution_markdown(skill_name)
            logger.info(
                "SkillExperienceWriter: rendered markdown using EvolutionStore for '%s'",
                skill_name,
            )
            self._refresh_runtime_skill_state(skill_name)
            logger.info(_EFFECTIVE_AFTER_RELOAD_MESSAGE)

            # Log the operation (handle record being None for non-ADD operations)
            if record:
                logger.info(
                    "SkillExperienceWriter: appended record %s → %s",
                    record.get("id", "unknown"),
                    evolution_path,
                )
                record_id = record.get("id", "unknown")
            else:
                logger.info(
                    "SkillExperienceWriter: applied operations to %s",
                    evolution_path,
                )
                record_id = f"ops-{proposal.proposal_id[:8]}"

            return ApplyRecord(
                proposal_id=proposal.proposal_id,
                target_type=ProposalTargetType.SKILL,
                target_store=TargetStore.SKILL_EXPERIENCE_STORE,
                target_id=str(evolution_path),
                status=ApplyStatus.APPLIED,
                stored_object_id=str(evolution_path),
                reason=(
                    f"EvolutionRecord {record_id} applied to "
                    f"{skill_name}/evolutions.json. "
                    f"{_EFFECTIVE_AFTER_RELOAD_MESSAGE}"
                ),
                applier_name=self.name,
            )
        except ImportError:
            # Let ImportError propagate - user must install openjiuwen
            raise
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
        """Load an existing evolution log or create an empty one.

        Uses dict format (no openjiuwen dependency).
        """
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                # Return dict format directly
                return {
                    "skill_id": data.get("skill_id", skill_id),
                    "version": data.get("version", "1.0.0"),
                    "entries": data.get("entries", []),
                    "created_at": data.get("created_at"),
                    "updated_at": data.get("updated_at"),
                }
            except Exception as exc:
                logger.warning(
                    "Failed to parse existing evolutions.json for %s "
                    "(will create new): %s",
                    skill_id,
                    exc,
                )

        # Create new log dict
        return {
            "skill_id": skill_id,
            "version": "1.0.0",
            "entries": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _build_record(proposal: Proposal):
        """Build an evolution record dict from a Proposal.

        Uses EXISTING format compatible with openjiuwen EvolutionStore:
        - change: nested structure (section, action, content, target)
        - usage_stats: nested structure for statistics
        - applied: boolean flag
        """
        content = _build_content(proposal)

        # Build record using EXISTING format (benchmark/skills/json-validator/evolutions.json)
        record = {
            "id": f"exp-{proposal.proposal_id[:8]}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            "source": "ahe_evolution",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "context": proposal.root_cause.strip() or f"Auto-generated from AHE proposal: {proposal.proposal_id}",
            "change": {
                "section": _DEFAULT_SECTION,
                "action": _DEFAULT_ACTION,
                "content": content,
                "target": "body",
            },
            "applied": True,
            "score": float(proposal.metadata.get("max_score", 0.6)),
            "usage_stats": {
                "times_presented": 0,
                "times_used": 0,
                "times_positive": 0,
                "times_negative": 0,
                "last_presented_at": None,
                "last_evaluated_at": None,
            },
            "summary": content[:300] if content else None,
            "proposal_id": proposal.proposal_id,
            "evidence": [
                {
                    "trace_id": e.trace_id,
                    "description": e.description,
                }
                for e in proposal.failure_evidence
            ],
        }

        return record

    def _is_builtin_skill(self, skill_name: str) -> bool:
        """Check if skill_name is a builtin/system skill.

        System skills are protected and cannot be modified by evolution.
        Only USER skills in workspace/skills can be modified.

        Args:
            skill_name: Skill name to check.

        Returns:
            True if skill is a builtin/system skill (protected).
        """
        # Check against builtin skills from package resources
        try:
            from jiuwenswarm.common.utils import get_builtin_skills_dir
            builtin_dir = get_builtin_skills_dir()
            if builtin_dir.exists():
                builtin_skills = {item.name for item in builtin_dir.iterdir() if item.is_dir()}
                return skill_name in builtin_skills
        except Exception as exc:
            logger.warning("SkillExperienceWriter._is_builtin_skill check failed: %s", exc)

        return False

    # ── PDA ExperienceOperation handlers ──────────────────────────────

    def _apply_add(
        self, evolution_log, proposal: Proposal, op: ExperienceOperation
    ) -> dict:
        """ADD a new experience entry using EXISTING format.

        Uses EXISTING format compatible with openjiuwen EvolutionStore:
        - change: nested structure
        - usage_stats: nested structure
        - applied: boolean flag

        Returns:
            The created record dict for logging purposes.
        """
        content = op.new_content or _build_content(proposal)

        # Build record using EXISTING format
        record = {
            "id": f"exp-{proposal.proposal_id[:8]}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            "source": "ahe_evolution",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "context": op.reason or f"AHE evolution proposal: {proposal.proposal_id}",
            "change": {
                "section": _DEFAULT_SECTION,
                "action": _DEFAULT_ACTION,
                "content": content,
                "target": "body",
            },
            "applied": True,
            "score": float(proposal.metadata.get("max_score", 0.6)),
            "usage_stats": {
                "times_presented": 0,
                "times_used": 0,
                "times_positive": 0,
                "times_negative": 0,
                "last_presented_at": None,
                "last_evaluated_at": None,
            },
            "summary": content[:200] if content else None,
            "proposal_id": proposal.proposal_id,
            "evidence": [
                {
                    "trace_id": e.trace_id,
                    "description": e.description,
                }
                for e in op.evidence_refs
            ],
        }

        evolution_log["entries"].append(record)
        return record  # Return for logging in outer scope

    def _apply_merge(
        self, evolution_log, proposal: Proposal, op: ExperienceOperation
    ) -> None:
        """MERGE evidence_refs into an existing entry.

        Preserves EXISTING format - does not modify change structure.
        """
        target_id = op.target_experience_id
        for entry in evolution_log.get("entries", []):
            if entry.get("id") == target_id:
                # Merge evidence into existing list
                existing_evidence = entry.get("evidence", [])
                existing_evidence.extend([
                    {
                        "trace_id": e.trace_id,
                        "description": e.description,
                    }
                    for e in op.evidence_refs
                ])
                entry["evidence"] = existing_evidence

                # Record merge metadata
                if "metadata" not in entry:
                    entry["metadata"] = {}
                entry["metadata"]["merged_from"] = proposal.proposal_id

                logger.info("MERGE evidence to %s (%d refs)", target_id, len(op.evidence_refs))
                return
        logger.warning("MERGE target %s not found", target_id)

    def _apply_replace(
        self, evolution_log, proposal: Proposal, op: ExperienceOperation
    ) -> None:
        """REPLACE an existing entry's content using EXISTING format.

        Modifies change.content (nested structure) to maintain compatibility
        with openjiuwen EvolutionStore.
        """
        target_id = op.target_experience_id
        new_content = op.new_content or _build_content(proposal)
        for entry in evolution_log.get("entries", []):
            if entry.get("id") == target_id:
                # Modify change.content (EXISTING format)
                if "change" in entry:
                    entry["change"]["content"] = new_content
                else:
                    # Fallback: create change structure if missing
                    entry["change"] = {
                        "section": entry.get("section", _DEFAULT_SECTION),
                        "action": entry.get("action", _DEFAULT_ACTION),
                        "content": new_content,
                        "target": entry.get("target", "body"),
                    }

                # Clear flat fields if they exist (legacy cleanup)
                entry.pop("content", None)
                entry.pop("section", None)
                entry.pop("action", None)
                entry.pop("target", None)
                entry["applied"] = True

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
        """DEPRECATE an existing entry.

        Sets metadata.state to "deprecated" while preserving EXISTING format.
        """
        target_id = op.target_experience_id
        for entry in evolution_log.get("entries", []):
            if entry.get("id") == target_id:
                # Add metadata for state tracking (new field, compatible)
                if "metadata" not in entry:
                    entry["metadata"] = {}
                entry["metadata"]["state"] = "deprecated"
                entry["metadata"]["deprecated_by"] = proposal.proposal_id
                entry["metadata"]["deprecated_at"] = datetime.now(timezone.utc).isoformat()
                entry["applied"] = True

                logger.info("DEPRECATE %s", target_id)
                return
        logger.warning("DEPRECATE target %s not found", target_id)

    def _refresh_runtime_skill_state(self, skill_name: str) -> None:
        """Best-effort refresh after writing evolutions.

        Rendering updates files on disk. Runtime agents may still hold derived
        skill indexes or SkillUtil caches, so refresh the JiuwenSwarm indexes and
        call known openjiuwen cache hooks when available. Missing hooks are
        ignored because this writer must also work in CLI/test environments.
        """
        try:
            from jiuwenswarm.server.runtime.skill.skill_manager import SkillManager

            manager = SkillManager(workspace_dir=str(self._skills_dir.parent))
            refresh = getattr(manager, "_refresh_agent_data_indexes", None)
            if callable(refresh):
                refresh()
        except Exception as exc:
            logger.debug(
                "SkillExperienceWriter: runtime skill index refresh skipped for %s: %s",
                skill_name,
                exc,
            )

        try:
            import importlib

            skills_mod = importlib.import_module("openjiuwen.core.single_agent.skills")
            for hook_name in (
                "clear_skill_cache",
                "clear_cache",
                "invalidate_skill_cache",
                "reload_skills",
            ):
                hook = getattr(skills_mod, hook_name, None)
                if callable(hook):
                    try:
                        hook()
                    except TypeError:
                        hook(skill_name)
                    logger.info(
                        "SkillExperienceWriter: called openjiuwen skill refresh hook %s",
                        hook_name,
                    )
                    break
        except Exception as exc:
            logger.debug(
                "SkillExperienceWriter: openjiuwen skill cache refresh skipped for %s: %s",
                skill_name,
                exc,
            )

