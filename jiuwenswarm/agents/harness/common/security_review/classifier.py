# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Deterministic security signal classification."""
from __future__ import annotations

import re

from jiuwenswarm.agents.harness.common.security_review.schema import (
    FailureClass,
    SecurityEvent,
    SecuritySignal,
    Severity,
)

_DANGEROUS_COMMAND = re.compile(
    r"(curl|wget)\b[^|;\n]*\|\s*(sh|bash)|rm\s+-[rf]{2}\s+/(?:\*|\s|$)|chmod\s+777",
    re.IGNORECASE,
)
_DESTRUCTIVE_FILE_OPERATION = re.compile(
    r"\b(rm|del|rd)\b\s+(?:-[A-Za-z]+\s+)?(?!/(?:\*|\s|$))[^|;&\n]+",
    re.IGNORECASE,
)
_SANDBOX_ESCAPE = re.compile(
    r"\b(?:docker|podman)\b[^|;&\n]*(?:--privileged|-v\s*/\s*:|--volume\s*/\s*:)"
    r"|\bnsenter\b|\bunshare\b|\bmount\b[^|;&\n]+/(?:proc|sys|dev)\b",
    re.IGNORECASE,
)
_SECRET_PATH = re.compile(
    r"(\.env\b|credentials?\b|(?<![A-Za-z0-9])(?:tokens?|secrets?)(?![A-Za-z0-9])"
    r"|\.ssh\b|id_rsa\b|id_ed25519\b|private[_-]?key\b)",
    re.IGNORECASE,
)
_SENSITIVE_FILE_PATH = re.compile(
    r"(^|[\"'\s:=])(?:"
    r"/etc/(?:passwd|shadow|sudoers|hosts|ssh/sshd_config)\b"
    r"|/var/run/docker\.sock\b"
    r"|/proc/(?:self/)?environ\b"
    r"|/root/(?:\.ssh|\.aws|\.kube)(?:/|\\|$)"
    r"|/Users/[^/]+/(?:\.ssh|\.aws|\.kube)(?:/|\\|$)"
    r"|/home/[^/]+/(?:\.ssh|\.aws|\.kube)(?:/|\\|$)"
    r")",
    re.IGNORECASE,
)
_PATH_TRAVERSAL = re.compile(
    r"(?:^|[\"'\s:=/\\])(?:\.\.(?:/|\\)|%2e%2e(?:%2f|%5c))"
    r"|(?:%00|%2500|\\x00|\\u0000|\x00)",
    re.IGNORECASE,
)
_NETWORK = re.compile(r"\b(curl|wget|nc|nmap|ssh|scp|ftp)\b", re.IGNORECASE)


