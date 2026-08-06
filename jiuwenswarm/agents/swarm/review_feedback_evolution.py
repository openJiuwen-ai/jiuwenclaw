# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Route scheduled review feedback through member and global Skill scopes."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

from openjiuwen.agent_evolving.signal import (
    ConversationSignalDetector,
    ReviewFeedbackAction,
    ReviewFeedbackAttributor,
    ReviewFeedbackClassification,
    ReviewFeedbackContextBuilder,
    attribution_to_evolution_signal,
)

from jiuwenswarm.common.config import (
    get_evolution_review_feedback_min_confidence,
    get_skill_evolution_enabled,
)

logger = logging.getLogger(__name__)

_SAFE_MEMBER_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
_MAX_AGGREGATED_FEEDBACK_CHARS = 16_000


@dataclass(frozen=True)
class _TaskFeedbackObservation:
    """One task-level attribution retained for the terminal team pass."""

    task_id: str
    review_round: int
    assignee: str
    feedback: str
    skill_name: str
    signal: Any


@dataclass(frozen=True)
class _NewSkillPatternObservation:
    """One reusable no-existing-Skill pattern retained across task reviews."""

    task_id: str
    review_round: int
    assignee: str
    feedback: str
    reusable_guidance: str
    reason: str
    confidence: float
    action: ReviewFeedbackAction
    supporting_evidence: tuple[str, ...]


