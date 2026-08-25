#!/usr/bin/env python3

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


PATTERNS = [
    re.compile(r"\b(SK-[A-Za-z0-9]{12,})\b"),
    re.compile(r"\b([A-Fa-f0-9]{32,})\b"),
    re.compile(r"(?i)\b(authorization\s*:\s*bearer\s+)([A-Za-z0-9._\-+/=]{12,})"),
    re.compile(r"(?i)\b(x-api-key\s*:\s*)([^\s]+)"),
    re.compile(r"(?i)\b(api[_-]?key\s*[:=]\s*)([^\s,;\"']+)"),
    re.compile(r"(?i)\b(token\s*[:=]\s*)([^\s,;\"']+)"),
    re.compile(r"(?i)\b(secret\s*[:=]\s*)([^\s,;\"']+)"),
    re.compile(r"(?i)\b(agent[_ ]?id\s*[:=]\s*)([^\s,;\"']+)"),
    re.compile(r"(?i)\b(uid\s*[:=]\s*)([^\s,;\"']+)"),
    re.compile(r"(?i)\b(session[_ -]?id\s*[:=]\s*)([^\s,;\"']+)"),
    re.compile(r"(?i)\b(cookie\s*:\s*)(.+)"),
]


def mask_value(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:6]}...{value[-4:]}"


def redact_text(text: str) -> str:
    out = text

    def replace_secret_only(match: re.Match[str]) -> str:
        value = match.group(1)
        return match.group(0).replace(value, mask_value(value))

    out = PATTERNS[0].sub(replace_secret_only, out)
    out = PATTERNS[1].sub(replace_secret_only, out)

    def replace_with_prefix(match: re.Match[str]) -> str:
        prefix = match.group(1)
        value = match.group(2)
        return f"{prefix}{mask_value(value)}"

    for pattern in PATTERNS[2:10]:
        out = pattern.sub(replace_with_prefix, out)

    def replace_cookie(match: re.Match[str]) -> str:
        prefix = match.group(1)
        return f"{prefix}[REDACTED]"

    out = PATTERNS[10].sub(replace_cookie, out)
    return out


def read_input(path: str | None) -> str:
    if not path or path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8", errors="ignore")


def main() -> int:
    parser = argparse.ArgumentParser(description="Redact likely secrets from text output.")
    parser.add_argument("path", nargs="?", help="Optional file to read. Reads stdin when omitted.")
    args = parser.parse_args()

    text = read_input(args.path)
    sys.stdout.write(redact_text(text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())