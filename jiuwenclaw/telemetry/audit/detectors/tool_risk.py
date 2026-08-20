# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Tool risk evaluator — DB-driven, async."""

from __future__ import annotations

from typing import Any, Optional

from jiuwenclaw.telemetry.audit.detectors.base import BaseDetector

_HIGH_RISK_TOOLS = frozenset({
    "shell_command", "bash", "sh", "execute_command",
    "sql_executor", "sql", "database_query",
    "send_email", "send_message",
    "write_file", "file_write",
})


class ToolRiskEvaluator(BaseDetector):
    _detector_type = "tool_risk"

    async def evaluate(self, tool_name: str, arguments: str) -> Optional[dict]:
        await self._maybe_reload()

        findings = []
        with self._lock:
            rules = list(self._compiled)

        for rule_dict, pattern in rules:
            m = pattern.search(arguments)
            if m:
                findings.append({
                    "risk_level": rule_dict.get("severity", "medium"),
                    "rule_name": rule_dict.get("rule_name", ""),
                    "action": rule_dict.get("action", "log"),
                    "matched_pattern": m.group()[:200],
                })

        if tool_name.lower() in _HIGH_RISK_TOOLS:
            if not any(f["risk_level"] == "high" for f in findings):
                findings.append({
                    "risk_level": "medium",
                    "rule_name": "high_risk_tool",
                    "action": "log",
                    "matched_pattern": tool_name,
                })

        if not findings:
            return None

        findings.sort(key=lambda f: 0 if f["risk_level"] == "high" else 1)
        top = findings[0]
        return {
            "risk_level": top["risk_level"],
            "rule_name": top["rule_name"],
            "action": top.get("action", "log"),
            "matched_pattern": top["matched_pattern"],
            "blocked": False,
            "tool_name": tool_name,
            "finding_count": len(findings),
        }

    async def evaluate_result(self, result: Any) -> Optional[dict]:
        result_str = str(result)[:4096] if result is not None else ""
        result_lower = result_str.lower()

        if "permission denied" in result_lower or "access denied" in result_lower:
            return {
                "risk_level": "medium",
                "rule_name": "permission_denied",
                "matched_pattern": "permission/access denied in result",
                "blocked": True,
                "tool_name": "",
            }

        if "forbidden" in result_lower and "tenant" in result_lower:
            return {
                "risk_level": "high",
                "rule_name": "cross_tenant_access",
                "matched_pattern": "tenant forbidden in result",
                "blocked": True,
                "tool_name": "",
            }

        return None
