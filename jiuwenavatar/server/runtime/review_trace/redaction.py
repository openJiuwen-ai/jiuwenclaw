"""Secret and local-path redaction for persisted PR review traces."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

SENSITIVE_MASK = "******"
SENSITIVE_KV_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9])"
    r"(token|access[_-]?token|api[_-]?key|authorization|password|passwd|pwd|secret)"
    r"(\s*[:=]\s*)([\"']?)[^,\s\"'\]\}]+([\"']?)"
)
SENSITIVE_BEARER_PATTERN = re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9\-._~+/]+=*")
SENSITIVE_KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9]{8,}\b")


def redact_sensitive_values(value: Any) -> Any:
    """Redact common credentials and normalize the current home path."""

    if isinstance(value, str):
        redacted = SENSITIVE_KV_PATTERN.sub(rf"\1\2{SENSITIVE_MASK}", value)
        redacted = SENSITIVE_BEARER_PATTERN.sub(rf"\1{SENSITIVE_MASK}", redacted)
        redacted = SENSITIVE_KEY_PATTERN.sub(SENSITIVE_MASK, redacted)
        home = str(Path.home())
        if home:
            redacted = redacted.replace(home, "<USER_HOME>")
            redacted = redacted.replace(home.replace("\\", "/"), "<USER_HOME>")
        return redacted
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if re.search(r"(?i)(token|secret|password|passwd|pwd|api[_-]?key|authorization)", key_text):
                result[key_text] = SENSITIVE_MASK if item not in (None, "", [], {}) else item
            else:
                result[key_text] = redact_sensitive_values(item)
        return result
    if isinstance(value, list):
        return [redact_sensitive_values(item) for item in value]
    return value
