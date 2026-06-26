# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""tokenjuice Python port — domain-specific output rewriters.

Port of src/core/reduce-formatters.ts. These are hard-coded post-processors
that run after the generic filter/transform pipeline, keyed on rule.id.
"""

from __future__ import annotations

import json
import re

from .text import clip_middle_with_hash

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LONG_SEARCH_LINE_MAX_CHARS = 420
LONG_CHANGED_LINE_MAX_CHARS = 260
GIT_DIFF_CHANGED_LINES_PER_HUNK = 8


# ---------------------------------------------------------------------------
# git/status rewriter
# ---------------------------------------------------------------------------

_SECTION_MAP = {
    "staged": "Staged changes:",
    "unstaged": "Changes not staged:",
    "untracked": "Untracked files:",
}

_DROP_PATTERNS = [
    re.compile(r"^and have \d+ and \d+ different commits each"),
    re.compile(r"^no changes added to commit"),
    re.compile(r"^nothing added to commit"),
    re.compile(r"^nothing to commit, working tree clean$"),
    re.compile(r'^\(use "git .+" to .+\)$'),
    re.compile(r'^use "git .+" to .+'),
]

_FILE_STATUS_MAP = {
    "modified:": "M:",
    "new file:": "A:",
    "deleted:": "D:",
    "renamed:": "R:",
    "copied:": "C:",
    "typechange:": "T:",
}


def _rewrite_section_header(line: str) -> str | None:
    # Section headers
    if line.startswith("Changes not staged for commit"):
        return "Changes not staged:"
    if line.startswith("Changes to be committed"):
        return "Staged changes:"
    if line.startswith("Untracked files"):
        return "Untracked files:"
    return None


def _rewrite_porcelain_format(stripped: str) -> str | None:
    # Porcelain XY format (X and Y must be valid status chars: M A D R C U ? ! or space)
    _xy_valid = frozenset("MADRCU?! ")
    if len(stripped) < 3:
        return None
    is_instripped = stripped[0] in _xy_valid and stripped[1] in _xy_valid and stripped[2] == " "
    if is_instripped == False:
        return None
    x, y = stripped[0], stripped[1]
    if x == "?" and y == "?":
        return f"?? {stripped[3:]}"
    code = x if x != " " else y
    return f"{code}: {stripped[3:]}"


def _rewrite_git_status_line(line: str, section: str | None) -> str | None:
    """Rewrite a single git status line. Returns None to drop the line."""
    stripped = line.strip()

    # Drop lines
    for pat in _DROP_PATTERNS:
        if pat.search(line):
            return None

    # Drop hint lines (use "git ..." to ...) regardless of indentation
    if stripped.startswith('(use "') or stripped.startswith('use "'):
        return None

    result = _rewrite_section_header(line)
    if result is not None:
        return result
    
    # File status lines (indented with spaces)
    if line.startswith("  ") or line.startswith("\t"):
        for old, new in _FILE_STATUS_MAP.items():
            if old in stripped:
                path = stripped.split(old, 1)[-1].strip()
                return f"{new} {path}"

    # Short format: ?? path
    if stripped.startswith("?? "):
        return stripped

    result = _rewrite_porcelain_format(stripped)
    if result is not None:
        return result

    # Untracked file in untracked section (indented, but not hint lines)
    if section == "untracked" and line.startswith(("  ", "\t")) and not stripped.startswith("("):
        return f"?? {stripped}"

    return line


def rewrite_git_status_lines(lines: list[str]) -> list[str]:
    """Rewrite git status output lines to compact format."""
    result: list[str] = []
    section: str | None = None

    for line in lines:
        stripped = line.strip()

        # Track section
        if stripped.startswith("Changes not staged"):
            section = "unstaged"
        elif stripped.startswith("Changes to be committed"):
            section = "staged"
        elif stripped.startswith("Untracked files"):
            section = "untracked"

        rewritten = _rewrite_git_status_line(line, section)
        if rewritten is not None:
            result.append(rewritten)

    # Collapse consecutive blank lines
    collapsed: list[str] = []
    prev_blank = False
    for line in result:
        is_blank = line.strip() == ""
        if is_blank and prev_blank:
            continue
        collapsed.append(line)
        prev_blank = is_blank

    # If only "On branch ..." remains (or empty), the tree is clean
    non_branch = [l for l in collapsed if not l.startswith("On branch ")]
    if not non_branch:
        return ["working tree clean"]

    return collapsed


# ---------------------------------------------------------------------------
# git/diff rewriter
# ---------------------------------------------------------------------------


def rewrite_git_diff_lines(lines: list[str], *, no_omit: bool = False) -> dict:
    """Rewrite git diff output: keep hunk headers + first N changed lines per hunk."""
    from .types import create_compaction, NO_COMPACTION

    result: list[str] = []
    emitted_in_hunk = 0
    omitted_added = 0
    omitted_removed = 0
    compaction = NO_COMPACTION

    def _flush_omitted():
        nonlocal omitted_added, omitted_removed
        if omitted_added > 0 or omitted_removed > 0:
            result.append(f"... hunk clipped: {omitted_added} added, {omitted_removed} removed lines omitted")
            omitted_added = 0
            omitted_removed = 0

    def _handele_changed_line(line: str):
        nonlocal emitted_in_hunk, omitted_added, omitted_removed, compaction
        if emitted_in_hunk < GIT_DIFF_CHANGED_LINES_PER_HUNK or no_omit:
                clipped = clip_middle_with_hash(line, LONG_CHANGED_LINE_MAX_CHARS, no_omit=no_omit)
                result.append(clipped["text"])
                emitted_in_hunk += 1
        else:
            omitted_added += 1
            if omitted_added == 1:
                compaction = create_compaction("git-diff-hunk-clip")

    for line in lines:
        # Hunk header or new file → flush and reset
        if line.startswith("@@ ") or line.startswith("diff --git"):
            _flush_omitted()
            emitted_in_hunk = 0
            result.append(line)
            continue

        # File headers
        if line.startswith("--- ") or line.startswith("+++ "):
            result.append(line)
            continue

        # Changed lines
        if line.startswith("+") and not line.startswith("+++"):
            _handele_changed_line(line)
            continue

        if line.startswith("-") and not line.startswith("---"):
            _handele_changed_line(line)
            continue

        # Context lines and other lines pass through
        result.append(line)

    _flush_omitted()
    return {"lines": result, "compaction": compaction}


# ---------------------------------------------------------------------------
# search/rg rewriter
# ---------------------------------------------------------------------------

_SEARCH_LINE_RE = re.compile(r"^(.+?:\d+(?::|-))(.*)$")


def rewrite_search_lines(lines: list[str], *, no_omit: bool = False) -> dict:
    """Clip long search match lines to LONG_SEARCH_LINE_MAX_CHARS."""
    from .types import create_compaction, NO_COMPACTION

    result: list[str] = []
    compaction = NO_COMPACTION

    for line in lines:
        m = _SEARCH_LINE_RE.match(line)
        if m:
            prefix = m.group(1)
            content = m.group(2)
            clipped = clip_middle_with_hash(content, LONG_SEARCH_LINE_MAX_CHARS, no_omit=no_omit)
            if clipped.get("compaction") and clipped["compaction"].kinds:
                compaction = clipped["compaction"]
            result.append(f"{prefix}{clipped['text']}")
        else:
            clipped = clip_middle_with_hash(line, LONG_SEARCH_LINE_MAX_CHARS, no_omit=no_omit)
            if clipped.get("compaction") and clipped["compaction"].kinds:
                compaction = clipped["compaction"]
            result.append(clipped["text"])

    return {"lines": result, "compaction": compaction}


# ---------------------------------------------------------------------------
# cloud/gh rewriter (simplified)
# ---------------------------------------------------------------------------


def rewrite_gh_lines(lines: list[str], input_: "ToolExecutionInput", *, no_omit: bool = False) -> dict:
    """Rewrite GitHub CLI output. Simplified port of the TS version."""
    from .types import NO_COMPACTION

    # Try whole-text JSON parse
    joined = "\n".join(line for line in lines if line.strip())
    try:
        parsed = json.loads(joined)
        if isinstance(parsed, (dict, list)):
            return {"lines": _format_gh_json(parsed), "compaction": NO_COMPACTION}
    except json.JSONDecodeError:
        pass

    # Try line-by-line JSON
    records: list[dict] = []
    all_json = True
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict):
                records.append(obj)
            else:
                all_json = False
                break
        except json.JSONDecodeError:
            all_json = False
            break

    if all_json and records:
        return {"lines": [_format_gh_record(r) for r in records], "compaction": NO_COMPACTION}

    # Fallback: pass through
    return {"lines": lines, "compaction": NO_COMPACTION}


def _format_gh_json(data: list | dict) -> list[str]:
    if isinstance(data, list):
        return [_format_gh_record(item) if isinstance(item, dict) else str(item) for item in data]
    return [_format_gh_record(data)]


def _format_gh_record(record: dict) -> str:
    # Comment records
    if "body" in record or "bodyText" in record:
        author = record.get("author", {}).get("login", "?")
        body = (record.get("bodyText") or record.get("body") or "")[:180]
        return f"comment @{author}: {body}"

    # Issue/PR/run records
    number = record.get("number", "")
    title = (
        record.get("title")
        or record.get("displayTitle")
        or record.get("name")
        or record.get("workflowName")
        or ""
    )
    state = record.get("state") or record.get("conclusion") or record.get("status") or ""
    branch = record.get("headRefName") or record.get("branch") or ""

    parts = []
    if number:
        parts.append(f"#{number}")
    if title:
        parts.append(title)
    if state:
        parts.append(f"[{state}]")
    if branch:
        parts.append(f"({branch})")

    return " ".join(parts) if parts else json.dumps(record, ensure_ascii=False)[:200]
