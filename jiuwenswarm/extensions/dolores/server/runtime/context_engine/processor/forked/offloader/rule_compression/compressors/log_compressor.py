from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.forked.offloader.rule_compression.common import meets_savings_ratio
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.forked.offloader.rule_compression.types import (
    ContentType,
    RuleCompressionResult,
    RuleContext,
)


class LogLevel(str, Enum):
    ERROR = "error"
    FAIL = "fail"
    WARN = "warn"
    INFO = "info"
    DEBUG = "debug"
    TRACE = "trace"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class LogLine:
    position: int
    content: str
    level: LogLevel = LogLevel.UNKNOWN
    stack_trace: bool = False
    summary: bool = False
    score: int = 0


_LEVEL_PATTERNS = (
    (LogLevel.ERROR, re.compile(r"\b(?:error|fatal|critical|exception)\b", re.I)),
    (LogLevel.FAIL, re.compile(r"\b(?:fail|failed|failure)\b", re.I)),
    (LogLevel.WARN, re.compile(r"\b(?:warn|warning)\b", re.I)),
    (LogLevel.INFO, re.compile(r"\binfo\b", re.I)),
    (LogLevel.DEBUG, re.compile(r"\bdebug\b", re.I)),
    (LogLevel.TRACE, re.compile(r"\btrace\b", re.I)),
)
_SUMMARY_RE = re.compile(
    r"^(?:={3,}|-{3,}|\d+\s+(?:passed|failed|skipped|errors?|warnings?)\b|"
    r"(?:Tests?|Suites?):?\s+\d+|(?:TOTAL|Total|Summary)\b|"
    r"(?:Build|Compile|Test).*(?:succeeded|failed|complete))"
)
_FORMAT_MARKERS = {
    "pytest": ("=== FAILURES", "=== ERRORS", "test session", "collected ", " passed", " failed"),
    "npm": ("npm ERR!", "npm WARN", "npm info", "npm http"),
    "cargo": ("Compiling ", "Finished ", "Running ", "warning: ", "error[E"),
    "jest": ("PASS ", "FAIL ", "Test Suites:"),
    "make": ("make[", "make:", "gcc ", "g++ ", "clang "),
}


class LogCompressor:
    def compress(self, content: str, ctx: RuleContext) -> RuleCompressionResult:
        raw_lines = content.splitlines()
        if len(raw_lines) < ctx.log_min_lines:
            return _unchanged(content)

        lines, stack_count = _classify_lines(raw_lines, ctx.log_stack_trace_max_lines)
        selected, warnings_deduplicated = self._select(lines, ctx)
        if not selected:
            selected = _head_tail(lines, min(ctx.log_max_total_lines, 20))
        selected = _add_context(lines, selected, ctx.log_error_context_lines)
        selected = _apply_cap(selected, ctx.log_max_total_lines)
        selected_positions = {line.position for line in selected}
        omitted = len(lines) - len(selected)
        counts = _level_counts(lines)
        format_detected = _detect_format(raw_lines)
        output = [
            (
                f"[LOG compressed: format={format_detected}, "
                f"kept={len(selected)}/{len(lines)}, omitted={omitted}]"
            ),
            *[line.content for line in selected],
        ]
        if omitted:
            labels = _nonzero_level_labels(counts)
            label_text = f": {', '.join(labels)}" if labels else ""
            output.append(f"[LOG omitted: {omitted} lines omitted{label_text}]")
        candidate = "\n".join(output)
        details: dict[str, Any] = {
            "format_detected": format_detected,
            "total_lines": len(lines),
            "selected_lines": len(selected),
            "omitted_lines": omitted,
            "errors": counts[LogLevel.ERROR],
            "fails": counts[LogLevel.FAIL],
            "warnings": counts[LogLevel.WARN],
            "info": counts[LogLevel.INFO],
            "stack_traces_seen": stack_count,
            "stack_traces_retained": len(_group_traces(selected)),
            "warnings_deduplicated": warnings_deduplicated,
            "selected_line_numbers": sorted(position + 1 for position in selected_positions),
        }
        if omitted > 0 and meets_savings_ratio(content, candidate, ctx):
            return RuleCompressionResult(
                content=candidate,
                content_type=ContentType.LOG,
                modified=True,
                lossy=True,
                details=details,
            )
        return RuleCompressionResult(
            content=content,
            content_type=ContentType.LOG,
            modified=False,
            lossy=False,
            details=details,
        )

    @staticmethod
    def _select(lines: list[LogLine], ctx: RuleContext) -> tuple[list[LogLine], int]:
        errors = [line for line in lines if line.level == LogLevel.ERROR]
        fails = [line for line in lines if line.level == LogLevel.FAIL]
        warnings = [line for line in lines if line.level == LogLevel.WARN]
        deduped_warnings = _dedupe_warnings(warnings)
        traces = _group_traces(lines)
        summaries = [line for line in lines if line.summary]

        selected = _first_last_top(errors, ctx.log_max_errors)
        selected.extend(_first_last_top(fails, ctx.log_max_errors))
        selected.extend(deduped_warnings[: ctx.log_max_warnings])
        for trace in traces[: ctx.log_max_stack_traces]:
            selected.extend(trace[: ctx.log_stack_trace_max_lines])
        selected.extend(summaries)
        return _dedupe_by_position(selected), len(warnings) - len(deduped_warnings)


