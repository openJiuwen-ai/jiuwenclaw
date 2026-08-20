# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Safety filter — DB-driven, async.

Rules with "injection" or "jailbreak" in rule_name are treated as injection/jailbreak
(checked on user input). All other safety rules are treated as content safety
(checked on both user input and LLM output).
"""

from __future__ import annotations

from typing import Any, Optional

from jiuwenclaw.telemetry.audit.detectors.base import BaseDetector

_INJECTION_KEYWORDS = ("injection", "jailbreak")


class SafetyFilter(BaseDetector):
    _detector_type = "safety"

    async def check_input(self, user_input: str) -> Optional[dict]:
        """Check user input for prompt injection / jailbreak."""
        await self._maybe_reload()

        if not user_input:
            return None

        with self._lock:
            rules = list(self._compiled)

        for rule_dict, pattern in rules:
            rule_name = rule_dict.get("rule_name", "")
            if not any(kw in rule_name for kw in _INJECTION_KEYWORDS):
                continue
            m = pattern.search(user_input)
            if m:
                return {
                    "detection_type": "prompt_injection" if "injection" in rule_name else "jailbreak",
                    "rule_name": rule_name,
                    "risk_score": 8 if rule_dict.get("severity") == "high" else 5,
                    "action": rule_dict.get("action", "block"),
                    "matched_fragment": m.group()[:200],
                }

        return None

    async def check_output(self, text: str) -> Optional[dict]:
        """Check text for content safety violations."""
        await self._maybe_reload()

        if not text:
            return None

        with self._lock:
            rules = list(self._compiled)

        for rule_dict, pattern in rules:
            rule_name = rule_dict.get("rule_name", "")
            if any(kw in rule_name for kw in _INJECTION_KEYWORDS):
                continue
            m = pattern.search(text)
            if m:
                return {
                    "detection_type": "content_safety",
                    "category": rule_name,
                    "rule_name": rule_name,
                    "severity": rule_dict.get("severity", "high"),
                    "action": rule_dict.get("action", "block"),
                    "matched_fragment": m.group()[:200],
                }

        return None
