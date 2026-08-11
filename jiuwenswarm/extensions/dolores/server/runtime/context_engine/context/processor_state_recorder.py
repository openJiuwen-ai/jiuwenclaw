# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

from openjiuwen.core.common.logging import logger
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.base import ContextStats
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.context.context_utils import ContextUtils
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.base import ContextProcessor
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.schema.context_state import (
    CONTEXT_COMPRESSION_STATE_TYPE,
    ContextCompressionMetric,
    ContextCompressionSaved,
    ContextCompressionState,
    ContextCompressionUsage,
)
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.token.base import TokenCounter
from openjiuwen.core.foundation.llm import BaseMessage
from openjiuwen.core.runner.callback import lazy_callback_framework as _fw
from openjiuwen.core.runner.callback.events import ContextEvents
from openjiuwen.core.session.stream.base import OutputSchema


@dataclass(frozen=True)
class ContextProcessorStateInput:
    operation_id: str
    status: str
    phase: str
    trigger: str
    processor: Optional[ContextProcessor]
    reason: str
    before_messages: list[BaseMessage]
    after_messages: Optional[list[BaseMessage]]
    started_at: float
    ended_at: Optional[float]
    error: Optional[str]
    messages_to_modify: list[int]
    force: bool
    context_max: Optional[int]
    compact_summary: str = ""
    compression_usage: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class _SummaryInput:
    status: str
    before: ContextCompressionMetric
    after: Optional[ContextCompressionMetric]
    saved: Optional[ContextCompressionSaved]
    reason: str
    messages_to_modify: list[int]