class SwarmReviewFeedbackEvolutionHandler:
    """Evolve assignee Skills per task, then aggregate into global Skills."""

    def __init__(
        self,
        *,
        channel_id: str | None,
        session_id: str,
        team_id: str,
        trajectory_registry: Any,
        config: dict[str, Any] | None,
    ) -> None:
        self._channel_id = channel_id
        self._session_id = session_id
        self._team_id = team_id
        self._trajectory_registry = trajectory_registry
        self._config = config
        self._processed: set[tuple[str, int, str]] = set()
        self._lock = asyncio.Lock()
        self._task_evolution_lock = asyncio.Lock()
        self._team_evolution_lock = asyncio.Lock()
        self._member_rails: dict[str, Any] = {}
        self._observations: list[_TaskFeedbackObservation] = []
        self._new_skill_patterns: list[_NewSkillPatternObservation] = []
        self._global_observation_cursor = 0
        self._new_skill_pattern_cursor = 0

    async def __call__(self, payload: dict[str, Any]) -> None:
        if not get_skill_evolution_enabled(self._config):
            return

        task_id = str(payload.get("task_id") or "").strip()
        review_round = int(payload.get("review_round") or 0)
        feedback = str(payload.get("feedback") or "").strip()
        assignee = str(payload.get("assignee") or "").strip()
        if not task_id or not feedback or not self._is_safe_member_name(assignee):
            if task_id and feedback:
                logger.warning(
                    "[ReviewFeedbackEvolution] task feedback skipped because assignee "
                    "is missing or unsafe: task=%s assignee=%r",
                    task_id,
                    assignee,
                )
            return
        key = (task_id, review_round, feedback)
        async with self._lock:
            if key in self._processed:
                return
            self._processed.add(key)

        global_rail = self._review_feedback_rail()
        if global_rail is None:
            logger.warning(
                "[ReviewFeedbackEvolution] no regular Skill sidecar rail: session=%s task=%s",
                self._session_id,
                task_id,
            )
            return

        # One handler owns all task-level mutations in the leader process.
        # Serializing this section keeps copy-on-write and shared model use
        # deterministic even when several task rounds fail close together.
        async with self._task_evolution_lock:
            member_rail = self._member_rail_for(assignee, global_rail)
            trajectory = self._get_member_trajectory(assignee)
            task_objective = "\n".join(
                part
                for part in (
                    str(payload.get("task_title") or "").strip(),
                    str(payload.get("task_content") or "").strip(),
                )
                if part
            )
            prior_pattern_evidence = tuple(
                self._format_new_skill_pattern_evidence(item)
                for item in self._new_skill_patterns
                if item.task_id != task_id
            )
            prior_pattern_task_count = len(
                {
                    item.task_id
                    for item in self._new_skill_patterns
                    if item.task_id != task_id
                }
            )
            context = await ReviewFeedbackContextBuilder(
                store=member_rail.evolution_store
            ).build(
                task_id=task_id,
                review_round=review_round,
                task_objective=task_objective,
                trajectory=trajectory,
                repetition_count=prior_pattern_task_count + 1,
                repeated_pattern_evidence=prior_pattern_evidence,
            )
            attributor = ReviewFeedbackAttributor(
                llm=global_rail.evolver.llm,
                model=global_rail.evolver.model,
                language=getattr(global_rail, "_language", "cn"),
            )
            attribution = await attributor.attribute(feedback, context=context)
            logger.info(
                "[ReviewFeedbackEvolution] task=%s assignee=%s round=%s action=%s "
                "classification=%s skill=%s confidence=%.2f reason=%s",
                task_id,
                assignee,
                review_round,
                attribution.action.value,
                attribution.classification.value,
                attribution.skill_name,
                attribution.confidence,
                attribution.reason,
            )

            threshold = get_evolution_review_feedback_min_confidence(self._config)
            if (
                attribution.classification
                == ReviewFeedbackClassification.NEW_SKILL_PATTERN
            ):
                if (
                    attribution.reusable_guidance
                    and attribution.confidence >= threshold
                ):
                    matching_patterns = self._matching_new_skill_patterns(
                        attribution.reusable_guidance,
                        exclude_task_id=task_id,
                    )
                    matching_evidence = tuple(
                        self._format_new_skill_pattern_evidence(item)
                        for item in matching_patterns
                    )
                    pattern_action = (
                        ReviewFeedbackAction.SUGGEST_NEW_SKILL
                        if (
                            attribution.action == ReviewFeedbackAction.SUGGEST_NEW_SKILL
                            and matching_patterns
                        )
                        else ReviewFeedbackAction.SKIP_UNATTRIBUTED
                    )
                    self._new_skill_patterns.append(
                        _NewSkillPatternObservation(
                            task_id=task_id,
                            review_round=review_round,
                            assignee=assignee,
                            feedback=feedback,
                            reusable_guidance=attribution.reusable_guidance,
                            reason=attribution.reason,
                            confidence=attribution.confidence,
                            action=pattern_action,
                            supporting_evidence=matching_evidence,
                        )
                    )
                return

            if attribution.action != ReviewFeedbackAction.EVOLVE_EXISTING_SKILL:
                return
            if attribution.confidence < threshold:
                logger.info(
                    "[ReviewFeedbackEvolution] actionable attribution below threshold: %.2f < %.2f",
                    attribution.confidence,
                    threshold,
                )
                return

            signal = attribution_to_evolution_signal(
                attribution,
                task_id=task_id,
                review_round=review_round,
            )
            if signal is None or not attribution.skill_name:
                return
            self._observations.append(
                _TaskFeedbackObservation(
                    task_id=task_id,
                    review_round=review_round,
                    assignee=assignee,
                    feedback=feedback,
                    skill_name=attribution.skill_name,
                    signal=signal,
                )
            )
            messages = (
                ConversationSignalDetector.convert_trajectory_to_messages(trajectory)
                if trajectory is not None
                else []
            )
            result = await member_rail.evolve_from_external_signals(
                signals=[signal],
                messages=messages,
                trajectory=trajectory,
                user_query=attribution.reusable_guidance,
                requires_approval=False,
            )
            logger.info(
                "[ReviewFeedbackEvolution] member evolution result: task=%s member=%s "
                "skill=%s status=%s",
                task_id,
                assignee,
                result.skill_name,
                result.status,
            )

    async def on_team_completed(self, _payload: dict[str, Any] | None = None) -> bool:
        """Promote aggregated task feedback into the corresponding global Skills."""
        if not get_skill_evolution_enabled(self._config):
            return False

        async with self._team_evolution_lock:
            end = len(self._observations)
            observations = self._observations[self._global_observation_cursor : end]
            pattern_end = len(self._new_skill_patterns)
            pattern_observations = self._new_skill_patterns[
                self._new_skill_pattern_cursor : pattern_end
            ]
            if not observations and not pattern_observations:
                return False

            global_rail = self._review_feedback_rail()
            if global_rail is None and observations:
                logger.warning(
                    "[ReviewFeedbackEvolution] team aggregation skipped: no global Skill rail"
                )
                return False

            grouped: dict[str, list[_TaskFeedbackObservation]] = {}
            for observation in observations:
                if global_rail is not None and global_rail.evolution_store.skill_exists(
                    observation.skill_name
                ):
                    grouped.setdefault(observation.skill_name, []).append(observation)
                else:
                    logger.info(
                        "[ReviewFeedbackEvolution] local-only Skill omitted from global promotion: %s",
                        observation.skill_name,
                    )
            attempted = False
            if grouped and global_rail is not None:
                trajectory = self._get_team_trajectory()
                messages = (
                    ConversationSignalDetector.convert_trajectory_to_messages(
                        trajectory
                    )
                    if trajectory is not None
                    else []
                )
                for skill_name, skill_observations in grouped.items():
                    try:
                        result = await global_rail.evolve_from_external_signals(
                            signals=[item.signal for item in skill_observations],
                            messages=messages,
                            trajectory=trajectory,
                            user_query=self._format_aggregated_feedback(
                                skill_observations
                            ),
                            requires_approval=not global_rail.auto_save,
                        )
                        attempted = True
                        logger.info(
                            "[ReviewFeedbackEvolution] global aggregate result: skill=%s "
                            "task_feedback_count=%d status=%s request=%s",
                            skill_name,
                            len(skill_observations),
                            result.status,
                            getattr(
                                getattr(result, "request", None), "request_id", None
                            ),
                        )
                    except Exception as exc:
                        logger.warning(
                            "[ReviewFeedbackEvolution] global aggregate failed for skill=%s: %s",
                            skill_name,
                            exc,
                            exc_info=True,
                        )

            if pattern_observations:
                attempted = (
                    await self._route_new_skill_patterns(pattern_observations)
                    or attempted
                )

            self._global_observation_cursor = end
            self._new_skill_pattern_cursor = pattern_end
            if global_rail is not None:
                try:
                    await self._push_pending_events(global_rail)
                except Exception as exc:
                    logger.warning(
                        "[ReviewFeedbackEvolution] failed to push global evolution events: %s",
                        exc,
                        exc_info=True,
                    )
            return attempted

    async def _route_new_skill_patterns(
        self,
        observations: list[_NewSkillPatternObservation],
    ) -> bool:
        """Route policy-approved repeated patterns to the creation Rail."""
        suggestions = [
            item
            for item in observations
            if item.action == ReviewFeedbackAction.SUGGEST_NEW_SKILL
        ]
        if not suggestions:
            return False

        creation_rail = self._team_skill_create_rail()
        if creation_rail is None:
            logger.info(
                "[ReviewFeedbackEvolution] repeated pattern detected but Skill creation "
                "is disabled or its Rail is unavailable"
            )
            return False

        attempted = False
        routed_keys: set[str] = set()
        for suggestion in suggestions:
            proposal_key = self._new_skill_proposal_key(suggestion.reusable_guidance)
            if not proposal_key or proposal_key in routed_keys:
                continue
            routed_keys.add(proposal_key)
            evidence = tuple(
                dict.fromkeys(
                    (
                        *suggestion.supporting_evidence,
                        self._format_new_skill_pattern_evidence(suggestion),
                    )
                )
            )
            try:
                routed = await creation_rail.propose_from_external_evidence(
                    proposal_key=proposal_key,
                    reusable_guidance=suggestion.reusable_guidance,
                    evidence=evidence,
                    reason=suggestion.reason,
                )
                attempted = bool(routed) or attempted
            except Exception as exc:
                logger.warning(
                    "[ReviewFeedbackEvolution] new-Skill creation routing failed: %s",
                    exc,
                    exc_info=True,
                )
        if attempted:
            await self._push_skill_creation_events(creation_rail)
        return attempted

    async def _push_skill_creation_events(self, creation_rail: Any) -> None:
        """Push structured new-Skill approval cards buffered by the Rail."""
        events = await creation_rail.drain_pending_approval_events(wait=False) or []
        if not events:
            return

        from jiuwenswarm.agents.harness.team.team_manager import get_team_manager
        from jiuwenswarm.server.utils.stream_utils import parse_stream_chunk

        manager = get_team_manager(self._channel_id)
        for event in events:
            parsed = parse_stream_chunk(event)
            if parsed is not None:
                await manager.broadcast_event(self._session_id, parsed)

    def _review_feedback_rail(self) -> Any | None:
        from jiuwenswarm.agents.harness.team.team_manager import get_team_manager

        return get_team_manager(self._channel_id).get_review_feedback_skill_rail(
            self._session_id
        )

    def _team_skill_create_rail(self) -> Any | None:
        from jiuwenswarm.agents.harness.team.team_manager import get_team_manager

        manager = get_team_manager(self._channel_id)
        getter = getattr(manager, "get_team_skill_create_rail", None)
        return getter(self._session_id) if callable(getter) else None

    def _member_rail_for(self, assignee: str, global_rail: Any) -> Any:
        cached = self._member_rails.get(assignee)
        if cached is not None:
            return cached

        from openjiuwen.agent_teams.workspace_layout import team_member_workspace_path
        from openjiuwen.harness.rails.evolution import EvolutionReviewRuntime
        from jiuwenswarm.agents.swarm.providers.evolution_rails import (
            SwarmMemberSkillEvolutionRail,
        )

        member_skills_dir = (
            team_member_workspace_path(self._team_id, assignee) / "skills"
        )
        member_skills_dir.mkdir(parents=True, exist_ok=True)
        global_skills_dir = global_rail.evolution_store.base_dir
        rail = SwarmMemberSkillEvolutionRail(
            [str(member_skills_dir), str(global_skills_dir)],
            llm=global_rail.evolver.llm,
            model=global_rail.evolver.model,
            review_runtime=EvolutionReviewRuntime(),
            language=getattr(global_rail, "_language", "cn"),
            signal_trigger=False,
            review_trigger=False,
            auto_save=True,
            disabled_skills=list(getattr(global_rail, "disabled_skills", set())),
        )
        rail.bind_swarm_context(
            channel=self._channel_id or "default",
            session_id=self._session_id,
            member_name=assignee,
            member_skills_dir=str(member_skills_dir),
            global_skills_dir=str(global_skills_dir),
        )
        self._member_rails[assignee] = rail
        return rail

    @staticmethod
    def _is_safe_member_name(member_name: str) -> bool:
        return bool(
            member_name
            and member_name not in {".", ".."}
            and _SAFE_MEMBER_NAME.fullmatch(member_name)
        )

    @staticmethod
    def _format_aggregated_feedback(
        observations: list[_TaskFeedbackObservation],
    ) -> str:
        lines = ["团队全部任务完成。以下是归因到同一全局 Skill 的任务审核反馈汇总："]
        for item in observations:
            lines.append(
                f"- task={item.task_id}, round={item.review_round}, "
                f"assignee={item.assignee}: {item.feedback}"
            )
        return "\n".join(lines)[:_MAX_AGGREGATED_FEEDBACK_CHARS]

    @staticmethod
    def _format_new_skill_pattern_evidence(
        observation: _NewSkillPatternObservation,
    ) -> str:
        return (
            f"task={observation.task_id}, round={observation.review_round}, "
            f"assignee={observation.assignee}: {observation.feedback}"
        )[:2_000]

    @staticmethod
    def _new_skill_proposal_key(reusable_guidance: str) -> str:
        return re.sub(r"[^\w]+", "-", reusable_guidance.strip().lower()).strip("-")[
            :160
        ]

    def _matching_new_skill_patterns(
        self,
        reusable_guidance: str,
        *,
        exclude_task_id: str,
    ) -> list[_NewSkillPatternObservation]:
        """Return prior observations for the same normalized reusable pattern."""
        pattern_key = self._new_skill_proposal_key(reusable_guidance)
        if not pattern_key:
            return []
        return [
            item
            for item in self._new_skill_patterns
            if item.task_id != exclude_task_id
            and self._new_skill_proposal_key(item.reusable_guidance) == pattern_key
        ]

    def _get_member_trajectory(self, assignee: str) -> Any | None:
        registry = self._trajectory_registry
        getter = getattr(registry, "get_member_trajectory", None)
        if callable(getter):
            for member_id in (
                assignee,
                f"{self._team_id}_{assignee}",
                f"jiuwen_{self._team_id}_{assignee}",
            ):
                try:
                    trajectory = getter(
                        team_id=self._team_id,
                        session_id=self._session_id,
                        member_id=member_id,
                    )
                except Exception as exc:
                    logger.warning(
                        "[ReviewFeedbackEvolution] member trajectory lookup failed: %s",
                        exc,
                    )
                    break
                if trajectory is not None:
                    return trajectory
        # Compatibility fallback for older/custom registry implementations.
        return self._get_team_trajectory()

    def _get_team_trajectory(self) -> Any | None:
        registry = self._trajectory_registry
        getter = getattr(registry, "get_trajectory", None)
        if not callable(getter):
            return None
        try:
            return getter(
                team_id=self._team_id,
                session_id=self._session_id,
                filter_collaborative=False,
            )
        except Exception as exc:
            logger.warning(
                "[ReviewFeedbackEvolution] trajectory lookup failed: %s", exc
            )
            return None

    async def _push_pending_events(self, rail: Any) -> None:
        events = await rail.drain_pending_approval_events(wait=False) or []
        if not events:
            return

        from jiuwenswarm.agents.harness.team.team_manager import get_team_manager
        from jiuwenswarm.server.gateway_push import WebSocketGatewayPushTransport
        from jiuwenswarm.server.runtime.agent_adapter.evolution_helpers import (
            EvolutionPushContext,
            build_evolution_status_update,
            group_evolution_approvals,
            push_evolution_event,
            push_evolution_status,
        )
        from jiuwenswarm.server.runtime.session.session_metadata import (
            build_server_push_message,
        )
        from jiuwenswarm.server.utils.stream_utils import parse_stream_chunk

        manager = get_team_manager(self._channel_id)
        push_context = EvolutionPushContext(
            transport=WebSocketGatewayPushTransport(),
            channel_id=self._channel_id,
            session_id=self._session_id,
        )
        grouped, _ = group_evolution_approvals(self._session_id, events)
        approval_event_ids = {
            id(event) for batch in grouped.values() for event in batch
        }
        for event in events:
            if id(event) in approval_event_ids:
                continue
            parsed = parse_stream_chunk(event)
            if parsed is not None:
                await manager.broadcast_event(self._session_id, parsed)

        for request_id, approval_events in grouped.items():
            await push_evolution_status(
                push_context,
                build_evolution_status_update(
                    request_id=request_id,
                    status="start",
                    stage="team_review_feedback_aggregated",
                    message="Task review feedback was aggregated for global Skill evolution",
                ),
                build_server_push_message,
            )
            for event in approval_events:
                await push_evolution_event(
                    push_context,
                    request_id,
                    event,
                    build_server_push_message,
                )
            await push_evolution_status(
                push_context,
                build_evolution_status_update(
                    request_id=request_id,
                    status="end",
                    stage="approval_required",
                    message="Team-level global Skill evolution is awaiting approval",
                ),
                build_server_push_message,
            )


def attach_review_feedback_handler(context: Any) -> Any:
    """Attach the scheduler callback to a live or reconstructed build context."""

    extras = getattr(context, "extras", None)
    if not isinstance(extras, dict):
        extras = {}
        context.extras = extras
    extras["review_feedback_handler"] = SwarmReviewFeedbackEvolutionHandler(
        channel_id=getattr(context, "channel_id", None),
        session_id=str(getattr(context, "session_id", "") or ""),
        team_id=str(getattr(context, "team_id", "") or ""),
        trajectory_registry=getattr(context, "trajectory_registry", None),
        config=getattr(context, "config", None),
    )
    return context


__all__ = [
    "SwarmReviewFeedbackEvolutionHandler",
    "attach_review_feedback_handler",
]
