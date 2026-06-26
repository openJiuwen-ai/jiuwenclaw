# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""tokenjuice Python port — rule classification and matching.

Port of src/core/classify.ts. Matches a ToolExecutionInput against all
compiled rules using 6 match dimensions, scores by specificity,
and returns the best match.
"""

from __future__ import annotations

import logging
import re

from .command import derive_candidates, get_command_name, get_git_subcommand
from .types import (
    ClassificationResult,
    CommandMatchCandidate,
    CompiledRule,
    JsonRule,
    ToolExecutionInput,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Word-boundary-aware substring matching
# ---------------------------------------------------------------------------

_WORD_CHAR_RE = re.compile(r"[A-Za-z0-9_]")


def _is_word_char(c: str | None) -> bool:
    return c is not None and bool(_WORD_CHAR_RE.match(c))


def _includes_command_part(command: str, part: str) -> bool:
    """Word-boundary-aware check: does *command* contain *part*?"""
    if not part:
        return True

    from_index = 0
    while from_index <= len(command):
        index = command.find(part, from_index)
        if index == -1:
            return False

        end = index + len(part)
        part_starts_word = _is_word_char(part[0])
        part_ends_word = _is_word_char(part[-1])
        prev_char = command[index - 1] if index > 0 else None
        next_char = command[end] if end < len(command) else None

        left_ok = (not part_starts_word) or (not _is_word_char(prev_char))
        right_ok = (not part_ends_word) or (not _is_word_char(next_char))

        if left_ok and right_ok:
            return True

        from_index = index + 1

    return False


# ---------------------------------------------------------------------------
# Rule matching
# ---------------------------------------------------------------------------


def matches_rule(rule: JsonRule, input_: ToolExecutionInput) -> bool:
    """Check all 6 match dimensions. All present dimensions must pass."""
    argv = input_.argv or []
    command = input_.command or ""
    tool_name = input_.tool_name

    # 1. toolNames
    if rule.match.tool_names is not None:
        if tool_name not in rule.match.tool_names:
            return False

    # 2. argv0
    if rule.match.argv0 is not None:
        argv0 = argv[0] if argv else ""
        if argv0 not in rule.match.argv0:
            return False

    # 3. gitSubcommands
    if rule.match.git_subcommands is not None:
        git_sub = get_git_subcommand(argv) or ""
        if git_sub not in rule.match.git_subcommands:
            return False

    # 4. argvIncludes — ALL groups must have ALL parts in argv
    if rule.match.argv_includes is not None:
        for group in rule.match.argv_includes:
            if not all(part in argv for part in group):
                return False

    # 5. argvIncludesAny — ANY group must have ALL parts in argv
    if rule.match.argv_includes_any is not None:
        if not any(all(part in argv for part in group) for group in rule.match.argv_includes_any):
            return False

    # 6. commandIncludes — ALL parts must word-boundary-match
    if rule.match.command_includes is not None:
        if not all(_includes_command_part(command, part) for part in rule.match.command_includes):
            return False

    # 7. commandIncludesAny — ANY part must word-boundary-match
    if rule.match.command_includes_any is not None:
        if not any(_includes_command_part(command, part) for part in rule.match.command_includes_any):
            return False

    return True


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def score_rule(rule: JsonRule) -> int:
    """Specificity-weighted score for tiebreaking."""
    m = rule.match
    return (
        (rule.priority or 0) * 1000
        + len(m.argv0 or []) * 100
        + len(m.git_subcommands or []) * 60
        + sum(len(parts) for parts in (m.argv_includes or [])) * 40
        + sum(len(parts) for parts in (m.argv_includes_any or [])) * 35
        + len(m.command_includes or []) * 25
        + len(m.command_includes_any or []) * 20
        + len(m.tool_names or []) * 10
    )


# ---------------------------------------------------------------------------
# Candidate priority
# ---------------------------------------------------------------------------

_CANDIDATE_PRIORITY = {"original": 0, "shell-body": 1, "effective": 2}


def _get_candidate_priority(candidate: CommandMatchCandidate) -> int:
    return _CANDIDATE_PRIORITY.get(candidate.source, 0)


# ---------------------------------------------------------------------------
# Helper: does a rule use command-text matchers?
# ---------------------------------------------------------------------------


def _uses_command_text_matcher(rule: JsonRule) -> bool:
    return bool(rule.match.command_includes or rule.match.command_includes_any)


# ---------------------------------------------------------------------------
# Best match
# ---------------------------------------------------------------------------


def _evaluate_rules_for_candidate(
    candidate,
    cand_input,
    rules,
    highest_priority,
    specific_matches,
    fallback
) -> dict | None:
    for compiled in rules:
        if not matches_rule(compiled.rule, cand_input):
            continue

        if compiled.rule.id == "generic/fallback":
            if fallback is None:
                fallback = {
                    "rule": compiled,
                    "candidate": candidate,
                    "candidate_input": cand_input,
                }
            continue

        # Skip builtin command-text rules on lower-priority candidates
        if (compiled.source == "builtin"
                and _uses_command_text_matcher(compiled.rule)
                and _get_candidate_priority(candidate) < highest_priority):
            continue

        specific_matches.append({
            "rule": compiled,
            "candidate": candidate,
            "candidate_input": cand_input,
        })
    return fallback


def find_best_match(
    input_: ToolExecutionInput,
    rules: list[CompiledRule],
) -> dict | None:
    """Find the best matching rule across all candidates.

    Returns a dict with keys: rule, candidate, candidate_input, classification.
    Returns None if no rule matches (not even fallback).
    """
    candidates = derive_candidates(input_)
    highest_priority = max((_get_candidate_priority(c) for c in candidates), default=0)

    specific_matches: list[dict] = []
    fallback: dict | None = None

    for candidate in candidates:
        # Build candidate input
        cand_input = ToolExecutionInput(
            tool_name=input_.tool_name,
            command=candidate.command,
            argv=candidate.argv,
            stdout=input_.stdout,
            stderr=input_.stderr,
            combined_text=input_.combined_text,
            exit_code=input_.exit_code,
            cwd=input_.cwd,
        )

        fallback = _evaluate_rules_for_candidate(
            candidate, cand_input, rules, highest_priority, specific_matches, fallback
        )

    if specific_matches:
        # Sort: score DESC, candidate priority DESC, rule.id ASC
        specific_matches.sort(key=lambda m: (
            -score_rule(m["rule"].rule),
            -_get_candidate_priority(m["candidate"]),
            m["rule"].rule.id,
        ))
        best = specific_matches[0]
        best["classification"] = _build_classification(best["rule"], best["candidate"])
        return best

    if fallback:
        fallback["classification"] = _build_classification(fallback["rule"], fallback["candidate"])
        return fallback

    return None


# ---------------------------------------------------------------------------
# Classification result builder
# ---------------------------------------------------------------------------


def _build_classification(
    compiled: CompiledRule,
    candidate: CommandMatchCandidate,
) -> ClassificationResult:
    rule = compiled.rule
    is_fallback = rule.id == "generic/fallback"
    return ClassificationResult(
        family=rule.family,
        confidence=0.2 if is_fallback else 0.9,
        matched_reducer=rule.id,
        matched_via=candidate.source,
        matched_command=candidate.command or " ".join(candidate.argv),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_execution(
    input_: ToolExecutionInput,
    rules: list[CompiledRule],
    forced_rule_id: str | None = None,
) -> tuple[ClassificationResult, CompiledRule, ToolExecutionInput]:
    """Classify input and return (classification, matched_rule, candidate_input).

    If *forced_rule_id* is set, skip matching and use that rule directly.
    """
    # Forced classifier
    if forced_rule_id:
        forced = next((r for r in rules if r.rule.id == forced_rule_id), None)
        if forced:
            return (
                ClassificationResult(family=forced.rule.family, confidence=1.0, matched_reducer=forced.rule.id),
                forced,
                input_,
            )

    # Normal matching
    match = find_best_match(input_, rules)
    if match:
        return match["classification"], match["rule"], match["candidate_input"]

    # No match at all — return generic
    fallback = next((r for r in rules if r.rule.id == "generic/fallback"), None)
    if fallback:
        return (
            ClassificationResult(family="generic", confidence=0.2, matched_reducer="generic/fallback"),
            fallback,
            input_,
        )

    raise ValueError("no rules loaded — cannot classify")
