# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SecurityReviewAndSkillRail for in-task security supervision."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import fields
from typing import Any

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenswarm.agents.harness.common.security_review.classifier import (
    SecuritySignalClassifier,
)
from jiuwenswarm.agents.harness.common.security_review.scheduler import (
    SecurityReviewScheduler,
)
from jiuwenswarm.agents.harness.common.security_review.schema import (
    ReviewRequest,
    ReviewResult,
    SecurityEvent,
    SecurityReviewConfig,
    SecuritySignal,
    Severity,
)
from jiuwenswarm.agents.harness.common.security_review.session_state import (
    SecuritySessionState,
)
from jiuwenswarm.agents.harness.common.security_review.worker import SecurityReviewWorker

logger = logging.getLogger(__name__)


class SecurityReviewAndSkillRail(DeepAgentRail):
    """Observe security signals and inject bounded runtime advice."""

    priority = 88

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__()
        self.config = self._parse_config(config or {})
        self.classifier = SecuritySignalClassifier()
        self.state = SecuritySessionState(self.config)
        self.scheduler = SecurityReviewScheduler(self.config)
        self.worker = SecurityReviewWorker()
        self.system_prompt_builder = None
        self._session_id = "default"
        self._session_signals: dict[str, list[SecuritySignal]] = {}
        self._message_provider: Callable[[str], list[dict[str, Any]]] | None = None
        self._skill_state_provider: Callable[[], dict[str, Any]] | None = None
        self._results: list[dict[str, Any]] = []
        self._deferred_review_requests: list[ReviewRequest] = []
        self._deferred_review_dedupe: set[tuple[str, ...]] = set()
        self._background_review_task: asyncio.Task | None = None
        self.worker_call_count = 0

    def init(self, agent) -> None:
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

    def uninit(self, agent) -> None:
        _ = agent
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section("security_runtime_advice")
        self.system_prompt_builder = None

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        self._session_id = self._extract_session_id(getattr(ctx, "inputs", None))

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        inputs = getattr(ctx, "inputs", None)
        session_id = self._extract_session_id(inputs, self._session_id)
        query = self._extract_input_value(inputs, "query")
        if query:
            self.state.record_message(session_id, "user", self._truncate(query))
            self._prune_evicted_sessions()
        advice = self.state.consume_advice(session_id)
        if self.system_prompt_builder is None:
            return
        if advice is None or not self.config.runtime_advice:
            self.system_prompt_builder.remove_section("security_runtime_advice")
            return

        self.system_prompt_builder.add_section(
            PromptSection(
                name="security_runtime_advice",
                content={"cn": advice.content, "en": advice.content},
                priority=97,
            )
        )

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        inputs = getattr(ctx, "inputs", None)
        session_id = self._extract_session_id(inputs, self._session_id)
        self._session_id = session_id
        event = SecurityEvent(
            event_type="tool_call",
            session_id=session_id,
            iteration=self._extract_iteration(inputs),
            tool_name=str(self._extract_input_value(inputs, "tool_name") or ""),
            arguments_digest=self._truncate(self._extract_input_value(inputs, "tool_args")),
        )
        self._handle_event(event)

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        inputs = getattr(ctx, "inputs", None)
        session_id = self._extract_session_id(inputs, self._session_id)
        self._session_id = session_id
        event = SecurityEvent(
            event_type="tool_result",
            session_id=session_id,
            iteration=self._extract_iteration(inputs),
            tool_name=str(self._extract_input_value(inputs, "tool_name") or ""),
            result_digest=self._truncate(self._extract_input_value(inputs, "tool_result")),
        )
        self._handle_event(event)

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        inputs = getattr(ctx, "inputs", None)
        session_id = self._extract_session_id(inputs, self._session_id)
        self._session_id = session_id
        response = self._extract_input_value(inputs, "response")
        content = getattr(response, "content", response)
        self.state.record_message(session_id, "assistant", self._truncate(content))
        event = SecurityEvent(
            event_type="model_output",
            session_id=session_id,
            iteration=self._extract_iteration(inputs),
            result_digest=self._truncate(content),
        )
        self._handle_event(event)

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        inputs = getattr(ctx, "inputs", None)
        session_id = self._extract_session_id(inputs, self._session_id)
        signals = self._session_signals.get(session_id, [])
        session_review_signals = list(signals)
        if not session_review_signals:
            return
        iteration = max((signal.iteration for signal in session_review_signals), default=0)
        priority = max(
            (signal.severity for signal in session_review_signals),
            key=lambda severity: {
                Severity.LOW: 1,
                Severity.MEDIUM: 2,
                Severity.HIGH: 3,
                Severity.CRITICAL: 4,
            }[severity],
        )
        self._schedule_review_request(
            ReviewRequest(
                request_type="session_end_review",
                session_id=session_id,
                priority=priority,
                dedupe_key=(session_id, "session_end_review", str(iteration)),
                iteration=iteration,
                signals=session_review_signals[-5:],
                counters=self.state.counter_snapshot(session_id),
                sample_events=self.state.snapshot_events(session_id)[-5:],
            ),
            allow_same_session_deferral=True,
        )

    def get_session_snapshot(self, session_id: str) -> list[SecurityEvent]:
        return self.state.snapshot_events(session_id)

    def drain_review_requests(self) -> list[ReviewRequest]:
        return self._drain_review_requests()

    def update_llm(self, llm: Any | None) -> None:
        self.worker.update_llm(llm)

    def update_config(self, config: dict[str, Any] | None = None) -> None:
        """Hot-update bounded security review configuration."""
        self.config = self._parse_config(config or {})
        self.state.config = self.config
        self.scheduler.config = self.config

    def set_context_providers(
        self,
        *,
        message_provider: Callable[[str], list[dict[str, Any]]] | None = None,
        skill_state_provider: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._message_provider = message_provider
        self._skill_state_provider = skill_state_provider

    async def process_pending_reviews(self, *, wait: bool = True) -> list[ReviewResult]:
        if not self.config.async_review:
            return []

        if not wait:
            self._ensure_background_review_task()
            return []

        if self._background_review_task is not None and not self._background_review_task.done():
            await self._background_review_task
            return []

        return await self._process_pending_reviews_now()

    async def wait_for_background_reviews(self) -> None:
        task = self._background_review_task
        if task is not None and not task.done():
            await task

    def _ensure_background_review_task(self) -> None:
        if not self.config.async_review or not self._has_pending_review_work():
            return
        if self._background_review_task is not None and not self._background_review_task.done():
            return
        self._background_review_task = asyncio.create_task(self._run_background_reviews())
        self._background_review_task.add_done_callback(self._on_background_review_done)

    async def _run_background_reviews(self) -> None:
        while self.config.async_review and self._has_pending_review_work():
            await self._process_pending_reviews_now()
            await asyncio.sleep(0)

    def _on_background_review_done(self, task: asyncio.Task) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("security review background task failed: %s", exc)

    def _has_pending_review_work(self) -> bool:
        return self.scheduler.has_pending_work() or bool(self._deferred_review_requests)

    async def _process_pending_reviews_now(self) -> list[ReviewResult]:
        results: list[ReviewResult] = []
        for request in self._drain_review_requests():
            # if not self.scheduler.mark_review_started(request.session_id):
            #     continue
            request = self._enrich_request(request)
            result = await self.worker.review(request)
            self.worker_call_count += 1
            if result.runtime_advice:
                self.state.set_runtime_advice(
                    result.session_id,
                    result.runtime_advice,
                    severity=request.priority,
                )
            self._results.append(
                {
                    "session_id": result.session_id,
                    "summary": result.summary,
                    "runtime_advice": result.runtime_advice,
                    "candidates": result.candidates,
                }
            )
            results.append(result)
        return results

    def drain_candidates(self, *, session_id: str | None = None) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        remaining_results: list[dict[str, Any]] = []
        for result in self._results:
            if session_id is not None and result.get("session_id") != session_id:
                remaining_results.append(result)
                continue
            candidates.extend(
                candidate
                for candidate in result.get("candidates", [])
                if self._candidate_enabled(candidate)
            )
        self._results = remaining_results
        return candidates

    def add_review_result_for_test(self, result: dict[str, Any]) -> None:
        self._results.append(result)

    def _handle_event(self, event: SecurityEvent) -> None:
        self.state.record_event(event)
        self._prune_evicted_sessions()
        signals = self.classifier.classify(event)
        if signals:
            self._session_signals.setdefault(event.session_id, []).extend(signals)
        generated_signals = self.state.record_signals(signals)
        if generated_signals:
            self._session_signals.setdefault(event.session_id, []).extend(generated_signals)
            self._schedule_timely_reviews(event.session_id, generated_signals)
        self._prune_evicted_sessions()

    def _schedule_timely_reviews(
        self,
        session_id: str,
        signals: list[SecuritySignal],
    ) -> None:
        if not self.config.timely_tool_failure_review:
            return
        reviewable_signal_types = {
            "repeated_tool_failure",
            "approval_boundary_gap",
            "policy_rule_gap",
        }
        has_same_session_pending = self._has_pending_timely_review_for_session(session_id)
        scheduled_or_buffered = False
        seen_dedupe_keys: set[tuple[str, ...]] = set()
        for signal in signals:
            if signal.signal_type not in reviewable_signal_types:
                continue
            failure = signal.failure_class.value if signal.failure_class is not None else ""
            request = ReviewRequest(
                request_type="timely_tool_failure_review",
                session_id=session_id,
                priority=signal.severity,
                dedupe_key=(
                    session_id,
                    "timely_tool_failure_review",
                    signal.tool_name,
                    failure,
                    signal.reason_code or "",
                ),
                iteration=signal.iteration,
                signals=[signal],
                counters=self.state.counter_snapshot(session_id),
                sample_events=self.state.snapshot_events(session_id)[-5:],
            )
            if request.dedupe_key in seen_dedupe_keys:
                continue
            seen_dedupe_keys.add(request.dedupe_key)
            if self._schedule_review_request(
                request,
                allow_same_session_deferral=scheduled_or_buffered or has_same_session_pending,
            ):
                scheduled_or_buffered = True

    def _drain_review_requests(self) -> list[ReviewRequest]:
        requests = self.scheduler.drain()
        scheduled_sessions = {request.session_id for request in requests}
        deferred_requests = [
            request
            for request in self._deferred_review_requests
            if request.session_id in scheduled_sessions
        ]
        for request in deferred_requests:
            self._record_deferred_request_accounting(request)
        requests.extend(deferred_requests)
        self._deferred_review_requests.clear()
        self._deferred_review_dedupe.clear()
        return requests

    def _schedule_review_request(
        self,
        request: ReviewRequest,
        *,
        allow_same_session_deferral: bool = False,
    ) -> bool:
        if self.scheduler.schedule(request):
            return True
        if self.scheduler.has_dedupe_key(request.dedupe_key):
            return False
        if request.dedupe_key in self._deferred_review_dedupe:
            return False
        if not allow_same_session_deferral:
            return False
        if not self._can_defer_same_session_collision(request):
            return False
        self._deferred_review_dedupe.add(request.dedupe_key)
        self._deferred_review_requests.append(request)
        return True

    def _has_pending_timely_review_for_session(self, session_id: str) -> bool:
        return self.scheduler.has_pending_timely_review(session_id)

    def _has_pending_review_for_session(self, session_id: str) -> bool:
        return self.scheduler.has_pending_session(session_id)

    def _record_deferred_request_accounting(self, request: ReviewRequest) -> None:
        self.scheduler.record_deferred_request_accounting(request)

    def _can_defer_same_session_collision(self, request: ReviewRequest) -> bool:
        return self.scheduler.can_defer_same_session_collision(request)

    def _prune_evicted_sessions(self) -> None:
        evicted_sessions = set(self.state.drain_evicted_sessions())
        if not evicted_sessions:
            return
        for session_id in evicted_sessions:
            self._session_signals.pop(session_id, None)
        self._drop_scheduled_requests_for_sessions(evicted_sessions)
        self._deferred_review_requests = [
            request
            for request in self._deferred_review_requests
            if request.session_id not in evicted_sessions
        ]
        self._deferred_review_dedupe = {
            dedupe_key
            for dedupe_key in self._deferred_review_dedupe
            if dedupe_key[0] not in evicted_sessions
        }
        self._results = [
            result
            for result in self._results
            if result.get("session_id") not in evicted_sessions
        ]

    def _drop_scheduled_requests_for_sessions(self, session_ids: set[str]) -> None:
        self.scheduler.drop_sessions(session_ids)

    def _enrich_request(self, request: ReviewRequest) -> ReviewRequest:
        request.sample_messages = self._sample_messages(request.session_id)[-8:]
        request.skill_state = self._skill_state()
        return request

    def _sample_messages(self, session_id: str) -> list[dict[str, str]]:
        if self._message_provider is not None:
            try:
                raw_messages = self._message_provider(session_id)
            except Exception:
                raw_messages = []
            messages: list[dict[str, str]] = []
            for message in raw_messages[-8:]:
                role = str(message.get("role") or message.get("type") or "unknown")
                content = str(message.get("content") or message.get("content_digest") or "")
                if content.strip():
                    messages.append({"role": role, "content_digest": self._truncate(content)})
            if messages:
                return messages
        return self.state.snapshot_messages(session_id)[-8:]

    def _skill_state(self) -> dict[str, Any]:
        if self._skill_state_provider is None:
            return {}
        try:
            return self._skill_state_provider()
        except Exception:
            return {}

    @staticmethod
    def _extract_session_id(inputs: Any, fallback: str = "default") -> str:
        if isinstance(inputs, dict):
            return str(inputs.get("conversation_id") or inputs.get("session_id") or fallback)
        return str(
            getattr(inputs, "conversation_id", None)
            or getattr(inputs, "session_id", None)
            or fallback
        )

    @staticmethod
    def _extract_iteration(inputs: Any) -> int:
        try:
            if isinstance(inputs, dict):
                return int(inputs.get("iteration", 0) or 0)
            return int(getattr(inputs, "iteration", 0) or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _extract_input_value(inputs: Any, key: str) -> Any:
        if isinstance(inputs, dict):
            return inputs.get(key, "")
        return getattr(inputs, key, "")

    def _truncate(self, value: Any) -> str:
        return str(value or "")[: max(1, self.config.max_event_chars)]

    def _candidate_enabled(self, candidate: dict[str, Any]) -> bool:
        candidate_type = candidate.get("type")
        if candidate_type == "security_rule":
            return self.config.propose_policy_rules
        if candidate_type in {"security_skill", "security_evolution"}:
            return self.config.evolve_security_skills
        return True

    @staticmethod
    def _parse_config(config: dict[str, Any]) -> SecurityReviewConfig:
        allowed = {field.name for field in fields(SecurityReviewConfig)}
        values = {key: config[key] for key in allowed if key in config}
        return SecurityReviewConfig(**values)