class SecuritySignalClassifier:
    """Classify compact events without IO or LLM calls."""

    def classify(self, event: SecurityEvent) -> list[SecuritySignal]:
        if event.event_type == "tool_call":
            return self.classify_tool_call(event)
        if event.event_type == "tool_result":
            return self.classify_tool_result(event)
        if event.event_type == "model_output":
            return self.classify_model_output(event)
        return []

    def classify_tool_call(self, event: SecurityEvent) -> list[SecuritySignal]:
        text = event.arguments_digest or ""
        return self._classify_command_and_path_risk(event, text, source="tool_call")

    def classify_tool_result(self, event: SecurityEvent) -> list[SecuritySignal]:
        text = event.result_digest or ""
        signal = self._classify_result_boundary(event, text)
        return [signal] if signal is not None else []

    def classify_model_output(self, event: SecurityEvent) -> list[SecuritySignal]:
        text = event.result_digest or event.arguments_digest or ""
        lowered = text.lower().replace("\u2019", "'")
        if re.search(
            r"\b(do not|don't|never|refuse|cannot|can't)\b.{0,40}"
            r"(?:\b(run|execute|use this command)\b|\bcommand:)",
            lowered,
        ):
            return []
        has_execution_intent = bool(
            re.search(
                r"\b(?:run|execute)\b|\buse this command\b|\bhere is the command\b"
                r"|\bcommand:|执行|运行",
                lowered,
            )
        )
        if not has_execution_intent:
            return []
        return self._classify_command_and_path_risk(event, text, source="model_output")

    def classify_failure(self, text: str) -> FailureClass:
        failure_class, _, _ = self.classify_failure_detail(text)
        return failure_class

    def classify_failure_detail(self, text: str) -> tuple[FailureClass, str, str]:
        lowered = (text or "").lower()
        if "blocked" in lowered and "policy" in lowered:
            return FailureClass.BLOCKED_BY_POLICY, "regex_medium", "blocked_by_policy"
        if "denied by rule" in lowered or "tiered_policy" in lowered:
            return FailureClass.BLOCKED_BY_POLICY, "regex_medium", "blocked_by_policy"
        if "sandbox" in lowered and ("denied" in lowered or "forbid" in lowered):
            return FailureClass.SANDBOX_DENIED, "regex_medium", "sandbox_denied"
        if "network" in lowered and ("denied" in lowered or "not allowed" in lowered):
            return FailureClass.NETWORK_DENIED, "regex_medium", "network_denied"
        if _SECRET_PATH.search(lowered) and (
            "access denied" in lowered or "not allowed" in lowered
        ):
            return FailureClass.SECRET_ACCESS_DENIED, "regex_medium", "secret_access_denied"
        if "permission denied" in lowered or "access denied" in lowered:
            return FailureClass.PERMISSION_DENIED, "regex_low", "generic_permission_denied"
        return FailureClass.UNKNOWN_FAILURE, "", ""

    def _classify_command_and_path_risk(
        self,
        event: SecurityEvent,
        text: str,
        *,
        source: str,
    ) -> list[SecuritySignal]:
        signals: list[SecuritySignal] = []

        if _DANGEROUS_COMMAND.search(text):
            signals.append(
                self._signal(
                    event,
                    "dangerous_command",
                    Severity.HIGH,
                    text,
                    source=source,
                    confidence="regex_high",
                    reason_code="dangerous_command",
                )
            )
        elif _DESTRUCTIVE_FILE_OPERATION.search(text):
            signals.append(
                self._signal(
                    event,
                    "destructive_file_operation",
                    Severity.MEDIUM,
                    text,
                    source=source,
                    confidence="regex_medium",
                    reason_code="destructive_file_operation",
                )
            )
        if _SANDBOX_ESCAPE.search(text):
            signals.append(
                self._signal(
                    event,
                    "sandbox_escape_attempt",
                    Severity.CRITICAL,
                    text,
                    source=source,
                    confidence="regex_high",
                    reason_code="sandbox_escape_attempt",
                )
            )
        if _PATH_TRAVERSAL.search(text):
            signals.append(
                self._signal(
                    event,
                    "path_traversal_attempt",
                    Severity.HIGH,
                    text,
                    source=source,
                    confidence="regex_high",
                    reason_code="path_traversal",
                )
            )
        if _SENSITIVE_FILE_PATH.search(text):
            signals.append(
                self._signal(
                    event,
                    "sensitive_file_access",
                    Severity.HIGH,
                    text,
                    source=source,
                    confidence="regex_high",
                    reason_code="sensitive_file_path",
                )
            )
        if _SECRET_PATH.search(text):
            signals.append(
                self._signal(
                    event,
                    "secret_or_token_exposure",
                    Severity.HIGH,
                    text,
                    source=source,
                    confidence="regex_high",
                    reason_code="secret_path_reference",
                )
            )
        if _NETWORK.search(text) and "|" in text:
            signals.append(
                self._signal(
                    event,
                    "unsafe_network_access",
                    Severity.HIGH,
                    text,
                    source=source,
                    confidence="regex_high",
                    reason_code="network_pipe",
                )
            )
        return signals

    def _classify_result_boundary(self, event: SecurityEvent, text: str) -> SecuritySignal | None:
        lowered = (text or "").lower()

        if "[permission_rejected]" in lowered or "user rejected" in lowered:
            return self._signal(
                event,
                "user_rejected_permission",
                Severity.LOW,
                text,
                failure_class=FailureClass.PERMISSION_DENIED,
                source="tool_result",
                confidence="structured_marker",
                reason_code="user_rejected_permission",
            )
        if "[approval_required]" in lowered:
            return self._signal(
                event,
                "approval_required",
                Severity.MEDIUM,
                text,
                failure_class=FailureClass.PERMISSION_DENIED,
                source="tool_result",
                confidence="structured_marker",
                reason_code="approval_required",
            )
        if "[permission_denied]" in lowered:
            if _SECRET_PATH.search(text or ""):
                return self._permission_boundary_signal(
                    event,
                    text,
                    FailureClass.SECRET_ACCESS_DENIED,
                    "structured_marker",
                    "permission_denied_secret",
                )
            if "tiered_policy" in lowered or "denied by rule" in lowered:
                return self._permission_boundary_signal(
                    event,
                    text,
                    FailureClass.BLOCKED_BY_POLICY,
                    "structured_marker",
                    "permission_denied_policy",
                )
            return self._permission_boundary_signal(
                event,
                text,
                FailureClass.PERMISSION_DENIED,
                "structured_marker",
                "permission_denied",
                severity=Severity.MEDIUM,
            )

        failure_class, confidence, reason_code = self.classify_failure_detail(text)
        if failure_class == FailureClass.UNKNOWN_FAILURE:
            return None
        severity = Severity.HIGH if failure_class in {
            FailureClass.BLOCKED_BY_POLICY,
            FailureClass.SECRET_ACCESS_DENIED,
        } else Severity.MEDIUM
        return self._permission_boundary_signal(
            event,
            text,
            failure_class,
            confidence,
            reason_code,
            severity=severity,
        )

    def _permission_boundary_signal(
        self,
        event: SecurityEvent,
        text: str,
        failure_class: FailureClass,
        confidence: str,
        reason_code: str,
        *,
        severity: Severity = Severity.HIGH,
    ) -> SecuritySignal:
        return self._signal(
            event,
            "permission_boundary_hit",
            severity,
            text,
            failure_class=failure_class,
            source="tool_result",
            confidence=confidence,
            reason_code=reason_code,
        )

    @staticmethod
    def _signal(
        event: SecurityEvent,
        signal_type: str,
        severity: Severity,
        evidence: str,
        *,
        failure_class: FailureClass | None = None,
        source: str = "",
        confidence: str = "",
        reason_code: str = "",
    ) -> SecuritySignal:
        return SecuritySignal(
            signal_type=signal_type,
            severity=severity,
            session_id=event.session_id,
            iteration=event.iteration,
            tool_name=event.tool_name,
            failure_class=failure_class,
            evidence=(evidence or "")[:500],
            source=source,
            confidence=confidence,
            reason_code=reason_code,
        )
