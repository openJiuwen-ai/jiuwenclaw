# Copyright (c) Huawei Technologies, Co., Ltd. 2026. All rights reserved.
"""ExperienceGovernor — governance context provider for PDA algorithm.

Pluggable: provides governance context before Propose and validates during
Decision. No dependency on RulePolicy/EvalPolicy. Other algorithms
(LLMProposer) never use this module.

Responsibilities:
1. Before Propose: provide GovernanceContext (existing experiences, capacity,
   allowed operations) so Proposer can generate governance-aware proposals.
2. During Decision: validate that operations are within allowed bounds.
3. Never post-modify Proposals — if MERGE/REPLACE is needed, it must be
   declared in Propose phase.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from jiuwenswarm.evolve.models import (
    ExperienceOperationType,
    ExperienceOperation,
)
from jiuwenswarm.evolve.ahe.models import GovernanceContext

logger = logging.getLogger(__name__)


class ExperienceGovernor:
    """Experience governance context provider.

    Usage:
        governor = ExperienceGovernor(skills_dir="/path/to/skills", max_per_skill=10)
        ctx = governor.get_context("bash-tool")
        # ctx tells Proposer what operations are allowed
        valid = governor.validate_operation("bash-tool", operation)
        # valid tells Decision if the operation is within bounds

    IMPORTANT: Only allows operations on USER skills that ALREADY EXIST in workspace.
    - BUILTIN/SYSTEM skills (in package resources) are PROTECTED
    - New skills cannot be created (no mkdir for non-existent skills)
    - Only existing skills in workspace/skills can be modified
    """

    def __init__(
        self,
        skills_dir: str | None = None,
        max_per_skill: int = 10,
    ) -> None:
        if skills_dir:
            self._skills_dir = Path(skills_dir)
        else:
            from jiuwenswarm.common.utils import get_user_workspace_dir
            self._skills_dir = (
                get_user_workspace_dir() / "agent" / "workspace" / "skills"
            )
        self._max_per_skill = max_per_skill
        self._builtin_skills = self._load_builtin_skills()
        self._user_skills = self._load_user_skills()

    def get_user_skill_names(self) -> set[str]:
        """Get the set of user skills that can be modified.

        Returns only skills that:
        1. Already exist in workspace/skills directory
        2. Are NOT builtin/system skills

        This is the whitelist of editable skills.
        """
        return self._user_skills

    def is_skill_editable(self, skill_name: str) -> bool:
        """Check if a skill name is editable (exists in user workspace and is not builtin).

        Args:
            skill_name: Skill name to check.

        Returns:
            True if skill exists in user workspace and can be modified.
        """
        # Must not be a builtin/system skill
        if self._is_builtin_skill(skill_name):
            return False

        # Must exist in user workspace skills directory
        return skill_name in self._user_skills

    def get_context(
        self,
        skill_name: str,
        query_hint: str | None = None,
    ) -> GovernanceContext:
        """Read skill's evolutions.json and build governance context.

        Args:
            skill_name: Target skill directory name.
            query_hint: Optional text describing the current problem,
                used for similarity detection.

        Returns:
            GovernanceContext with classification of existing experiences
            and allowed operations based on current state.

        CRITICAL SAFETY CHECKS:
        1. If skill is a BUILTIN/SYSTEM skill → NO operations allowed
        2. If skill does NOT EXIST in user workspace → NO operations allowed
           (cannot create new skills, only modify existing ones)
        """
        # Safety Check 1: Is this a builtin/system skill?
        if self._is_builtin_skill(skill_name):
            logger.warning(
                "ExperienceGovernor: skill '%s' is a builtin/system skill, "
                "no modifications allowed",
                skill_name,
            )
            return GovernanceContext(
                skill_name=skill_name,
                current_count=0,
                max_count=0,
                can_add=False,
                existing_experiences=[],
                similar_experiences=[],
                replaceable_experiences=[],
                protected_experiences=[],
                allowed_operations=[ExperienceOperationType.NOOP],
            )

        # Safety Check 2: Does this skill exist in user workspace?
        # Cannot create new skills - only modify existing ones
        skill_dir = self._skills_dir / skill_name
        if not skill_dir.exists() or not skill_dir.is_dir():
            logger.warning(
                "ExperienceGovernor: skill '%s' does NOT exist in user workspace "
                "(skills_dir=%s). Cannot create new skills, only modify existing ones. "
                "This might be a hallucination from diagnosis_result.",
                skill_name,
                self._skills_dir,
            )
            return GovernanceContext(
                skill_name=skill_name,
                current_count=0,
                max_count=0,
                can_add=False,
                existing_experiences=[],
                similar_experiences=[],
                replaceable_experiences=[],
                protected_experiences=[],
                allowed_operations=[ExperienceOperationType.NOOP],
            )

        evo_path = self._skills_dir / skill_name / "evolutions.json"
        entries = self._load_entries(evo_path)
        current_count = len(entries)

        # Classify experiences
        existing = self._summarize_entries(entries)
        similar = self._find_similar(entries, query_hint) if query_hint else []
        replaceable = self._find_replaceable(entries)
        protected = self._find_protected(entries)

        # Determine allowed operations
        can_add = current_count < self._max_per_skill
        allowed_ops = self._compute_allowed_operations(
            can_add, similar, replaceable, entries
        )

        return GovernanceContext(
            skill_name=skill_name,
            current_count=current_count,
            max_count=self._max_per_skill,
            can_add=can_add,
            existing_experiences=existing,
            similar_experiences=similar,
            replaceable_experiences=replaceable,
            protected_experiences=protected,
            allowed_operations=allowed_ops,
        )

    def validate_operation(
        self, skill_name: str, operation: ExperienceOperation
    ) -> bool:
        """Check if operation is within governance bounds.

        Called by AheDecisionPolicy during RuleGate phase.
        Returns False if:
        - skill is a BUILTIN/SYSTEM skill (PROTECTED)
        - op not in allowed_operations
        - ADD when can_add=False
        - REPLACE/MERGE targeting a protected or non-replaceable experience
        """
        # First check: is this a builtin/system skill?
        if self._is_builtin_skill(skill_name):
            logger.warning(
                "ExperienceGovernor: rejecting operation on builtin/system skill '%s'",
                skill_name,
            )
            return False

        ctx = self.get_context(
            skill_name,
            query_hint=operation.new_content if operation.new_content else None,
        )

        # Check allowed operations
        if operation.op not in ctx.allowed_operations:
            return False

        # ADD-specific: check capacity
        if operation.op == ExperienceOperationType.ADD and not ctx.can_add:
            return False

        # Target-specific: check replaceable/mergeable
        if operation.op == ExperienceOperationType.REPLACE:
            if operation.target_experience_id not in [
                e.get("id", "") for e in ctx.replaceable_experiences
            ]:
                return False

        if operation.op == ExperienceOperationType.DEPRECATE:
            # Cannot deprecate protected experiences
            if operation.target_experience_id in ctx.protected_experiences:
                return False

        return True

    # ── Internal helpers ──────────────────────────────────────────────

    @staticmethod
    def _load_builtin_skills() -> set[str]:
        """Load the set of builtin/system skill names from package resources.

        Builtin skills are protected and cannot be modified by evolution.
        """
        try:
            from jiuwenswarm.common.utils import get_builtin_skills_dir
            builtin_dir = get_builtin_skills_dir()
            if not builtin_dir.exists():
                return set()
            return {item.name for item in builtin_dir.iterdir() if item.is_dir()}
        except Exception as exc:
            logger.warning("ExperienceGovernor._load_builtin_skills failed: %s", exc)
            return set()

    def _load_user_skills(self) -> set[str]:
        """Load the set of user skills that exist in workspace/skills directory.

        These are the ONLY skills that can be modified by evolution.
        New skills cannot be created - only existing ones can be modified.

        Filtering rules:
        1. Skill must exist as a directory in workspace/skills
        2. Skill must NOT be a builtin/system skill
        """
        if not self._skills_dir.exists():
            return set()

        user_skills = set()
        for item in self._skills_dir.iterdir():
            if item.is_dir():
                # Filter: Not a builtin skill
                if item.name not in self._builtin_skills:
                    user_skills.add(item.name)

        return user_skills

    def _is_builtin_skill(self, skill_name: str) -> bool:
        """Check if skill_name is a builtin/system skill.

        Args:
            skill_name: Skill name to check.

        Returns:
            True if skill is a builtin/system skill (protected).
        """
        # Check against builtin skills list
        return skill_name in self._builtin_skills

    @staticmethod
    def _load_entries(evo_path: Path) -> list[dict]:
        """Load entries from evolutions.json."""
        if not evo_path.exists():
            return []
        try:
            data = json.loads(evo_path.read_text(encoding="utf-8"))
            return data.get("entries", [])
        except (json.JSONDecodeError, TypeError, OSError) as exc:
            logger.warning("ExperienceGovernor._load_entries failed: %s", exc)
            return []

    @staticmethod
    def _summarize_entries(entries: list[dict]) -> list[dict[str, Any]]:
        """Create brief summaries of each experience entry."""
        summaries = []
        for entry in entries:
            change = entry.get("change", {})
            summaries.append({
                "id": entry.get("id", ""),
                "content": change.get("content", "")[:100],
                "state": entry.get("metadata", {}).get("state", "active"),
                "section": change.get("section", ""),
                "hit_count": entry.get("metadata", {}).get("hit_count", 0),
            })
        return summaries

    @staticmethod
    def _find_similar(entries: list[dict], query_hint: str) -> list[dict]:
        """Find experiences with content similar to the query hint.

        Simple text similarity: check if query keywords appear in
        experience content.
        """
        if not query_hint:
            return []

        # Extract key words from query hint
        keywords = set(query_hint.lower().split())

        similar = []
        for entry in entries:
            change = entry.get("change", {})
            content = change.get("content", "").lower()
            # Check if any keyword appears in content
            overlap = sum(1 for kw in keywords if kw in content)
            if overlap >= 1:  # At least one keyword match
                similar.append({
                    "id": entry.get("id", ""),
                    "content": content[:200],
                    "overlap_count": overlap,
                })

        # Sort by overlap count (most similar first)
        similar.sort(key=lambda x: x.get("overlap_count", 0), reverse=True)
        return similar[:5]  # Top 5 similar experiences

    @staticmethod
    def _find_replaceable(entries: list[dict]) -> list[dict]:
        """Find candidate experiences that can be replaced.

        Replaceable criteria:
        - state = "candidate" (not yet proven effective)
        - hit_count = 0 (never used)
        """
        replaceable = []
        for entry in entries:
            metadata = entry.get("metadata", {})
            state = metadata.get("state", "active")
            hit_count = metadata.get("hit_count", 0)

            # Candidate experiences with low usage are replaceable
            if state == "candidate" and hit_count == 0:
                replaceable.append({
                    "id": entry.get("id", ""),
                    "state": state,
                    "hit_count": hit_count,
                    "content": entry.get("change", {}).get("content", "")[:100],
                })
        return replaceable

    @staticmethod
    def _find_protected(entries: list[dict]) -> list[str]:
        """Find experience IDs that should not be replaced or deprecated.

        Protected criteria:
        - state = "active" (proven effective)
        - hit_count > 0 (has been used successfully)
        """
        protected = []
        for entry in entries:
            metadata = entry.get("metadata", {})
            state = metadata.get("state", "active")
            hit_count = metadata.get("hit_count", 0)

            if state == "active" and hit_count > 0:
                protected.append(entry.get("id", ""))
        return protected

    @staticmethod
    def _compute_allowed_operations(
        can_add: bool,
        similar: list[dict],
        replaceable: list[dict],
        entries: list[dict],
    ) -> list[ExperienceOperationType]:
        """Compute which operation types are currently allowed."""
        allowed = []

        if can_add:
            allowed.append(ExperienceOperationType.ADD)

        if similar:
            allowed.append(ExperienceOperationType.MERGE)

        if replaceable:
            allowed.append(ExperienceOperationType.REPLACE)

        # UPDATE is always allowed (improving existing experiences is safe)
        if entries:
            allowed.append(ExperienceOperationType.UPDATE)

        # DEPRECATE is allowed if there are entries
        if entries:
            allowed.append(ExperienceOperationType.DEPRECATE)

        # NOOP is always allowed
        allowed.append(ExperienceOperationType.NOOP)

        return allowed
