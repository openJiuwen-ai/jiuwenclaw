# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""AgentDropoutRail — rectify-or-reject teammate contributions before team share.

Maps AgentDropoutV2 "intercept before broadcast" onto DeepAgentRail hooks:
- ``before_tool_call`` audits ``team.send_message``-style share tools
- ``before_model_call`` injects pending rectify feedback into the prompt

Visibility:
- Emits ``chat.notice`` so the group chat UI shows system messages for every check
- Emits ``llm_reasoning`` (source=agent_dropout) for member self-reasoning streams
- Emits synthetic ``tool_call`` / ``tool_result`` for the member process timeline
- On DROP, emits ``team.member.shutdown`` and force-finishes the member loop
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Callable

from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenswarm.agents.dropout import (
    AgentDropoutConfig,
    AgentDropoutService,
    ContributionAction,
)
from jiuwenswarm.agents.dropout.prompts import (
    DROP_SIGNAL_MESSAGE,
    DROP_SIGNAL_PREFIX,
    RECTIFY_TOOL_MESSAGE,
    REJECT_TOOL_MESSAGE,
)
from jiuwenswarm.agents.dropout.types import AuditResult

logger = logging.getLogger(__name__)

# Team-sharing tools (openjiuwen team tools use id ``team.send_message``).
_TEAM_SHARE_TOOL_NAMES = frozenset({"send_message", "broadcast"})
_CONTENT_ARG_KEYS = ("content", "message", "text", "query", "body")
_AUDIT_TOOL_NAME = "agent_dropout_audit"


