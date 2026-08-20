# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""AuditRail — independent DeepAgentRail for security/compliance auditing.

Runs alongside TelemetryRail (priority 10) at priority 20, so TelemetryRail
spans are already active when AuditRail hooks fire — this lets audit records
carry a valid ``trace_id`` for correlation.

The detectors (tool_risk, pii_scanner, safety_filter) are placeholder stubs
in this skeleton. They return ``None`` (no finding) by default. Replace the
``_evaluate_*`` methods with real logic in subsequent phases.
"""

from __future__ import annotations

import functools
from typing import Any, Optional

from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenswarm.utils import logger
from jiuwenswarm.telemetry.audit import AuditLogger, AuditType
from jiuwenswarm.telemetry.audit.detectors import ToolRiskEvaluator, PIIScanner, SafetyFilter


def _hook_safe(method):
    """Decorator: swallow exceptions in AuditRail hooks (same pattern as TelemetryRail)."""
    @functools.wraps(method)
    async def wrapper(self, *args, **kwargs):
        try:
            return await method(self, *args, **kwargs)
        except Exception as exc:
            logger.warning("[AuditRail] hook %s failed: %s", method.__name__, exc)
            return None
    return wrapper


class AuditRail(DeepAgentRail):
    """Rail that emits audit records for security/compliance events.

    Hooks:
      - before_tool_call: tool risk evaluation + interception audit
      - after_model_call: PII scan + content safety filter on LLM output
      - before_model_call: prompt injection detection on user input

    Each detector returns ``None`` when no finding, or a ``dict`` of audit
    details when a finding is triggered. Only findings produce audit records.
    """

    priority = 20

    def __init__(self) -> None:
        super().__init__()
        self._audit = AuditLogger()
        self._tool_risk = ToolRiskEvaluator()
        self._pii_scanner = PIIScanner()
        self._safety_filter = SafetyFilter()
        self._agent = None

    def init(self, agent: Any) -> None:
        """Called when Rail is attached to agent — capture agent reference."""
        self._agent = agent

    def _get_agent_name(self) -> str:
        if self._agent and hasattr(self._agent, "card"):
            return getattr(self._agent.card, "id", "")
        return ""

    # ------------------------------------------------------------------
    # Tool hooks — Tool & Action Audit
    # ------------------------------------------------------------------

    @_hook_safe
    async def before_tool_call(self, ctx: Any) -> None:
        """Evaluate tool risk before execution; audit if high-risk or blocked."""
        tool_name = ""
        tool_call_id = ""
        arguments: dict = {}

        if hasattr(ctx, "inputs"):
            inputs = ctx.inputs
            if hasattr(inputs, "tool_call"):
                tc = inputs.tool_call
                tool_name = getattr(tc, "name", "") or ""
                tool_call_id = getattr(tc, "id", "") or ""
                arguments = getattr(tc, "arguments", {}) or {}

        finding = await self._evaluate_tool_risk(tool_name, str(arguments))
        if finding is not None:
            self._audit.log_audit(
                audit_type=AuditType.TOOL_ACTION,
                details={
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                    **finding,
                },
                agent_name=self._get_agent_name(),
            )
            self.block_if_set(ctx, finding, scope="tool_call")

    @_hook_safe
    async def after_tool_call(self, ctx: Any) -> None:
        """Audit blocked tool calls or cross-tenant access attempts."""
        result = None
        if hasattr(ctx, "inputs"):
            inputs = ctx.inputs
            if hasattr(inputs, "tool_result"):
                result = inputs.tool_result

        finding = await self._evaluate_tool_result(result)
        if finding is not None:
            self._audit.log_audit(
                audit_type=AuditType.TOOL_ACTION,
                details=finding,
                agent_name=self._get_agent_name(),
            )

    # ------------------------------------------------------------------
    # Model hooks — Privacy & PII + Guardrails & Safety
    # ------------------------------------------------------------------

    @_hook_safe
    async def before_model_call(self, ctx: Any) -> None:
        """Check user input for PII + prompt injection / jailbreak before LLM call."""
        logger.info("[AuditRail] before_model_call hook fired")

        messages = None
        if hasattr(ctx, "inputs"):
            inputs = ctx.inputs
            if hasattr(inputs, "messages"):
                messages = inputs.messages
        if messages is None:
            messages = getattr(ctx, "messages", None)

        if messages is None:
            return

        # Get the LAST user message (latest user input)
        user_input = ""
        for msg in messages:
            role = getattr(msg, "role", None) or (msg.get("role") if isinstance(msg, dict) else "")
            if role == "user":
                content = getattr(msg, "content", None) or (msg.get("content", "") if isinstance(msg, dict) else "")
                user_input = str(content)[:4096]

        logger.info("[AuditRail] user_input extracted: %d chars, preview=%s", len(user_input), user_input[:100])

        # PII scan on user input
        pii_finding = await self._evaluate_pii(user_input)
        if pii_finding is not None:
            logger.info("[AuditRail] PII detected in user input: %s", pii_finding)
            self._audit.log_audit(
                audit_type=AuditType.PRIVACY_PII,
                details=pii_finding,
                agent_name=self._get_agent_name(),
            )
            self.block_if_set(ctx, pii_finding, scope="pii")

        # Safety check on user input (injection / jailbreak)
        finding = await self._evaluate_input_safety(user_input)
        if finding is not None:
            logger.info("[AuditRail] Safety issue detected in user input: %s", finding)
            self._audit.log_audit(
                audit_type=AuditType.GUARDRAILS_SAFETY,
                details=finding,
                agent_name=self._get_agent_name(),
            )
            self.block_if_set(ctx, finding, scope="input_safety")

        # Content safety check on user input (violence / illegal / self-harm)
        content_finding = await self._evaluate_output_safety(user_input)
        if content_finding is not None:
            logger.info("[AuditRail] Content safety issue detected in user input: %s", content_finding)
            self._audit.log_audit(
                audit_type=AuditType.GUARDRAILS_SAFETY,
                details=content_finding,
                agent_name=self._get_agent_name(),
            )
            self.block_if_set(ctx, content_finding, scope="content_safety")

    @_hook_safe
    async def after_model_call(self, ctx: Any) -> None:
        """Scan LLM output for PII + content safety violations."""
        result = None
        if hasattr(ctx, "inputs"):
            inputs = ctx.inputs
            result = getattr(inputs, "response", None)
        if result is None:
            result = getattr(ctx, "result", None)
        if result is None:
            return

        output_text = getattr(result, "content", "") or ""

        # PII scan
        pii_finding = await self._evaluate_pii(output_text)
        if pii_finding is not None:
            self._audit.log_audit(
                audit_type=AuditType.PRIVACY_PII,
                details=pii_finding,
                agent_name=self._get_agent_name(),
            )

        # Content safety
        safety_finding = await self._evaluate_output_safety(output_text)
        if safety_finding is not None:
            self._audit.log_audit(
                audit_type=AuditType.GUARDRAILS_SAFETY,
                details=safety_finding,
                agent_name=self._get_agent_name(),
            )

    # ------------------------------------------------------------------
    # Detector delegates
    # ------------------------------------------------------------------

    async def _evaluate_tool_risk(self, tool_name: str, arguments: str) -> Optional[dict]:
        """Evaluate tool call risk: SQL keywords, shell commands, sensitive paths."""
        return await self._tool_risk.evaluate(tool_name, arguments)

    async def _evaluate_tool_result(self, result: Any) -> Optional[dict]:
        """Check tool result for permission denied or cross-tenant access."""
        return await self._tool_risk.evaluate_result(result)

    async def _evaluate_input_safety(self, user_input: str) -> Optional[dict]:
        """Detect prompt injection and jailbreak attempts in user input."""
        return await self._safety_filter.check_input(user_input)

    async def _evaluate_pii(self, text: str) -> Optional[dict]:
        """Scan text for PII: ID card, phone, API key, email, bank card."""
        return await self._pii_scanner.scan(text)

    async def _evaluate_output_safety(self, text: str) -> Optional[dict]:
        """Check model output for violence, illegal activity, self-harm content."""
        return await self._safety_filter.check_output(text)

    # ------------------------------------------------------------------
    # Block enforcement
    # ------------------------------------------------------------------

    def block_if_set(self, ctx: Any, finding: dict, *, scope: str) -> None:
        """If finding.action == "block", request the agent loop to force-finish.

        Calls ``ctx.request_force_finish(result_dict)`` provided by the
        framework's AgentCallbackContext (see
        openjiuwen.core.single_agent.rail.base). The @rail wrapper around
        _do_model_call / _do_tool_call checks has_force_finish_request
        AFTER all before-hooks run and skips the wrapped call when set.

        The result dict follows the framework's invoke-result contract so it
        surfaces in the frontend chat: ReActAgent._write_invoke_result_to_stream
        reads ``result["output"]`` as the answer text and ``result["result_type"]``
        as the category tag. Without ``output`` the user would see an empty
        bubble on a block.

        The ``output`` text is deliberately generic — rule names, detection
        types and matched fragments are NOT put in the result dict (which flows
        toward the frontend). The full detail is written to the local WARNING
        log here and to the Loki audit log via AuditLogger.log_audit in the
        calling hook.

        Exceptions are left to propagate to ``@_hook_safe`` on the calling
        hook (before_model_call / before_tool_call), which swallows them.
        """
        action = (finding or {}).get("action")
        if action != "block":
            return

        rule_name = (finding or {}).get("rule_name", "")
        detection_type = (finding or {}).get("detection_type") or (finding or {}).get("pii_types") or scope
        matched_fragment = (finding or {}).get("matched_fragment", "")

        # Detailed log for ops/devs — the full picture lives in logs, not in
        # the user-facing result. matched_fragment is truncated to avoid
        # dumping large payloads into log lines.
        logger.warning(
            "[AuditRail] block enforced: scope=%s rule=%s detection=%s "
            "action=%s fragment=%.120s -> force_finish requested",
            scope, rule_name, detection_type, action, matched_fragment,
        )

        # Minimal user-facing result — only framework contract fields + a
        # coarse block flag/scope. No rule_name / detection_type / matched_fragment
        # here: this dict is returned by invoke() and could surface downstream.
        result = {
            "output": "您的请求内容不符合安全规范，已被拒绝处理。",
            "result_type": "blocked",
            "finish_reason": "blocked",
            "blocked": True,
            "block_scope": scope,
        }
        ctx.request_force_finish(result)
