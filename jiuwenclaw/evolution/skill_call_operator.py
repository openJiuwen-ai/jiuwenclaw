# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""SkillCallOperator — manages all Skills as a single operator."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from openjiuwen.core.operator.base import Operator, TunableSpec
from openjiuwen.core.session.agent import Session
from jiuwenclaw.utils import logger


class SkillCallOperator(Operator):
    """Manages all Skills as a single operator (aligned with ToolCallOperator pattern).

    Inherits from openjiuwen.core.operator.base.Operator

    Args:
        skills_base_dir: Root directory for Skills (each subdir corresponds to a Skill).
        evolution_manager: SkillEvolutionManager instance (optional).
            Required for write path (set_parameter) and read path (get_*) to work.
    """

    # Fixed identifier, unique
    OPERATOR_ID = "skill_call"

    def __init__(
        self,
        skills_base_dir: str,
        evolution_manager: Optional[Any] = None,
    ) -> None:
        self.skills_base_dir = Path(skills_base_dir)
        self.evolution_manager = evolution_manager

    # ------------------------------------------------------------------
    # Operator identity
    # ------------------------------------------------------------------

    @property
    def operator_id(self) -> str:
        return self.OPERATOR_ID

    # ------------------------------------------------------------------
    # Tunable parameters (for SkillOptimizer bind/step)
    # ------------------------------------------------------------------

    def get_tunables(self) -> Dict[str, TunableSpec]:
        """Return tunable parameter descriptions."""
        return {
            "skill_content": TunableSpec(
                name="skill_content",
                kind="prompt",
                path="skill_content",
            ),
            "new_skill": TunableSpec(
                name="new_skill",
                kind="prompt",
                path="new_skill",
            ),
        }

    def set_parameter(self, target: str, value: Any) -> None:
        """Route Optimizer updates to specific Skills (aligned with ToolCallOperator).

        Args:
            target: "skill_content" | "new_skill"
            value:
                skill_content -> Dict[skill_name, EvolutionEntry]
                                e.g. {"weather-check": entry}
                new_skill     -> Dict[skill_name, content_str]
                                e.g. {"search-skill": "# Search Skill\\n..."}
        """
        if not isinstance(value, dict):
            logger.warning(
                "[SkillCallOperator] set_parameter value should be dict, got: %s",
                type(value).__name__,
            )
            return

        if target == "skill_content":
            self._apply_skill_content(value)

        elif target == "new_skill":
            self._apply_new_skill(value)

        else:
            logger.debug("[SkillCallOperator] ignoring unknown target: %s", target)

    # ------------------------------------------------------------------
    # State management (Operator base class interface)
    # ------------------------------------------------------------------

    def get_state(self) -> Dict[str, Any]:
        """Get current state for rollback, snapshot, or version comparison."""
        return {
            "operator_id": self.operator_id,
            "skills_base_dir": str(self.skills_base_dir),
        }

    def load_state(self, state: Dict[str, Any]) -> None:
        """Restore operator state from serialized dict."""
        if "skills_base_dir" in state:
            self.skills_base_dir = Path(state["skills_base_dir"])

    async def invoke(
        self,
        inputs: Dict[str, Any],
        session: Session,
        **kwargs: Any,
    ) -> Any:
        """Execute skill content retrieval (management operator, not execution).

        Returns merged skill content for the skill system.

        Args:
            inputs: Input dict, expects "skill_name" key
            session: Session for tracing
            **kwargs: Additional parameters

        Returns:
            Dict with skill_name, content, and summary
        """
        skill_name = inputs.get("skill_name")
        if not skill_name:
            return {"error": "skill_name required in inputs"}

        self._set_operator_context(session, self.operator_id)
        try:
            content = self.get_merged_content(skill_name)
            return {
                "skill_name": skill_name,
                "content": content,
                "summary": self.get_evolution_summary(skill_name),
            }
        finally:
            self._set_operator_context(session, None)

    # ------------------------------------------------------------------
    # Read path (does not modify files)
    # ------------------------------------------------------------------

    def get_skill_content(self, skill_name: str) -> str:
        """Read SKILL.md raw content (without evolution merge)."""
        skill_dir = self.skills_base_dir / skill_name
        skill_md = skill_dir / "SKILL.md"
        if skill_md.is_file():
            try:
                return skill_md.read_text(encoding="utf-8")
            except Exception as exc:
                logger.warning("[SkillCallOperator] failed to read SKILL.md (%s): %s", skill_name, exc)
        # Fallback: any .md file
        md_files = sorted(skill_dir.glob("*.md"))
        if md_files:
            try:
                return md_files[0].read_text(encoding="utf-8")
            except Exception:
                pass
        return ""

    def get_merged_content(self, skill_name: str) -> str:
        """Return SKILL.md + pending evolution merged content (in-memory, no file change)."""
        base = self.get_skill_content(skill_name)
        if self.evolution_manager is not None:
            return self.evolution_manager.load_skill_with_evolution(skill_name, base)
        return base

    def get_evolution_summary(self, skill_name: str) -> str:
        """Return single Skill's pending evolution summary (Markdown)."""
        if self.evolution_manager is None:
            return ""
        return self.evolution_manager.get_evolution_summary(skill_name)

    def get_all_evolution_summaries(self, skill_names: List[str]) -> str:
        """Aggregate multiple Skills' pending evolution summaries.

        Args:
            skill_names: List of registered Skill names.

        Returns:
            Markdown summary string; returns "" when all Skills have no pending.
        """
        parts: List[str] = []
        for name in skill_names:
            summary = self.get_evolution_summary(name)
            if summary:
                parts.append(summary)
        return "\n".join(parts)

    def skill_exists(self, skill_name: str) -> bool:
        """Check if Skill directory exists."""
        return (self.skills_base_dir / skill_name).is_dir()

    # ------------------------------------------------------------------
    # Write path internal implementation
    # ------------------------------------------------------------------

    def _apply_skill_content(self, value: Dict[str, Any]) -> None:
        """Apply skill_content update: write EvolutionEntry to Skill's evolutions.json."""
        for skill_name, entry in value.items():
            if self.evolution_manager is None:
                logger.warning(
                    "[SkillCallOperator] evolution_manager not injected, cannot write (skill=%s)",
                    skill_name,
                )
                continue
            self.evolution_manager.append_entry(skill_name, entry)
            logger.info(
                "[SkillCallOperator] wrote evolution entry (skill=%s, section=%s)",
                skill_name,
                entry.change.section if hasattr(entry, "change") else "?",
            )

    def _apply_new_skill(self, value: Dict[str, str]) -> None:
        """Apply new_skill update: create new Skill directory and SKILL.md."""
        for skill_name, content in value.items():
            skill_dir = self.skills_base_dir / skill_name
            skill_md = skill_dir / "SKILL.md"
            try:
                skill_dir.mkdir(parents=True, exist_ok=True)
                if skill_md.exists():
                    logger.warning(
                        "[SkillCallOperator] SKILL.md already exists, skipping creation (skill=%s)", skill_name
                    )
                    continue
                skill_md.write_text(content, encoding="utf-8")
                logger.info("[SkillCallOperator] created new Skill: %s", skill_name)
            except Exception as exc:
                logger.error(
                    "[SkillCallOperator] failed to create Skill (%s): %s", skill_name, exc
                )

    def __repr__(self) -> str:
        return (
            f"SkillCallOperator(id={self.operator_id!r}, "
            f"base={self.skills_base_dir!r}, "
            f"has_manager={self.evolution_manager is not None})"
        )