class ContextProcessorStateRecorder:
    def __init__(
            self,
            *,
            session_id: str,
            context_id: str,
            get_session_ref: Callable[[], Any],
            token_counter: TokenCounter = None,
            history_limit: int = 100,
    ):
        self._session_id = session_id
        self._context_id = context_id
        self._get_session_ref = get_session_ref
        self._token_counter = token_counter
        self._history_limit = history_limit
        self._history: list[dict[str, Any]] = []

    def history(self) -> list[dict[str, Any]]:
        return list(self._history)

    def load_history(self, history: list[dict[str, Any]] | None) -> None:
        self._history = list(history or [])[-self._history_limit:]

    async def emit(self, context: Any, state: ContextCompressionState) -> None:
        self._record(state)
        logger.info(
            "context compression state: status=%s phase=%s processor=%s "
            "session_id=%s context_id=%s op=%s before=%s after=%s saved=%s",
            state.status,
            state.phase,
            state.processor,
            self._session_id,
            self._context_id,
            state.operation_id,
            self._format_metric_for_log(state.before),
            self._format_metric_for_log(state.after),
            state.saved,
        )
        try:
            await _fw.trigger(
                ContextEvents.CONTEXT_COMPRESSION_STATE,
                context=context,
                session_ref=self._get_session_ref(),
                session_id=self._session_id,
                context_id=self._context_id,
                state=state,
            )
        except Exception as exc:
            logger.warning(
                "failed to trigger context compression state callback: session_id=%s context_id=%s op=%s "
                "status=%s error=%s",
                self._session_id,
                self._context_id,
                state.operation_id,
                state.status,
                exc,
                exc_info=True,
            )
        session = self._get_session_ref()
        if session is None or not hasattr(session, "write_stream"):
            logger.debug(
                "context compression state stream skipped: no session writer session_id=%s context_id=%s op=%s",
                self._session_id,
                self._context_id,
                state.operation_id,
            )
            return
        try:
            await session.write_stream(
                OutputSchema(
                    type=CONTEXT_COMPRESSION_STATE_TYPE,
                    index=0,
                    payload=state.model_dump(mode="json"),
                )
            )
            logger.debug(
                "context compression state stream emitted: session_id=%s context_id=%s op=%s status=%s",
                self._session_id,
                self._context_id,
                state.operation_id,
                state.status,
            )
        except Exception as exc:
            logger.warning(
                "failed to emit context compression state to session stream: session_id=%s context_id=%s op=%s "
                "status=%s error=%s",
                self._session_id,
                self._context_id,
                state.operation_id,
                state.status,
                exc,
                exc_info=True,
            )

    def build_state(
            self,
            state_input: ContextProcessorStateInput,
    ) -> ContextCompressionState:
        before = self._build_metric(
            state_input.before_messages,
            state_input.context_max,
            observed_at=state_input.started_at,
        )
        after = (
            self._build_metric(
                state_input.after_messages,
                state_input.context_max,
                observed_at=state_input.ended_at,
            )
            if state_input.after_messages is not None
            else None
        )
        saved = self._build_saved(before, after)
        statistic_messages = (
            state_input.after_messages
            if state_input.after_messages is not None
            else state_input.before_messages
        )
        return ContextCompressionState(
            operation_id=state_input.operation_id,
            status=state_input.status,
            phase=state_input.phase,
            processor=state_input.processor.processor_type() if state_input.processor is not None else "",
            model=self._resolve_model_name(state_input.processor, state_input.trigger, state_input.force),
            before=before,
            after=after,
            statistic=self._build_statistic(statistic_messages),
            saved=saved,
            compression_usage=self._build_compression_usage(state_input.compression_usage),
            duration_ms=(
                int((state_input.ended_at - state_input.started_at) * 1000)
                if state_input.ended_at is not None
                else None
            ),
            context_max=state_input.context_max,
            summary=self._build_summary(_SummaryInput(
                status=state_input.status,
                before=before,
                after=after,
                saved=saved,
                reason=state_input.reason,
                messages_to_modify=state_input.messages_to_modify,
            )),
            compact_summary=state_input.compact_summary or "",
            error=state_input.error,
        )

    @staticmethod
    def _build_compression_usage(usage: Optional[dict[str, Any]]) -> Optional[ContextCompressionUsage]:
        if not usage:
            return None
        return ContextCompressionUsage(**usage)

    def _record(self, state: ContextCompressionState) -> None:
        self._history.append(state.model_dump(mode="json"))
        if len(self._history) > self._history_limit:
            self._history = self._history[-self._history_limit:]

    def _build_metric(
            self,
            messages: Optional[list[BaseMessage]],
            context_max: Optional[int],
            *,
            observed_at: Optional[float],
    ) -> ContextCompressionMetric:
        messages = list(messages or [])
        tokens = self._measure_messages(messages)
        return ContextCompressionMetric(
            time=self._format_time(observed_at),
            messages=len(messages),
            tokens=tokens,
            context_percent=self._context_percent(tokens, context_max),
        )

    def _measure_messages(self, messages: list[BaseMessage]) -> int:
        if self._token_counter:
            try:
                tokens = self._token_counter.count_messages(messages)
                if isinstance(tokens, int):
                    return tokens
            except Exception as exc:
                logger.debug("token_counter failed while measuring context processor state: %s", exc)
        total_chars = sum(len(str(getattr(message, "content", "") or "")) for message in messages)
        return math.ceil(total_chars / 4)

    def _build_statistic(self, messages: Optional[list[BaseMessage]]) -> ContextStats:
        stat = ContextStats()
        for message in list(messages or []):
            stat.total_messages += 1
            tokens = self._count_message_for_statistic(message)
            if message.role == "assistant":
                stat.assistant_messages += 1
                stat.assistant_message_tokens += tokens
            elif message.role == "user":
                stat.user_messages += 1
                stat.user_message_tokens += tokens
            elif message.role == "system":
                stat.system_messages += 1
                stat.system_message_tokens += tokens
            elif message.role == "tool":
                stat.tool_messages += 1
                stat.tool_message_tokens += tokens
        stat.total_tokens += (
            stat.assistant_message_tokens +
            stat.user_message_tokens +
            stat.system_message_tokens +
            stat.tool_message_tokens
        )
        stat.total_dialogues = len(ContextUtils.find_all_dialogue_round(list(messages or [])))
        return stat

    def _count_message_for_statistic(self, message: BaseMessage) -> int:
        if self._token_counter is None:
            return 0
        try:
            tokens = self._token_counter.count(message.content or "")
            if isinstance(tokens, int):
                return tokens
        except Exception as exc:
            logger.debug("token_counter failed while building context processor statistic: %s", exc)
        return 0

    @staticmethod
    def _build_saved(
            before: ContextCompressionMetric,
            after: Optional[ContextCompressionMetric],
    ) -> Optional[ContextCompressionSaved]:
        if after is None:
            return None
        saved_messages = before.messages - after.messages
        saved_tokens = before.tokens - after.tokens
        saved_percent = 0
        if before.tokens > 0:
            saved_percent = round(saved_tokens / before.tokens * 100, 1)
        return ContextCompressionSaved(
            messages=saved_messages,
            tokens=saved_tokens,
            percent=saved_percent,
        )

    @staticmethod
    def _format_metric_for_log(metric: Optional[ContextCompressionMetric]) -> Optional[str]:
        if metric is None:
            return None
        return (
            f"messages={metric.messages} tokens={metric.tokens} "
            f"percent={metric.context_percent} time={metric.time}"
        )

    @staticmethod
    def _context_percent(tokens: int, context_max: Optional[int]) -> Optional[int]:
        if not context_max:
            return None
        return max(0, min(100, round(tokens / context_max * 100)))

    @staticmethod
    def _compact_number(value: Optional[int]) -> str:
        if value is None:
            return "unknown"
        abs_value = abs(value)
        if abs_value >= 1_000_000:
            return f"{value / 1_000_000:.1f}m".replace(".0m", "m")
        if abs_value >= 1_000:
            return f"{value / 1_000:.1f}k".replace(".0k", "k")
        return str(value)

    @staticmethod
    def _format_time(timestamp: Optional[float]) -> Optional[str]:
        if timestamp is None:
            return None
        return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="milliseconds")

    def _build_summary(
            self,
            summary_input: _SummaryInput,
    ) -> str:
        status = summary_input.status
        before = summary_input.before
        after = summary_input.after
        saved = summary_input.saved
        if status == "started":
            return f"Compressing {before.messages} messages, ~{self._compact_number(before.tokens)} tokens"
        if status == "failed":
            return (
                f"Context processor failed; context remains ~{self._compact_number(before.tokens)} tokens"
            )
        if after is None or saved is None:
            return f"Context processor skipped: {summary_input.reason}"
        if status == "noop":
            return (
                f"Context unchanged at ~{self._compact_number(after.tokens)} tokens "
                f"(saved {saved.percent:.1f}%)"
            )
        messages_to_modify = summary_input.messages_to_modify
        modified = f", modified {len(messages_to_modify)} messages" if messages_to_modify else ""
        return (
            f"Compressed {before.messages} -> {after.messages} messages, "
            f"~{self._compact_number(before.tokens)} -> ~{self._compact_number(after.tokens)} tokens"
            f", saved ~{self._compact_number(saved.tokens)} tokens ({saved.percent:.1f}%)"
            f"{modified}"
        )

    @staticmethod
    def _resolve_model_name(
            processor: Optional[ContextProcessor],
            trigger: str,
            force: bool,
    ) -> str:
        _ = trigger, force
        config = getattr(processor, "config", None)
        model_config = getattr(config, "model", None)
        if model_config is not None:
            for key in ("model_name", "model"):
                value = getattr(model_config, key, None)
                if isinstance(value, str) and value:
                    return value
        for key in ("model_name", "model"):
            value = getattr(config, key, None)
            if isinstance(value, str) and value:
                return value
        return ""