class AgentDropoutRail(DeepAgentRail):
    """Audit teammate share tools; rectify, reject, drop, and notify the UI."""

    priority = 85
    SECTION_NAME = "agent_dropout_feedback"
    SECTION_PRIORITY = 45

    def __init__(
        self,
        *,
        service: AgentDropoutService,
        member_name: str = "teammate",
        role: str = "teammate",
        active_members: int = 2,
        task_resolver: Callable[[AgentCallbackContext], str] | None = None,
    ) -> None:
        super().__init__()
        self.system_prompt_builder = None
        self._service = service
        self._member_name = member_name or "teammate"
        self._role = role or "teammate"
        self._active_members = max(1, int(active_members))
        self._task_resolver = task_resolver

    def init(self, agent) -> None:
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)
        card = getattr(agent, "card", None)
        if card is not None:
            name = getattr(card, "name", None) or getattr(card, "id", None)
            if name:
                self._member_name = str(name)

    def uninit(self, agent) -> None:
        _ = agent
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section(self.SECTION_NAME)
        self.system_prompt_builder = None

    def set_active_members(self, count: int) -> None:
        self._active_members = max(1, int(count))

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject pending rectify feedback into the system prompt, if any."""
        if self._service.tracker.is_dropped(self._member_name):
            force_finish = getattr(ctx, "request_force_finish", None)
            if callable(force_finish):
                force_finish(
                    {
                        "content": (
                            f"{DROP_SIGNAL_PREFIX} Member '{self._member_name}' "
                            "was dropped by AgentDropout and will not continue."
                        )
                    }
                )
            return

        if self.system_prompt_builder is None:
            return
        feedback = self._service.get_pending_feedback(self._member_name)
        if not feedback:
            self.system_prompt_builder.remove_section(self.SECTION_NAME)
            return
        self.system_prompt_builder.add_section(
            PromptSection(
                name=self.SECTION_NAME,
                content={
                    "en": "# Agent Dropout Rectify Feedback\n\n" + feedback + "\n",
                    "cn": "# Agent Dropout 纠正反馈\n\n" + feedback + "\n",
                },
                priority=self.SECTION_PRIORITY,
            )
        )

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Intercept team share tools and run rectify-or-reject evaluation."""
        inputs = getattr(ctx, "inputs", None)
        if inputs is None:
            return
        tool_name = getattr(inputs, "tool_name", None) or ""
        tool_call = getattr(inputs, "tool_call", None)
        if not self._is_team_share_tool(tool_name, tool_call):
            return

        if self._service.tracker.is_dropped(self._member_name):
            message = (
                f"{DROP_SIGNAL_PREFIX} Member '{self._member_name}' is dropped; "
                "sharing is blocked."
            )
            self._reject_tool(ctx, message)
            await self._emit_visibility(
                ctx,
                notice_type="agent_dropout_blocked",
                content=message,
                level="warning",
                reasoning=message,
            )
            return

        content = self._extract_share_content(tool_call)
        if not content.strip():
            logger.info(
                "[AgentDropoutRail] skip empty share content member=%s tool=%s id=%s",
                self._member_name,
                tool_name,
                getattr(tool_call, "id", None),
            )
            return

        task = self._resolve_task(ctx)
        attempt = self._service.rectify_attempt(self._member_name) + 1
        audit_id = f"ad-{uuid.uuid4().hex[:10]}"
        preview = _preview_text(content)
        check_msg = (
            f"AgentDropout: checking '{self._member_name}' share "
            f"(attempt {attempt}) — {preview}"
        )
        logger.info(
            "[AgentDropoutRail] auditing share member=%s tool=%s attempt=%s audit_id=%s",
            self._member_name,
            tool_name,
            attempt,
            audit_id,
        )
        await self._emit_visibility(
            ctx,
            notice_type="agent_dropout_check",
            content=check_msg,
            level="info",
            reasoning=(
                f"[AgentDropout] Auditing outbound share from '{self._member_name}'.\n"
                f"Task: {task}\n"
                f"Preview: {preview}\n"
                "Running rectify-or-reject metrics…"
            ),
            request_id=f"{audit_id}-check",
            emit_tool_call=True,
            tool_call_id=audit_id,
            tool_phase="checking",
            tool_args={"attempt": attempt, "preview": preview},
        )

        try:
            result = await self._service.evaluate_contribution(
                task=task,
                content=content,
                member_name=self._member_name,
                role=self._role,
                active_members=self._active_members,
            )
        except Exception as exc:  # noqa: BLE001 — never break team tool path
            logger.warning(
                "[AgentDropoutRail] evaluate_contribution failed member=%s: %s",
                self._member_name,
                exc,
            )
            await self._emit_visibility(
                ctx,
                notice_type="agent_dropout_error",
                content=(
                    f"AgentDropout: audit failed for '{self._member_name}' "
                    f"({type(exc).__name__}); share allowed."
                ),
                level="warning",
                reasoning=f"[AgentDropout] Audit error: {exc}",
                request_id=f"{audit_id}-error",
                tool_call_id=audit_id,
                tool_phase="error",
                tool_result=str(exc),
            )
            return

        summary = _format_audit_summary(result.audit)

        if result.action == ContributionAction.PASS:
            self._service.clear_pending_feedback(self._member_name)
            if self.system_prompt_builder is not None:
                self.system_prompt_builder.remove_section(self.SECTION_NAME)
            pass_msg = (
                f"AgentDropout: '{self._member_name}' share passed "
                f"({result.audit.pass_count}/{result.audit.total_metrics}) — {summary}"
            )
            logger.info(
                "[AgentDropoutRail] pass member=%s message_id=%s",
                self._member_name,
                result.message_id,
            )
            await self._emit_visibility(
                ctx,
                notice_type="agent_dropout_pass",
                content=pass_msg,
                level="info",
                reasoning=(
                    f"[AgentDropout] PASS for '{self._member_name}'.\n"
                    f"Metrics: {summary}\n"
                    "Share allowed."
                ),
                request_id=f"{audit_id}-pass",
                tool_call_id=audit_id,
                tool_phase="pass",
                tool_result=pass_msg,
            )
            return

        if result.action == ContributionAction.RECTIFY:
            feedback = result.audit.feedback or "Please revise your contribution."
            message = RECTIFY_TOOL_MESSAGE.format(
                prefix=DROP_SIGNAL_PREFIX,
                attempt=result.rectify_attempt,
                max_attempts=self._service.config.max_rectify_attempts,
                feedback=feedback,
            )
            self._reject_tool(ctx, message)
            rectify_msg = (
                f"AgentDropout: '{self._member_name}' contribution blocked "
                f"(rectify attempt {result.rectify_attempt}/"
                f"{self._service.config.max_rectify_attempts}) — {summary}"
            )
            await self._emit_visibility(
                ctx,
                notice_type="agent_dropout_rectify",
                content=rectify_msg,
                level="info",
                reasoning=(
                    f"[AgentDropout] RECTIFY for '{self._member_name}'.\n"
                    f"Metrics: {summary}\n"
                    f"Feedback:\n{feedback}"
                ),
                request_id=f"{audit_id}-rectify",
                tool_call_id=audit_id,
                tool_phase="rectify",
                tool_result=rectify_msg,
            )
            return

        details = result.audit.feedback or "Audit failed after rectify attempts."
        if result.action == ContributionAction.DROP and result.dropout is not None:
            message = DROP_SIGNAL_MESSAGE.format(
                prefix=DROP_SIGNAL_PREFIX,
                member_name=self._member_name,
                failure_count=result.dropout.failure_count,
                reason=result.dropout.reason,
            )
            message = f"{message}\n{details}"
            self._reject_tool(ctx, message)
            drop_msg = (
                f"AgentDropout: member '{self._member_name}' dropped after "
                f"{result.dropout.failure_count} failed correction(s). "
                f"{result.dropout.reason} — {summary}"
            )
            await self._emit_visibility(
                ctx,
                notice_type="agent_dropout_drop",
                content=drop_msg,
                level="warning",
                reasoning=(
                    f"[AgentDropout] DROP '{self._member_name}'.\n"
                    f"Metrics: {summary}\n"
                    f"Reason: {result.dropout.reason}"
                ),
                request_id=f"{audit_id}-drop",
                tool_call_id=audit_id,
                tool_phase="drop",
                tool_result=drop_msg,
            )
            await self._emit_member_shutdown(ctx)
            force_finish = getattr(ctx, "request_force_finish", None)
            if callable(force_finish):
                force_finish(
                    {
                        "content": (
                            f"{DROP_SIGNAL_PREFIX} Member '{self._member_name}' "
                            "was dropped by AgentDropout."
                        )
                    }
                )
            logger.warning(
                "[AgentDropoutRail] DROP member=%s failures=%s reason=%s",
                self._member_name,
                result.dropout.failure_count,
                result.dropout.reason,
            )
            return

        message = REJECT_TOOL_MESSAGE.format(
            prefix=DROP_SIGNAL_PREFIX,
            details=details,
        )
        self._reject_tool(ctx, message)
        reject_msg = (
            f"AgentDropout: '{self._member_name}' contribution rejected "
            f"after failed corrections (member kept) — {summary}"
        )
        await self._emit_visibility(
            ctx,
            notice_type="agent_dropout_reject",
            content=reject_msg,
            level="warning",
            reasoning=(
                f"[AgentDropout] REJECT for '{self._member_name}'.\n"
                f"Metrics: {summary}\n"
                f"Details:\n{details}"
            ),
            request_id=f"{audit_id}-reject",
            tool_call_id=audit_id,
            tool_phase="reject",
            tool_result=reject_msg,
        )

    async def _emit_visibility(
        self,
        ctx: AgentCallbackContext,
        *,
        notice_type: str,
        content: str,
        level: str = "info",
        reasoning: str | None = None,
        request_id: str | None = None,
        emit_tool_call: bool = False,
        tool_call_id: str | None = None,
        tool_phase: str | None = None,
        tool_args: dict[str, Any] | None = None,
        tool_result: str | None = None,
    ) -> None:
        """Emit group-chat notice + optional reasoning / member-process tool events."""
        rid = request_id or f"ad-{uuid.uuid4().hex[:10]}"
        await self._emit_notice(
            ctx,
            notice_type=notice_type,
            content=content,
            level=level,
            request_id=rid,
        )
        if reasoning:
            await self._emit_reasoning(ctx, reasoning)
        call_id = tool_call_id or rid
        if emit_tool_call:
            await self._emit_audit_tool_call(
                ctx,
                tool_call_id=call_id,
                phase=tool_phase or "checking",
                arguments=tool_args or {},
            )
        if tool_result is not None:
            await self._emit_audit_tool_result(
                ctx,
                tool_call_id=call_id,
                phase=tool_phase or "done",
                result=tool_result,
            )

    async def _emit_notice(
        self,
        ctx: AgentCallbackContext,
        *,
        notice_type: str,
        content: str,
        level: str = "info",
        request_id: str | None = None,
    ) -> None:
        session = getattr(ctx, "session", None)
        if session is None or not hasattr(session, "write_stream"):
            return
        try:
            await session.write_stream(
                OutputSchema(
                    type="notice",
                    index=0,
                    payload={
                        "event_type": "chat.notice",
                        "notice_type": notice_type,
                        "level": level,
                        "content": content,
                        "member_name": self._member_name,
                        "request_id": request_id or f"ad-{uuid.uuid4().hex[:10]}",
                        "timestamp": int(time.time() * 1000),
                    },
                )
            )
        except Exception:  # noqa: BLE001
            logger.debug("[AgentDropoutRail] notice emit failed", exc_info=True)

    async def _emit_reasoning(self, ctx: AgentCallbackContext, content: str) -> None:
        session = getattr(ctx, "session", None)
        if session is None or not hasattr(session, "write_stream"):
            return
        try:
            await session.write_stream(
                OutputSchema(
                    type="llm_reasoning",
                    index=0,
                    payload={
                        "content": content,
                        "source": "agent_dropout",
                        "member_name": self._member_name,
                        "timestamp": int(time.time() * 1000),
                    },
                )
            )
        except Exception:  # noqa: BLE001
            logger.debug("[AgentDropoutRail] reasoning emit failed", exc_info=True)

    async def _emit_audit_tool_call(
        self,
        ctx: AgentCallbackContext,
        *,
        tool_call_id: str,
        phase: str,
        arguments: dict[str, Any],
    ) -> None:
        session = getattr(ctx, "session", None)
        if session is None or not hasattr(session, "write_stream"):
            return
        try:
            await session.write_stream(
                OutputSchema(
                    type="tool_call",
                    index=0,
                    payload={
                        "tool_call": {
                            "id": tool_call_id,
                            "name": _AUDIT_TOOL_NAME,
                            "arguments": {
                                "phase": phase,
                                "member_name": self._member_name,
                                **arguments,
                            },
                        }
                    },
                )
            )
        except Exception:  # noqa: BLE001
            logger.debug("[AgentDropoutRail] audit tool_call emit failed", exc_info=True)

    async def _emit_audit_tool_result(
        self,
        ctx: AgentCallbackContext,
        *,
        tool_call_id: str,
        phase: str,
        result: str,
    ) -> None:
        session = getattr(ctx, "session", None)
        if session is None or not hasattr(session, "write_stream"):
            return
        try:
            await session.write_stream(
                OutputSchema(
                    type="tool_result",
                    index=0,
                    payload={
                        "tool_result": {
                            "tool_name": _AUDIT_TOOL_NAME,
                            "tool_call_id": tool_call_id,
                            "result": result,
                            "summary": phase,
                            "success": phase in {"pass", "checking"},
                        }
                    },
                )
            )
        except Exception:  # noqa: BLE001
            logger.debug(
                "[AgentDropoutRail] audit tool_result emit failed", exc_info=True
            )

    async def _emit_member_shutdown(self, ctx: AgentCallbackContext) -> None:
        session = getattr(ctx, "session", None)
        if session is None or not hasattr(session, "write_stream"):
            return
        try:
            await session.write_stream(
                OutputSchema(
                    type="team.member",
                    index=0,
                    payload={
                        "event_type": "team.member",
                        "type": "team.member.shutdown",
                        "member_id": self._member_name,
                        "status": "shutdown",
                        "force": True,
                        "reason": "agent_dropout",
                        "timestamp": int(time.time() * 1000),
                    },
                )
            )
        except Exception:  # noqa: BLE001
            logger.debug("[AgentDropoutRail] shutdown emit failed", exc_info=True)

    def _resolve_task(self, ctx: AgentCallbackContext) -> str:
        if self._task_resolver is not None:
            try:
                return self._task_resolver(ctx) or "team collaboration"
            except Exception:  # noqa: BLE001
                pass
        inputs = getattr(ctx, "inputs", None)
        for attr in ("query", "task", "user_query", "conversation_id"):
            value = getattr(inputs, attr, None) if inputs is not None else None
            if isinstance(value, str) and value.strip():
                return value.strip()
        return "team collaboration"

    @staticmethod
    def _is_team_share_tool(tool_name: str, tool_call: Any) -> bool:
        candidates = [
            str(tool_name or "").strip().lower(),
            str(getattr(tool_call, "name", "") or "").strip().lower(),
            str(getattr(tool_call, "id", "") or "").strip().lower(),
        ]
        for candidate in candidates:
            if not candidate:
                continue
            short = candidate.rsplit(".", 1)[-1]
            if short in _TEAM_SHARE_TOOL_NAMES or candidate in _TEAM_SHARE_TOOL_NAMES:
                return True
            if "send_message" in candidate or candidate.endswith(".broadcast"):
                return True
        return False

    @staticmethod
    def _extract_share_content(tool_call: Any) -> str:
        args = _parse_tool_arguments(tool_call)
        for key in _CONTENT_ARG_KEYS:
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return ""

    @staticmethod
    def _reject_tool(ctx: AgentCallbackContext, message: str) -> None:
        """Skip tool execution and return the rejection / feedback message."""
        tool_call = getattr(ctx.inputs, "tool_call", None)
        tool_call_id = getattr(tool_call, "id", "") if tool_call else ""
        ctx.extra["_skip_tool"] = True
        ctx.inputs.tool_result = message
        ctx.inputs.tool_msg = ToolMessage(content=message, tool_call_id=tool_call_id)