def _nonzero_level_labels(counts: dict[LogLevel, int]) -> list[str]:
    labels: list[str] = []
    for level, label in (
        (LogLevel.ERROR, "ERROR"),
        (LogLevel.FAIL, "FAIL"),
        (LogLevel.WARN, "WARN"),
        (LogLevel.INFO, "INFO"),
    ):
        if counts[level]:
            labels.append(f"{counts[level]} {label}")
    return labels


def _classify_lines(lines: list[str], max_trace_lines: int) -> tuple[list[LogLine], int]:
    classified: list[LogLine] = []
    trace_flavor: str | None = None
    trace_lines = 0
    traces_seen = 0

    for position, content in enumerate(lines):
        opened = _trace_flavor(content)
        if opened is not None and trace_flavor is None:
            trace_flavor = opened
            trace_lines = 0
            traces_seen += 1
        elif trace_flavor is not None and _terminates_trace(trace_flavor, content):
            trace_flavor = None
            trace_lines = 0
            opened = _trace_flavor(content)
            if opened is not None:
                trace_flavor = opened
                traces_seen += 1

        in_trace = trace_flavor is not None
        if in_trace:
            trace_lines += 1
            if trace_lines >= max_trace_lines:
                trace_flavor = None

        level = _classify_level(content)
        summary = bool(_SUMMARY_RE.search(content))
        score = {
            LogLevel.ERROR: 90,
            LogLevel.FAIL: 90,
            LogLevel.WARN: 45,
            LogLevel.INFO: 12,
            LogLevel.DEBUG: 4,
            LogLevel.TRACE: 2,
            LogLevel.UNKNOWN: 15,
        }[level]
        score += 35 if in_trace else 0
        score += 35 if summary else 0
        classified.append(LogLine(position, content, level, in_trace, summary, min(score, 100)))
    return classified, traces_seen


def _trace_flavor(line: str) -> str | None:
    stripped = line.lstrip()
    if stripped.startswith(("Traceback (most recent call last)", 'File "')):
        return "python"
    if re.match(r"at .+\(.+:\d+:\d+\)", stripped):
        return "js"
    if re.match(r"at [\w.$]+\(", stripped):
        return "java"
    if stripped.startswith("--> ") and re.search(r":\d+:\d+", stripped):
        return "rust"
    if re.match(r"\d+:\s+0x[0-9a-f]+", stripped, re.I):
        return "go"
    return None


