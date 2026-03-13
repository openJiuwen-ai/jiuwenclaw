# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""SkillOptimizer — Optimizer driving Skill online evolution."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openjiuwen.agent_evolving.optimizer.base import BaseOptimizer
from openjiuwen.agent_evolving.trajectory.types import Updates as BaseUpdates

from jiuwenclaw.evolution.manager import SkillEvolutionManager, build_conversation_snippet
from jiuwenclaw.evolution.schema import EvolutionSignal
from jiuwenclaw.utils import logger

# Updates type: {(op_id, target): Dict[skill_name, EvolutionEntry]}
Updates = Dict[Tuple[str, str], Dict[str, Any]]


class SkillOptimizer(BaseOptimizer):
    """Optimizer driving Skill online evolution.

    Inherits from openjiuwen.agent_evolving.optimizer.base.BaseOptimizer

    Args:
        llm: openJiuwen Model instance (used for LLM calls in generate phase).
        skills_base_dir: Skill root directory (e.g. "workspace/agent/skills").
        auto_scan: whether to auto fire-and-forget scan after invoke (default False).
    """

    domain: str = "skill"

    def __init__(
        self,
        llm: Any,
        model: str,
        skills_base_dir: str = "workspace/agent/skills",
        auto_scan: bool = False,
    ) -> None:
        super().__init__()
        self._llm = llm
        self._skills_base_dir = skills_base_dir
        self.auto_scan = auto_scan
        self._model = model
        self._manager = SkillEvolutionManager(llm=llm, skills_base_dir=skills_base_dir, model=model)

        # Operator pipeline internal state (for async flow)
        self._gradient: Dict[str, Any] = {}    # Store signals and target Skill list

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def evolution_manager(self) -> SkillEvolutionManager:
        """Expose internal Manager (for ReActAgentEvolve._get_skill_evolution_manager() access)."""
        return self._manager

    @property
    def skills_base_dir(self) -> str:
        return self._skills_base_dir

    @staticmethod
    def default_targets() -> List[str]:
        return ["skill_content"]

    # ------------------------------------------------------------------
    # Operator pipeline interface (aligned with v2)
    # ------------------------------------------------------------------

    def bind(
        self,
        operators: Optional[Dict[str, Any]] = None,
        targets: Optional[List[str]] = None,
        **config,
    ) -> int:
        """Bind SkillCallOperator, return number of successful binds.

        Args:
            operators: { "skill_call": SkillCallOperator }
            targets:   Tunable parameter list, default ["skill_content"]
            **config:  Additional config (ignored, for BaseOptimizer compatibility)
        """
        # Use base class bind for filtering and binding
        count = super().bind(operators, targets, **config)
        logger.info("[SkillOptimizer] bind: bound %d operators", count)
        return count

    def signal_backward(
        self,
        signals: List[EvolutionSignal],
        target_skills: Optional[List[str]] = None,
    ) -> None:
        """Store signals to gradient (no LLM, pure storage).

        Args:
            signals:       EvolutionSignal list returned by scan().
            target_skills: Target Skill name list for this evolution (explicitly specified).
                           When None, auto-infer from signals.skill_name.
        """
        self._gradient["signals"] = list(signals)
        if target_skills:
            self._gradient["target_skills"] = list(target_skills)
        logger.info(
            "[SkillOptimizer] signal_backward: %d signals, target_skills=%s",
            len(signals),
            target_skills,
        )

    async def async_step(self) -> Updates:
        """LLM generates EvolutionEntry, return Updates dict (async, online path).

        Updates format:
          { ("skill_call", "skill_content"): {skill_name: EvolutionEntry} }

        Generate one entry per Skill in target_skills order;
        Return empty {} when no signals or no bound operator.

        Side effects:
            Clears _gradient after call.
        """
        signals: List[EvolutionSignal] = self._gradient.get("signals", [])
        target_skills: List[str] = self._gradient.get("target_skills", [])
        self._gradient.clear()

        if not signals or not self._operators:
            logger.info("[SkillOptimizer] async_step: no signals or no bound operator, skip")
            return {}

        op = self._operators.get("skill_call")
        if op is None:
            logger.warning("[SkillOptimizer] async_step: skill_call operator not found")
            return {}

        # Determine Skill list to evolve
        if target_skills:
            skills_to_evolve = target_skills
        else:
            # Auto-infer from signal attribution (deduplicate, preserve order)
            seen: set = set()
            skills_to_evolve = []
            for s in signals:
                if s.skill_name and s.skill_name not in seen:
                    seen.add(s.skill_name)
                    skills_to_evolve.append(s.skill_name)

        if not skills_to_evolve:
            logger.info("[SkillOptimizer] async_step: no target Skills, skip")
            return {}

        result_entries: Dict[str, Any] = {}  # {skill_name: EvolutionEntry}

        for skill_name in skills_to_evolve:
            # Only process signals with explicit skill attribution
            attributed = [s for s in signals if s.skill_name == skill_name]
            if not attributed:
                continue

            skill_content = op.get_skill_content(skill_name)
            try:
                entry = await self._manager.generate(skill_name, attributed, skill_content)
            except Exception as exc:
                logger.error(
                    "[SkillOptimizer] generate failed (skill=%s): %s", skill_name, exc
                )
                continue

            if entry is not None:
                result_entries[skill_name] = entry
                logger.info(
                    "[SkillOptimizer] async_step: generated entry %s -> skill=%s [%s]",
                    entry.id,
                    skill_name,
                    entry.change.section,
                )

        if not result_entries:
            return {}

        # Format aligned with ToolCallOperator's value routing pattern
        return {("skill_call", "skill_content"): result_entries}

    # ------------------------------------------------------------------
    # High-level methods (direct call, skip bind/step pipeline)
    # ------------------------------------------------------------------

    async def evolve(self, skill_name: str, messages: List[dict]) -> str:
        """Manually trigger evolution (/evolve high-level interface, skip bind/step pipeline).

        Flow: scan -> generate -> append

        Returns:
            Evolution result description string (display to user).
        """
        signals = self._manager.scan(messages)
        if not signals:
            return (
                f"No clear evolution signals in current conversation (no tool failures, no user corrections).\n"
                f"To manually record experience, describe the issue and I will update Skill '{skill_name}'."
            )

        skill_content = self._read_skill_content(skill_name)
        conversation_snippet = build_conversation_snippet(messages)
        entry = await self._manager.generate(
            skill_name,
            signals,
            skill_content,
            conversation_snippet=conversation_snippet or None,
        )
        logger.info("[SkillOptimizer] evolve: generate entry=%s", entry.id if entry else None)
        if entry is None:
            return (
                f"Evolution signals analyzed, but LLM determined Skill '{skill_name}' "
                f"already contains relevant content."
            )

        self._manager.append_entry(skill_name, entry)
        logger.info("[SkillOptimizer] evolve: appended entry=%s", entry.id)
        return (
            f"Evolution experience recorded to Skill '{skill_name}':\n"
            f"  **[{entry.change.section}]** {entry.change.content[:200]}\n\n"
            f"(evolutions.json updated, effective next conversation; "
            f"use `/solidify {skill_name}` to固化 to SKILL.md)"
        )

    async def evolve_generate(
        self, skill_name: str, messages: List[dict]
    ) -> "Optional[Any]":
        """Generate-only version of manual evolution: scan + generate, no write to evolutions.json.

        Returns:
            EvolutionEntry or None (no signals / LLM thinks no changes needed).
        """
        signals = self._manager.scan(messages)
        if not signals:
            return None
        skill_content = self._read_skill_content(skill_name)
        conversation_snippet = build_conversation_snippet(messages)
        return await self._manager.generate(
            skill_name,
            signals,
            skill_content,
            conversation_snippet=conversation_snippet or None,
        )

    async def auto_scan_generate(
        self,
        messages: List[dict],
        operators: Dict[str, Any],
        skill_names: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Generate-only version of auto-scan: returns {skill_name: EvolutionEntry}, no file writes.

        Args:
            messages:    Current session message list (dict format).
            operators:   { "skill_call": SkillCallOperator }.
            skill_names: Registered Skill name list.

        Returns:
            { skill_name: EvolutionEntry } -- only contains skills that successfully generated entries.
        """
        op = operators.get("skill_call")
        if op is None or not skill_names:
            return {}

        skill_dir_map = {
            name: str(op.skills_base_dir / name / "SKILL.md")
            for name in skill_names
            if op.skill_exists(name)
        }
        signals = self._manager.scan(messages, skill_dir_map=skill_dir_map)
        if not signals:
            logger.debug("[SkillOptimizer] auto_scan_generate: no signals, silent exit")
            return {}

        results: Dict[str, Any] = {}
        for skill_name in skill_names:
            attributed = [s for s in signals if s.skill_name == skill_name]
            if not attributed:
                continue
            try:
                skill_content = op.get_skill_content(skill_name)
                conversation_snippet = build_conversation_snippet(messages)
                entry = await self._manager.generate(
                    skill_name,
                    attributed,
                    skill_content,
                    conversation_snippet=conversation_snippet or None,
                )
                if entry is not None:
                    results[skill_name] = entry
                    logger.info(
                        "[SkillOptimizer] auto_scan_generate: generated entry %s -> skill=%s",
                        entry.id,
                        skill_name,
                    )
            except Exception as exc:
                logger.warning(
                    "[SkillOptimizer] auto_scan_generate: error processing skill=%s: %s",
                    skill_name,
                    exc,
                )
        return results

    def solidify(self, skill_name: str) -> str:
        """Solidify pending experience into SKILL.md."""
        count = self._manager.solidify(skill_name)
        if count == 0:
            return f"Skill '{skill_name}' has no pending evolution experience to solidify."
        return f"Solidified {count} evolution experience entries to Skill '{skill_name}' SKILL.md."

    def list_summary(self, skill_names: List[str]) -> str:
        """List all Skill evolution records (for /evolve list command)."""
        return self._manager.list_pending_summary(skill_names)

    # ------------------------------------------------------------------
    # BaseOptimizer abstract method implementations
    # ------------------------------------------------------------------

    def _step(self) -> BaseUpdates:
        """Sync step method implementing BaseOptimizer interface.

        Uses asyncio.run() to call async async_step().
        """
        return asyncio.run(self.async_step())

    def _backward(self, evaluated_cases: List[Any]) -> None:
        """Backward method implementing BaseOptimizer interface.

        Extracts signals from evaluated_cases and stores to gradient.
        Used for offline training scenarios.

        Args:
            evaluated_cases: List of evaluated cases with score and reason
        """
        if not evaluated_cases:
            return

        # Extract signals from failed cases
        signals = []
        target_skills = set()

        for case in evaluated_cases:
            # Only process failed cases (score == 0)
            if hasattr(case, "score") and case.score == 0:
                # Extract signal from reason or answer
                reason = getattr(case, "reason", "") or ""
                answer = getattr(case, "answer", None)

                # Build signal content
                excerpt = reason or str(answer) if answer else ""
                if not excerpt:
                    continue

                # Try to extract skill_name from answer
                skill_name = None
                if answer and isinstance(answer, dict):
                    skill_name = answer.get("skill_name")

                # Try to extract skill_name from case inputs
                if not skill_name and hasattr(case, "inputs"):
                    inputs = case.inputs
                    if isinstance(inputs, dict):
                        skill_name = inputs.get("skill_name")

                signal = EvolutionSignal(
                    type="execution_failure",
                    section="troubleshooting",
                    excerpt=excerpt[:500],  # Limit length
                    skill_name=skill_name,
                )
                signals.append(signal)
                if skill_name:
                    target_skills.add(skill_name)

        if signals:
            self.signal_backward(
                signals,
                list(target_skills) if target_skills else None,
            )
            logger.info(
                "[SkillOptimizer] _backward: extracted %d signals from %d evaluated cases",
                len(signals),
                len(evaluated_cases),
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_skill_content(self, skill_name: str) -> str:
        skill_md = Path(self._skills_base_dir) / skill_name / "SKILL.md"
        if skill_md.is_file():
            try:
                return skill_md.read_text(encoding="utf-8")
            except Exception:
                pass
        return ""