def _parse_tool_arguments(tool_call: Any) -> dict[str, Any]:
    """Parse tool_call.arguments whether dict or JSON string (SDK default)."""
    raw = getattr(tool_call, "arguments", None) if tool_call is not None else None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _preview_text(content: str, limit: int = 80) -> str:
    text = " ".join((content or "").split())
    if len(text) <= limit:
        return text or "(empty)"
    return text[: limit - 1] + "…"


def _format_audit_summary(audit: AuditResult) -> str:
    if not audit.judgements:
        return "no metric judgements"
    parts: list[str] = []
    for judgement in audit.judgements:
        mark = "ok" if judgement.is_correct else "fail"
        parts.append(f"{judgement.metric}:{mark}")
    return ", ".join(parts)


def build_agent_dropout_rail(
    *,
    config: AgentDropoutConfig | dict[str, Any] | None,
    member_name: str = "teammate",
    role: str = "teammate",
    active_members: int = 2,
    llm=None,
) -> AgentDropoutRail | None:
    """Factory used by the swarm provider; returns None when disabled."""
    cfg = (
        config
        if isinstance(config, AgentDropoutConfig)
        else AgentDropoutConfig.from_mapping(config if isinstance(config, dict) else None)
    )
    if not cfg.enabled:
        return None
    service = AgentDropoutService(config=cfg, llm=llm)
    return AgentDropoutRail(
        service=service,
        member_name=member_name,
        role=role,
        active_members=active_members,
    )


__all__ = ["AgentDropoutRail", "build_agent_dropout_rail"]
