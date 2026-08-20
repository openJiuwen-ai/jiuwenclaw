# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""PII scanner — DB-driven, async."""

from __future__ import annotations

from typing import Any, Optional

from jiuwenswarm.telemetry.audit.detectors.base import BaseDetector


def _mask(value: str) -> str:
    if len(value) <= 4:
        return "*" * len(value)
    return value[:2] + "*" * (len(value) - 4) + value[-2:]


class PIIScanner(BaseDetector):
    _detector_type = "pii"

    async def scan(self, text: str) -> Optional[dict]:
        await self._maybe_reload()

        if not text:
            return None

        findings: dict[str, list[str]] = {}
        actions: set[str] = set()

        with self._lock:
            rules = list(self._compiled)

        for rule_dict, pattern in rules:
            matches = pattern.findall(text)
            if matches:
                pii_type = rule_dict.get("rule_name", "unknown")
                if pii_type not in findings:
                    findings[pii_type] = []
                for m in matches:
                    masked = _mask(str(m))
                    if masked not in findings[pii_type]:
                        findings[pii_type].append(masked)
                actions.add(rule_dict.get("action", "warn"))

        if not findings:
            return None

        pii_types = list(findings.keys())
        total_count = sum(len(v) for v in findings.values())
        samples = {t: findings[t][:3] for t in pii_types}

        # most-severe action among matched rules: block > warn > log
        if "block" in actions:
            action = "block"
        elif "warn" in actions:
            action = "warn"
        else:
            action = "log"

        return {
            "pii_types": pii_types,
            "pii_count": total_count,
            "samples": str(samples),
            "masked": True,
            "action": action,
        }