def _terminates_trace(flavor: str, line: str) -> bool:
    stripped = line.lstrip()
    if flavor == "python":
        if not line or line[:1].isspace():
            return False
        if stripped.startswith(("Traceback", "During handling", "The above exception")):
            return False
        if re.match(r"[A-Za-z_][\w.]*?(?:Error|Exception)?:", stripped):
            return False
        return True
    if flavor in {"js", "java"}:
        return bool(line) and not stripped.startswith("at ")
    if flavor == "rust":
        return bool(line) and not stripped.startswith("--> ")
    return bool(line) and not re.match(r"\d+:\s+0x", stripped, re.I)


def _classify_level(line: str) -> LogLevel:
    for level, pattern in _LEVEL_PATTERNS:
        if pattern.search(line):
            return level
    return LogLevel.UNKNOWN


def _detect_format(lines: list[str]) -> str:
    sample = lines[:100]
    scores = {
        name: sum(1 for line in sample if any(marker in line for marker in markers))
        for name, markers in _FORMAT_MARKERS.items()
    }
    best = max(scores, key=scores.get)
    return best if scores[best] else "generic"


def _group_traces(lines: list[LogLine]) -> list[list[LogLine]]:
    groups: list[list[LogLine]] = []
    current: list[LogLine] = []
    for line in lines:
        if line.stack_trace:
            current.append(line)
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def _first_last_top(lines: list[LogLine], limit: int) -> list[LogLine]:
    if len(lines) <= limit:
        return lines
    selected = {lines[0].position: lines[0], lines[-1].position: lines[-1]}
    for line in sorted(lines, key=lambda item: (-item.score, item.position)):
        if len(selected) >= limit:
            break
        selected.setdefault(line.position, line)
    return sorted(selected.values(), key=lambda item: item.position)


def _dedupe_warnings(lines: list[LogLine]) -> list[LogLine]:
    seen: set[str] = set()
    result: list[LogLine] = []
    for line in lines:
        prefix, separator, suffix = line.content.partition(":")
        if not separator:
            prefix, separator, suffix = line.content.partition("=")
        normalized_suffix = re.sub(r"0x[0-9a-f]+", "ADDR", suffix, flags=re.I)
        normalized_suffix = re.sub(r"\d+", "N", normalized_suffix)
        normalized_suffix = re.sub(r"[/\\][\w./\\-]+", "/PATH/", normalized_suffix)
        key = f"{prefix}{separator}{normalized_suffix}"
        if key not in seen:
            seen.add(key)
            result.append(line)
    return result


def _add_context(lines: list[LogLine], selected: list[LogLine], radius: int) -> list[LogLine]:
    positions = {line.position for line in selected}
    for position in tuple(positions):
        positions.update(range(max(0, position - radius), min(len(lines), position + radius + 1)))
    return [line for line in lines if line.position in positions]


def _apply_cap(lines: list[LogLine], limit: int) -> list[LogLine]:
    if len(lines) <= limit:
        return lines
    selected = sorted(lines, key=lambda item: (-item.score, item.position))[:limit]
    return sorted(selected, key=lambda item: item.position)


def _head_tail(lines: list[LogLine], limit: int) -> list[LogLine]:
    if limit <= 0:
        return []
    if len(lines) <= limit:
        return lines
    head_count = max(limit // 2, 1)
    tail_count = max(limit - head_count, 0)
    return _dedupe_by_position([*lines[:head_count], *lines[-tail_count:]])


def _dedupe_by_position(lines: list[LogLine]) -> list[LogLine]:
    return sorted({line.position: replace(line) for line in lines}.values(), key=lambda item: item.position)


def _level_counts(lines: list[LogLine]) -> dict[LogLevel, int]:
    return {level: sum(1 for line in lines if line.level == level) for level in LogLevel}


def _unchanged(content: str) -> RuleCompressionResult:
    return RuleCompressionResult(
        content=content,
        content_type=ContentType.LOG,
        modified=False,
    )
