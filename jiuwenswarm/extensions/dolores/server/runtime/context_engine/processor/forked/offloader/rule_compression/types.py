from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable


class ContentType(str, Enum):
    JSON_ARRAY = "JSON_ARRAY"
    GIT_DIFF = "GIT_DIFF"
    HTML = "HTML"
    SEARCH_RESULTS = "SEARCH_RESULTS"
    LOG = "LOG"
    BUILD_OUTPUT = "LOG"
    PLAIN_TEXT = "PLAIN_TEXT"


@dataclass(frozen=True)
class RuleContext:
    max_tokens: int
    head_tokens: int = 2000
    tail_tokens: int = 2000
    count_tokens: Callable[[str], int] | None = None
    min_savings_ratio: float = 0.1
    json_csv_min_density: float = 0.8
    query_terms: frozenset[str] = frozenset()
    tool_name: str | None = None
    search_max_matches_per_file: int = 5
    search_max_total_matches: int = 30
    search_max_files: int = 15
    diff_min_lines: int = 50
    diff_max_context_lines: int = 2
    diff_max_changed_lines_per_hunk: int = 8
    diff_max_changed_lines_total: int = 200
    diff_max_hunks_per_file: int = 10
    diff_max_files: int = 20
    html_min_content_chars: int = 100
    log_min_lines: int = 50
    log_error_context_lines: int = 3
    log_max_errors: int = 10
    log_max_warnings: int = 5
    log_max_stack_traces: int = 3
    log_stack_trace_max_lines: int = 20
    log_max_total_lines: int = 100


@dataclass(frozen=True)
class RuleCompressionResult:
    content: str
    content_type: ContentType
    modified: bool
    lossy: bool = False
    details: dict[str, Any] | None = None
