# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Skill experience Apply writer.

Writes active Skill Proposals directly into the target skill's ``SKILL.md``
under a ``## Troubleshooting`` section. The agent runtime reads ``SKILL.md``
directly, so the change takes effect in newly created sessions or after
agent reload — no separate experience store, render layer, or runtime index
refresh is needed.

Append-only: this writer never rewrites existing prompt prose; it only
appends experience content under the troubleshooting section.
"""

from __future__ import annotations

import logging
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

_DEFAULT_SECTION = "Troubleshooting"
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


def _append_experience_section(
    skill_md_text: str, section: str, content: str
) -> str:
    """Append ``content`` under ``## {section}`` in the skill markdown.

    Append-only: existing prose is never rewritten. If the section already
    exists, content is appended at the end of that section (before the next
    ``## `` heading, or at end of file). If the section does not exist, it is
    created at the end of the file.
    """
    heading = f"## {section}"
    lines = skill_md_text.splitlines()

    section_start = None
    section_end = len(lines)
    for i, line in enumerate(lines):
        if line.strip() == heading:
            section_start = i
            continue
        if section_start is not None and line.lstrip().startswith("## "):
            # First heading after the section → section ends here.
            section_end = i
            break

    block = [heading, "", content, ""] if section_start is None else ["", content, ""]

    if section_start is None:
        # Section not present: ensure a blank separator then append section.
        if lines and lines[-1].strip() != "":
            lines.append("")
        lines.extend(block)
        return "\n".join(lines) + "\n"

    # Section present: insert content before the next heading (or EOF).
    new_lines = lines[:section_end] + block + lines[section_end:]
    return "\n".join(new_lines) + "\n"


@apply_writers.register("skill_writer")
class SkillExperienceWriter(ApplyWriter):
    """Write Skill Proposals as an experience section in ``{skill_dir}/SKILL.md``.

    The agent runtime reads ``SKILL.md`` directly, so no separate experience
    store, render step, or runtime index refresh is required. Append-only.
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
            # CRITICAL: Cannot create new skills - only modify existing ones.
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

            # Skill exists — append experience to SKILL.md (append-only).
            skill_md_path = skill_dir / "SKILL.md"
            existing = (
                skill_md_path.read_text(encoding="utf-8")
                if skill_md_path.exists()
                else ""
            )
            content = _build_content(proposal)
            updated = _append_experience_section(
                existing, _DEFAULT_SECTION, content
            )
            skill_md_path.write_text(updated, encoding="utf-8")
            logger.info(
                "SkillExperienceWriter: appended experience to '%s' under '%s'",
                skill_md_path,
                _DEFAULT_SECTION,
            )
            logger.info(_EFFECTIVE_AFTER_RELOAD_MESSAGE)

            return ApplyRecord(
                proposal_id=proposal.proposal_id,
                target_type=ProposalTargetType.SKILL,
                target_store=TargetStore.SKILL_EXPERIENCE_STORE,
                target_id=str(skill_md_path),
                status=ApplyStatus.APPLIED,
                stored_object_id=str(skill_md_path),
                reason=(
                    f"Experience appended to {skill_name}/SKILL.md "
                    f"under '{_DEFAULT_SECTION}'. "
                    f"{_EFFECTIVE_AFTER_RELOAD_MESSAGE}"
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

    def _is_builtin_skill(self, skill_name: str) -> bool:
        """Check if skill_name is a builtin/system skill.

        System skills are protected and cannot be modified by evolution.
        Only USER skills in workspace/skills can be modified.
        """
        try:
            from jiuwenswarm.common.utils import get_builtin_skills_dir

            builtin_dir = get_builtin_skills_dir()
            if builtin_dir.exists():
                builtin_skills = {
                    item.name for item in builtin_dir.iterdir() if item.is_dir()
                }
                return skill_name in builtin_skills
        except Exception as exc:
            logger.warning("SkillExperienceWriter._is_builtin_skill check failed: %s", exc)

        return False
