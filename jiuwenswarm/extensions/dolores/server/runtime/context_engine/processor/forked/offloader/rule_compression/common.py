from __future__ import annotations

import re

from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.forked.offloader.rule_compression.types import RuleContext


ERROR_RE = re.compile(
    r"\b(error|failed|failure|traceback|exception|warn|warning)\b",
    re.IGNORECASE,
)
_DISPLAY_LINE_PREFIX_RE = re.compile(
    r"(?m)^(?P<prefix>\s*(?:"
    r"\d+[\t ]+|"
    r"[|>:#-]\s*\d+\s*[|:.)\]-]?\s*|"
    r"(?:line|row)\s+\d+\s*[:|.)\]-]\s*"
    r"))(?P<body>\S.*)$",
    re.IGNORECASE,
)
_DISPLAY_LINE_PREFIX_PRESERVE_WS_RE = re.compile(
    r"(?m)^(?P<prefix>\s*(?:"
    r"\d+\t|"
    r"\d+ +|"
    r"[|>:#-]\s*\d+\s*[|:.)\]-]?\s*|"
    r"(?:line|row)\s+\d+\s*[:|.)\]-]\s*"
    r"))(?P<body>.*\S.*)$",
    re.IGNORECASE,
)


def count_tokens(text: str, ctx: RuleContext) -> int:
    if ctx.count_tokens is not None:
        return max(ctx.count_tokens(text), 1)
    return max(len(text) // 3, 1)


def meets_savings_ratio(original: str, candidate: str, ctx: RuleContext) -> bool:
    original_tokens = count_tokens(original, ctx)
    candidate_tokens = count_tokens(candidate, ctx)
    if original_tokens <= 0:
        return False
    return 1 - candidate_tokens / original_tokens >= ctx.min_savings_ratio


def fits_budget_and_saves(original: str, candidate: str, ctx: RuleContext) -> bool:
    if count_tokens(candidate, ctx) > ctx.max_tokens:
        return False
    return meets_savings_ratio(original, candidate, ctx)


def strip_display_line_prefixes(content: str) -> str:
    """Remove line-display prefixes added by tools such as read_file or grep."""
    return _strip_display_line_prefixes(content, preserve_body_whitespace=False)


def strip_display_line_prefixes_preserving_body_whitespace(content: str) -> str:
    """Remove display prefixes while preserving leading body whitespace."""
    return _strip_display_line_prefixes(content, preserve_body_whitespace=True)


def _strip_display_line_prefixes(content: str, *, preserve_body_whitespace: bool) -> str:
    if not content:
        return content
    pattern = _DISPLAY_LINE_PREFIX_PRESERVE_WS_RE if preserve_body_whitespace else _DISPLAY_LINE_PREFIX_RE
    matches = list(pattern.finditer(content))
    lines = content.splitlines()
    non_empty_lines = [line for line in lines if line.strip()]
    if not matches or not non_empty_lines:
        return content
    if len(matches) / len(non_empty_lines) < 0.3:
        return content
    return pattern.sub(r"\g<body>", content)
