# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""tokenjuice Python port — rule loading, compilation, and overlay.

Port of src/core/rules.ts. Loads JSON rules from three layers
(builtin → user → project), overlays by ID (last wins), and
pre-compiles all regex patterns.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from .types import (
    CompiledCounter,
    CompiledOutputMatch,
    CompiledRule,
    JsonRule,
    RuleCounter,
    RuleFailure,
    RuleFilters,
    RuleMatch,
    RuleOutputMatch,
    RuleSummarize,
    RuleTransforms,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_BUILTIN_RULES_DIR = Path(__file__).parent / "rules"
_USER_RULES_DIR = Path.home() / ".config" / "tokenjuice" / "rules"


def _project_rules_dir(cwd: str | None = None) -> Path | None:
    if cwd is None:
        return None
    d = Path(cwd) / ".tokenjuice" / "rules"
    return d if d.is_dir() else None


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

_EXCLUDE_SUFFIXES = (".schema.json", ".fixture.json")


def _discover_rule_files(root: Path) -> list[Path]:
    """Recursively find .json rule files, excluding schemas and fixtures."""
    if not root.is_dir():
        return []

    files: list[Path] = []
    for path in sorted(root.rglob("*.json")):
        if not path.is_file():
            continue
        if path.is_symlink():
            continue
        name = path.name
        if any(name.endswith(suffix) for suffix in _EXCLUDE_SUFFIXES):
            continue
        # Ensure the file is under root (no .. escape)
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError:
            continue
        files.append(path)

    return files


# ---------------------------------------------------------------------------
# JSON → dataclass parsing
# ---------------------------------------------------------------------------


def _parse_rule_match(data: dict[str, Any]) -> RuleMatch:
    return RuleMatch(
        tool_names=data.get("toolNames"),
        argv0=data.get("argv0"),
        git_subcommands=data.get("gitSubcommands"),
        argv_includes=data.get("argvIncludes"),
        argv_includes_any=data.get("argvIncludesAny"),
        command_includes=data.get("commandIncludes"),
        command_includes_any=data.get("commandIncludesAny"),
    )


def _parse_counters(data: list[dict[str, Any]] | None) -> list[RuleCounter]:
    if not data:
        return []
    return [RuleCounter(name=c["name"], pattern=c["pattern"], flags=c.get("flags")) for c in data]


def _parse_output_matches(data: list[dict[str, Any]] | None) -> list[RuleOutputMatch]:
    if not data:
        return []
    return [RuleOutputMatch(pattern=e["pattern"], message=e["message"], flags=e.get("flags")) for e in data]


def _parse_json_rule(raw: dict[str, Any]) -> JsonRule:
    match_data = raw.get("match", {})
    match = _parse_rule_match(match_data) if match_data else RuleMatch()

    filters_data = raw.get("filters")
    filters = None
    if filters_data:
        filters = RuleFilters(
            skip_patterns=filters_data.get("skipPatterns"),
            keep_patterns=filters_data.get("keepPatterns"),
        )

    transforms_data = raw.get("transforms")
    transforms = None
    if transforms_data:
        transforms = RuleTransforms(
            strip_ansi=transforms_data.get("stripAnsi"),
            pretty_print_json=transforms_data.get("prettyPrintJson"),
            dedupe_adjacent=transforms_data.get("dedupeAdjacent"),
            trim_empty_edges=transforms_data.get("trimEmptyEdges"),
        )

    summarize_data = raw.get("summarize")
    summarize = None
    if summarize_data:
        summarize = RuleSummarize(
            head=summarize_data.get("head"),
            tail=summarize_data.get("tail"),
        )

    failure_data = raw.get("failure")
    failure = None
    if failure_data:
        failure = RuleFailure(
            preserve_on_failure=failure_data.get("preserveOnFailure"),
            head=failure_data.get("head"),
            tail=failure_data.get("tail"),
        )

    return JsonRule(
        id=raw["id"],
        family=raw["family"],
        match=match,
        description=raw.get("description"),
        priority=raw.get("priority"),
        on_empty=raw.get("onEmpty"),
        match_output=_parse_output_matches(raw.get("matchOutput")),
        counter_source=raw.get("counterSource"),
        filters=filters,
        transforms=transforms,
        summarize=summarize,
        counters=_parse_counters(raw.get("counters")),
        failure=failure,
    )


# ---------------------------------------------------------------------------
# Regex compilation
# ---------------------------------------------------------------------------


def _build_regex(pattern: str, flags: str | None = None) -> re.Pattern[str] | None:
    """Compile a regex string into a Pattern, returning None on failure."""
    re_flags = re.UNICODE
    if flags:
        if "i" in flags:
            re_flags |= re.IGNORECASE
        if "m" in flags:
            re_flags |= re.MULTILINE
        if "s" in flags:
            re_flags |= re.DOTALL
    try:
        return re.compile(pattern, re_flags)
    except re.error as exc:
        logger.warning("invalid regex %r (flags=%r): %s", pattern, flags, exc)
        return None


def _compile_rule(rule: JsonRule, source: str, path: str) -> CompiledRule:
    """Convert a JsonRule into a CompiledRule with pre-built regex objects."""
    skip_patterns: list[re.Pattern[str]] = []
    keep_patterns: list[re.Pattern[str]] = []
    counters: list[CompiledCounter] = []
    output_matches: list[CompiledOutputMatch] = []

    if rule.filters:
        for p in rule.filters.skip_patterns or []:
            compiled = _build_regex(p)
            if compiled:
                skip_patterns.append(compiled)
        for p in rule.filters.keep_patterns or []:
            compiled = _build_regex(p)
            if compiled:
                keep_patterns.append(compiled)

    for c in rule.counters or []:
        compiled = _build_regex(c.pattern, c.flags)
        if compiled:
            counters.append(CompiledCounter(name=c.name, pattern=compiled))

    for om in rule.match_output or []:
        compiled = _build_regex(om.pattern, om.flags)
        if compiled:
            output_matches.append(CompiledOutputMatch(pattern=compiled, message=om.message))

    return CompiledRule(
        rule=rule,
        source=source, 
        path=path,
        skip_patterns=skip_patterns,
        keep_patterns=keep_patterns,
        counters=counters,
        output_matches=output_matches,
    )


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------


def _sort_rules(rules: list[CompiledRule]) -> list[CompiledRule]:
    """Sort rules: generic/fallback always last, rest alphabetical by ID."""
    fallback = [r for r in rules if r.rule.id == "generic/fallback"]
    rest = sorted([r for r in rules if r.rule.id != "generic/fallback"], key=lambda r: r.rule.id)
    return rest + fallback


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

_RULES_CACHE: dict[tuple, list[CompiledRule]] = {}


def parse_rule_type(
    type: str,
    dir: Path | None = None,
    descriptors: list[tuple[JsonRule, str, str]] = []
) -> list[tuple[JsonRule, str, str]]:
    for path in _discover_rule_files(dir):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            descriptors.append((_parse_json_rule(raw), type, str(path)))
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning(f"skipping invalid {type} rule %s: %s", path, exc)
    return descriptors


def load_rules(
    cwd: str | None = None,
    include_user: bool = True,
    include_project: bool = True,
) -> list[CompiledRule]:
    """Load and compile rules from all three layers with caching."""
    cache_key = (cwd, include_user, include_project)
    if cache_key in _RULES_CACHE:
        return _RULES_CACHE[cache_key]

    # Collect descriptors: (rule, source, path)
    descriptors: list[tuple[JsonRule, str, str]] = []

    # Layer 1: Builtin
    descriptors = parse_rule_type("builtin", _BUILTIN_RULES_DIR, descriptors)

    # Layer 2: User
    if include_user:
        descriptors = parse_rule_type("user", _USER_RULES_DIR, descriptors)

    # Layer 3: Project
    if include_project:
        project_dir = _project_rules_dir(cwd)
        if project_dir:
            descriptors = parse_rule_type("project",project_dir, descriptors)

    # Overlay: last wins by ID
    by_id: dict[str, tuple[JsonRule, str, str]] = {}
    for rule, source, path in descriptors:
        by_id[rule.id] = (rule, source, path)

    # Compile
    compiled = [_compile_rule(rule, source, path) for rule, source, path in by_id.values()]

    # Sort
    result = _sort_rules(compiled)

    # Validate fallback exists
    if not any(r.rule.id == "generic/fallback" for r in result):
        logger.error("missing generic/fallback rule — compression will fail for unmatched commands")

    _RULES_CACHE[cache_key] = result
    logger.info("tokenjuice rules loaded: %d rules (builtin=%d, user=%d, project=%d)",
                len(result),
                sum(1 for r in result if r.source == "builtin"),
                sum(1 for r in result if r.source == "user"),
                sum(1 for r in result if r.source == "project"))

    return result
