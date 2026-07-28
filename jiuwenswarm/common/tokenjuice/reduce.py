# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""tokenjuice Python port — main compression pipeline.

Port of src/core/reduce.ts. This is the heart of the system.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from .classify import classify_execution
from .command import is_file_inspection_command, normalize_execution_input
from .formatters import (
    rewrite_git_diff_lines,
    rewrite_git_status_lines,
    rewrite_gh_lines,
    rewrite_search_lines,
)
from .rules import load_rules
from .text import (
    clamp_text,
    clamp_text_middle,
    count_text_chars,
    dedupe_adjacent,
    head_tail,
    normalize_lines,
    pluralize,
    strip_ansi,
    trim_empty_edges,
)
from .types import (
    CompactResult,
    CompactionMetadata,
    ClassificationResult,
    CompiledRule,
    NO_COMPACTION,
    ReduceOptions,
    ToolExecutionInput,
    JsonRule,
    create_compaction,
    merge_compaction,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


TINY_OUTPUT_MAX_CHARS = 240
SMALL_OUTPUT_PASSTHROUGH_MIN_SAVED_CHARS = 120
SMALL_OUTPUT_PASSTHROUGH_MAX_RATIO = 0.75


def _build_raw_text(input_: ToolExecutionInput) -> str:
    if input_.combined_text:
        return input_.combined_text
    stdout = input_.stdout or ""
    stderr = input_.stderr or ""
    if not stdout:
        return stderr
    if not stderr:
        return stdout
    return f"{stdout}\n{stderr}"


def _apply_filters(
    compiled: CompiledRule,
    lines: list[str],
    no_omit: bool
) -> tuple[list[str], list[str]]:
    # --- Filter: skipPatterns ---
    if not no_omit and compiled.skip_patterns:
        lines = [line for line in lines if not any(p.search(line) for p in compiled.skip_patterns)]

    # Snapshot for preKeep counters
    counter_lines = list(lines)

    # --- Filter: keepPatterns ---
    if not no_omit and compiled.keep_patterns:
        kept = [line for line in lines if any(p.search(line) for p in compiled.keep_patterns)]
        if kept:
            lines = kept

    return lines, counter_lines


def _apply_post_filters(
    rule: JsonRule,
    lines: list[str],
    counter_lines: list[str],
    no_omit: bool
) ->tuple[list[str], list[str]]:
    if rule.transforms and rule.transforms.trim_empty_edges:
        counter_lines = trim_empty_edges(counter_lines)
        lines = trim_empty_edges(lines)

    if not no_omit and rule.transforms and rule.transforms.dedupe_adjacent:
        counter_lines = dedupe_adjacent(counter_lines)
        lines = dedupe_adjacent(lines)

    return lines, counter_lines



def _apply_domain_rewrites(
    rule: JsonRule,
    lines: list[str],
    counter_lines: list[str],
    input_: ToolExecutionInput,
    no_omit: bool
) -> tuple[list[str], list[str], CompactionMetadata | None]:
    rewrite_compaction: CompactionMetadata | None = None

    if rule.id == "git/status":
        counter_lines = rewrite_git_status_lines(counter_lines)
        lines = rewrite_git_status_lines(lines)

    pre_rewrite_lines = list(lines)

    if rule.id == "cloud/gh":
        result = rewrite_gh_lines(lines, input_, no_omit=no_omit)
        lines = result["lines"]
        rewrite_compaction = merge_compaction(rewrite_compaction, result.get("compaction"))

    if rule.id == "search/rg":
        result = rewrite_search_lines(lines, no_omit=no_omit)
        lines = result["lines"]
        rewrite_compaction = merge_compaction(rewrite_compaction, result.get("compaction"))

    if rule.id == "git/diff":
        result = rewrite_git_diff_lines(lines, no_omit=no_omit)
        lines = result["lines"]
        rewrite_compaction = merge_compaction(rewrite_compaction, result.get("compaction"))

    return lines, counter_lines, pre_rewrite_lines, rewrite_compaction


def _calculate_facts(
    compiled: CompiledRule,
    rule: JsonRule,
    lines: list[str],
    pre_rewrite_lines: list[str],
    counter_lines: list[str]
) -> dict:
    facts: dict[str, int] = {}
    for counter in compiled.counters:
        if rule.counter_source == "preKeep":
            fact_lines = counter_lines
        elif rule.id == "git/diff":
            fact_lines = pre_rewrite_lines
        else:
            fact_lines = lines

        # git/diff: skip +++ and --- for added/removed line counters
        if rule.id == "git/diff" and counter.name in ("added line", "removed line"):
            fact_lines = [l for l in fact_lines if not l.startswith("+++") and not l.startswith("---")]

        facts[counter.name] = sum(1 for line in fact_lines if counter.pattern.search(line))

    return facts

def _apply_rule(
    compiled: CompiledRule,
    input_: ToolExecutionInput,
    raw_text: str,
    *,
    no_omit: bool = False,
) -> dict:
    rule = compiled.rule
    facts: dict[str, int] = {}

    # --- Transforms (pre-filter) ---
    text = raw_text

    # prettyPrintJson
    if rule.transforms and rule.transforms.pretty_print_json:
        try:
            parsed = json.loads(text)
            text = json.dumps(parsed, indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            pass

    # Normalize lines
    lines = normalize_lines(text)

    # stripAnsi
    if rule.transforms and rule.transforms.strip_ansi:
        lines = normalize_lines(strip_ansi("\n".join(lines)))

    # --- Output matching (early exit) ---
    output_text = "\n".join(trim_empty_edges(lines))
    if not no_omit and compiled.output_matches:
        for om in compiled.output_matches:
            if om.pattern.search(output_text):
                return {"summary": om.message, "facts": facts, "compaction": NO_COMPACTION}

    # --- Filter ---
    lines, counter_lines = _apply_filters(compiled, lines, no_omit)

    # --- Transforms (post-filter) ---
    lines, counter_lines = _apply_post_filters(rule, lines, counter_lines, no_omit)

    # --- Domain-specific rewriters ---
    lines, counter_lines, pre_rewrite_lines, rewrite_compaction = _apply_domain_rewrites(rule, lines, counter_lines, input_, no_omit)

    # --- Counters ---
    facts = _calculate_facts(compiled, rule, lines, pre_rewrite_lines, counter_lines)
    if not lines and rule.on_empty:
        return {
            "summary": rule.on_empty,
            "facts": facts,
            "compaction": rewrite_compaction or NO_COMPACTION,
        }

    # --- Head/tail summarization ---
    # JSON 里没写 summarize/failure → head/tail=None → 默认不截断(h=0,t=0)
    exit_code = input_.exit_code or 0
    if exit_code != 0 and rule.failure and rule.failure.preserve_on_failure:
        h = rule.failure.head or 0
        t = rule.failure.tail or 0
    else:
        h = (rule.summarize.head if rule.summarize and rule.summarize.head is not None else 0)
        t = (rule.summarize.tail if rule.summarize and rule.summarize.tail is not None else 0)

    compacted = head_tail(lines, h, t, no_omit=no_omit)
    summary = "\n".join(compacted["lines"]).strip()

    return {
        "summary": summary,
        "facts": facts,
        "compaction": merge_compaction(rewrite_compaction, compacted.get("compaction")),
    }



def _format_inline(
    classification_family: str,
    input_: ToolExecutionInput,
    summary: str,
    facts: dict[str, int],
    *,
    no_omit: bool = False,
) -> str:
    fact_parts = [
        pluralize(count, name)
        for name, count in facts.items()
        if count > 0
    ]

    result_lines: list[str] = []

    exit_code = input_.exit_code or 0
    if exit_code != 0:
        result_lines.append(f"exit {exit_code}")

    # Facts inclusion logic
    should_include = (
        classification_family == "search"
        or (
            classification_family not in ("git-status", "help")
            and (no_omit or "omitted" in summary)
        )
        or (classification_family == "test-results" and exit_code != 0)
    )

    if should_include and fact_parts:
        result_lines.append(", ".join(fact_parts))

    result_lines.append(summary)
    return "\n".join(result_lines).strip()


def _build_passthrough_text(input_: ToolExecutionInput, raw_text: str) -> str:
    text = strip_ansi(raw_text).strip()
    exit_code = input_.exit_code or 0
    if exit_code != 0:
        return f"exit {exit_code}\n{text}"
    return text


def _select_inline_text(
    classification_family: str,
    input_: ToolExecutionInput,
    raw_text: str,
    compact_text: str,
    max_inline_chars: int,
    compact_compaction: CompactionMetadata | None,
    *,
    no_omit: bool = False,
) -> dict:
    passthrough = _build_passthrough_text(input_, raw_text)
    raw_chars = count_text_chars(strip_ansi(raw_text))
    compact_chars = count_text_chars(compact_text)
    passthrough_chars = count_text_chars(passthrough)

    # 1. noOmit → always compact
    if no_omit:
        return {"text": compact_text, "compaction": compact_compaction}

    # 2. git-status → always compact
    if classification_family == "git-status":
        return {"text": compact_text, "compaction": compact_compaction}

    # 3. raw fits AND compact didn't help → passthrough
    if raw_chars <= max_inline_chars and compact_chars >= raw_chars:
        return {"text": passthrough, "compaction": NO_COMPACTION}

    # 4. passthrough is tiny → passthrough
    passthrough_limit = max_inline_chars if classification_family == "help" else TINY_OUTPUT_MAX_CHARS
    if passthrough_chars <= passthrough_limit:
        return {"text": passthrough, "compaction": NO_COMPACTION}

    # 5. passthrough shorter or equal → passthrough
    if passthrough_chars <= compact_chars:
        return {"text": passthrough, "compaction": NO_COMPACTION}

    # 6. compact is smaller → compact
    return {"text": compact_text, "compaction": compact_compaction}


def _compact_json_text(raw_text: str, max_chars: int, *, no_omit: bool = False) -> dict | None:
    """Try to minify and clamp JSON output."""
    from .text import clip_middle_with_hash, compact_whitespace

    trimmed = raw_text.strip()
    if not (trimmed.startswith("{") or trimmed.startswith("[")):
        return None

    try:
        parsed = json.loads(trimmed)
    except json.JSONDecodeError:
        return None

    minified = json.dumps(parsed, separators=(",", ":"), ensure_ascii=False)
    clipped = clip_middle_with_hash(minified, max_chars, no_omit=no_omit)
    return clipped


def _compact_fallback_result(
    classification: ClassificationResult,
    reducer_input: ToolExecutionInput,
    raw_text: str,
    measured_raw: int,
    max_inline: int,
    opts: ReduceOptions
) -> CompactResult | None:
    exit_code = reducer_input.exit_code or 0
    exit_prefix = f"exit {exit_code}\n" if exit_code != 0 else ""
    json_budget = max(0, max_inline - count_text_chars(exit_prefix))
    json_output = _compact_json_text(raw_text, json_budget, no_omit=opts.no_omit)
    if json_output:
        inline = f"{exit_prefix}{json_output['text']}"
        reduced = count_text_chars(inline)
        return CompactResult(
            inline_text=inline,
            stats={"raw_chars": measured_raw, "reduced_chars": reduced, "ratio": reduced / max(measured_raw, 1)},
            classification=classification,
        )
    return None


def _final_clamping(
    classification: ClassificationResult,
    matched_rule: CompiledRule,
    reducer_input: ToolExecutionInput,
    raw_text: str,
    measured_raw: int,
    max_inline: int,
    opts: ReduceOptions
) -> CompactResult | None:
    result = _apply_rule(matched_rule, reducer_input, raw_text, no_omit=opts.no_omit)
    summary = result["summary"] or "(no output)"
    facts = result["facts"]
    compaction = result.get("compaction")

    compact_text = _format_inline(
        classification.family, reducer_input, summary, facts, no_omit=opts.no_omit
    )

    selected = _select_inline_text(
        classification.family, reducer_input, raw_text, compact_text,
        max_inline, compaction, no_omit=opts.no_omit,
    )
    
    if classification.family == "help" or "\n" in selected["text"]:
        clamped = clamp_text_middle(selected["text"], max_inline, no_omit=opts.no_omit)
    else:
        clamped = clamp_text(selected["text"], max_inline, no_omit=opts.no_omit)

    reduced = count_text_chars(clamped["text"])

    return CompactResult(
        inline_text=clamped["text"],
        preview_text=summary if summary != "(no output)" else None,
        facts=facts if facts else None,
        compaction=merge_compaction(selected.get("compaction"), clamped.get("compaction")),
        stats={
            "raw_chars": measured_raw,
            "reduced_chars": reduced,
            "ratio": reduced / max(measured_raw, 1),
        },
        classification=classification,
    )


def reduce_execution(
    input_: ToolExecutionInput,
    rules: list[CompiledRule] | None = None,
    opts: ReduceOptions | None = None,
) -> CompactResult:
    """Main compression pipeline entry point.

    If *rules* is None, loads builtin rules automatically.
    """
    if opts is None:
        opts = ReduceOptions()
    if rules is None:
        rules = load_rules(cwd=opts.cwd)

    max_inline = opts.max_inline_chars

    # 1. Normalize input
    input_ = normalize_execution_input(input_)
    raw_text = _build_raw_text(input_)
    measured_raw = count_text_chars(strip_ansi(raw_text))

    # 2. Classify
    classification, matched_rule, reducer_input = classify_execution(
        input_, rules, forced_rule_id=opts.classifier
    )

    # 3. Raw mode → return as-is
    if opts.raw:
        return CompactResult(
            inline_text=raw_text,
            stats={"raw_chars": measured_raw, "reduced_chars": measured_raw, "ratio": 1.0},
            classification=classification,
        )

    # 4. File content inspection → pass through raw
    if classification.matched_reducer == "generic/fallback" and is_file_inspection_command(reducer_input):
        return CompactResult(
            inline_text=raw_text,
            stats={"raw_chars": measured_raw, "reduced_chars": measured_raw, "ratio": 1.0},
            classification=classification,
        )

    # 5. JSON compaction for generic/fallback
    if classification.matched_reducer == "generic/fallback":
        fallback_result = _compact_fallback_result(classification, reducer_input, raw_text, measured_raw, max_inline, opts)
        if fallback_result is not None:
            return fallback_result

    # 6. Final clamping
    return _final_clamping(
        classification, matched_rule, reducer_input, raw_text, measured_raw, max_inline, opts
        )
