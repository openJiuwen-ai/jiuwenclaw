# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""tokenjuice Python port — type definitions.

Mirrors the TypeScript types.ts from https://github.com/vincentkoc/tokenjuice
with Python naming conventions (snake_case). JSON serialization uses camelCase.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------

@dataclass
class ToolExecutionInput:
    """Universal input to the compression pipeline."""

    tool_name: str
    tool_call_id: str | None = None
    run_id: str | None = None
    command: str | None = None
    argv: list[str] | None = None
    args: dict[str, Any] | None = None
    cwd: str | None = None
    partial: bool | None = None
    stdout: str | None = None
    stderr: str | None = None
    combined_text: str | None = None
    exit_code: int | None = None
    started_at: float | None = None
    finished_at: float | None = None
    duration_ms: float | None = None
    metadata: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Rule descriptors (loaded from JSON)
# ---------------------------------------------------------------------------

@dataclass
class RuleMatch:
    tool_names: list[str] | None = None
    argv0: list[str] | None = None
    git_subcommands: list[str] | None = None
    argv_includes: list[list[str]] | None = None
    argv_includes_any: list[list[str]] | None = None
    command_includes: list[str] | None = None
    command_includes_any: list[str] | None = None


@dataclass
class RuleCounter:
    name: str
    pattern: str
    flags: str | None = None


@dataclass
class RuleOutputMatch:
    pattern: str
    message: str
    flags: str | None = None


@dataclass
class RuleFilters:
    skip_patterns: list[str] | None = None
    keep_patterns: list[str] | None = None


@dataclass
class RuleTransforms:
    strip_ansi: bool | None = None
    pretty_print_json: bool | None = None
    dedupe_adjacent: bool | None = None
    trim_empty_edges: bool | None = None


@dataclass
class RuleSummarize:
    head: int | None = None
    tail: int | None = None


@dataclass
class RuleFailure:
    preserve_on_failure: bool | None = None
    head: int | None = None
    tail: int | None = None


@dataclass
class JsonRule:
    """Declarative rule loaded from a JSON file."""

    id: str
    family: str
    match: RuleMatch
    description: str | None = None
    priority: int | None = None
    on_empty: str | None = None
    match_output: list[RuleOutputMatch] | None = None
    counter_source: Literal["postKeep", "preKeep"] | None = None
    filters: RuleFilters | None = None
    transforms: RuleTransforms | None = None
    summarize: RuleSummarize | None = None
    counters: list[RuleCounter] | None = None
    failure: RuleFailure | None = None


# ---------------------------------------------------------------------------
# Compiled rule (with pre-built regex objects)
# ---------------------------------------------------------------------------

@dataclass
class CompiledCounter:
    name: str
    pattern: re.Pattern[str]


@dataclass
class CompiledOutputMatch:
    pattern: re.Pattern[str]
    message: str


@dataclass
class CompiledRule:
    """A rule with all regex patterns pre-compiled."""

    rule: JsonRule
    source: Literal["builtin", "user", "project"]
    path: str
    skip_patterns: list[re.Pattern[str]] = field(default_factory=list)
    keep_patterns: list[re.Pattern[str]] = field(default_factory=list)
    counters: list[CompiledCounter] = field(default_factory=list)
    output_matches: list[CompiledOutputMatch] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Command match candidates
# ---------------------------------------------------------------------------

@dataclass
class CommandMatchCandidate:
    argv: list[str]
    source: Literal["original", "shell-body", "effective"]
    command: str | None = None


# ---------------------------------------------------------------------------
# Classification result
# ---------------------------------------------------------------------------

@dataclass
class ClassificationResult:
    family: str
    confidence: float
    matched_reducer: str | None = None
    matched_via: str | None = None
    matched_command: str | None = None


# ---------------------------------------------------------------------------
# Compaction metadata
# ---------------------------------------------------------------------------

CompactionKind = Literal[
    "head-tail-omission",
    "middle-truncation",
    "tail-truncation",
    "hashed-middle-clip",
    "git-diff-hunk-clip",
    "no-omit-head-tail-passthrough",
    "no-omit-char-clip-passthrough",
    "no-omit-domain-passthrough",
]


@dataclass
class CompactionMetadata:
    authoritative: bool
    kinds: list[str] = field(default_factory=list)


NO_COMPACTION = CompactionMetadata(authoritative=False, kinds=[])


def create_compaction(*kinds: str) -> CompactionMetadata:
    if not kinds:
        return NO_COMPACTION
    return CompactionMetadata(authoritative=True, kinds=list(dict.fromkeys(kinds)))


def create_passthrough_compaction(*kinds: str) -> CompactionMetadata:
    if not kinds:
        return NO_COMPACTION
    return CompactionMetadata(authoritative=False, kinds=list(dict.fromkeys(kinds)))


def merge_compaction(*values: CompactionMetadata | None) -> CompactionMetadata:
    present = [v for v in values if v and v.kinds]
    if not present:
        return NO_COMPACTION
    all_kinds = list(dict.fromkeys(k for v in present for k in v.kinds))
    all_auth = all(v.authoritative for v in present)
    return CompactionMetadata(authoritative=all_auth, kinds=all_kinds)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

@dataclass
class CompactResult:
    """Pipeline output returned to the caller."""

    inline_text: str
    preview_text: str | None = None
    facts: dict[str, int] | None = None
    compaction: CompactionMetadata | None = None
    stats: dict[str, Any] = field(default_factory=dict)
    classification: ClassificationResult | None = None


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------

@dataclass
class ReduceOptions:
    classifier: str | None = None
    max_inline_chars: int = 1200
    no_omit: bool = False
    raw: bool = False
    cwd: str | None = None
